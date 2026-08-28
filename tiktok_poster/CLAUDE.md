# CLAUDE.md — tiktok_poster

Serviço de agendamento e publicação de vídeos via Buffer. Recebe `POST /schedule` do orchestrador com o vídeo pronto, calcula o próximo slot disponível, monta a caption e agenda no Buffer — **no TikTok e, quando o canal está conectado, também no YouTube**.

⚠️ **O nome do serviço é histórico.** Ele nasceu com um destino só; hoje publica em dois. Renomear tocaria o compose, o repo individual, as URLs de serviço e o deploy da máquina de produção, então o nome ficou. Nada aqui é específico do TikTok além do `BUFFER_PROFILE_ID` ser o canal dele.

## Arquitetura

- `api/routes/schedule.py` — endpoint principal; `_schedule_youtube()` é o segundo destino
- `api/routes/health.py` — `GET /health`, verifica conexão com Buffer
- `buffer/client.py` — `BufferClient(channel_id=None)`: `get_pending_posts()`, `create_post()`, `verify_connection()`; exceção `BufferRejected`
- `buffer/scheduler.py` — `next_available_slot()`: calcula próximo horário livre respeitando limite de fila
- `storage/client.py` — `generate_presigned_url()`: gera URL pré-assinada do MinIO/R2 para o Buffer baixar o vídeo
- `hashtags/selector.py` — `select_hashtags()` + `compose_caption()`
- `youtube/metadata.py` — `youtube_category_id()`, `compose_title()`, `build_metadata()`; puro
- `schemas/schedule.py` — `ScheduleRequest`, `ScheduleResponse`
- `hashtags.json` — pool configurável de hashtags + obrigatórias

## Features

### Endpoint de agendamento (`src/tiktok_poster/api/routes/schedule.py`)

`POST /schedule` — agenda um vídeo no Buffer para publicação no TikTok e no YouTube.

**Request** (`ScheduleRequest`):
- `video_key` (str) — MinIO/R2 key do vídeo renderizado
- `classification` (dict) — objeto de classificação do orchestrador (com `hashtag_hints`, `cta_per_part`, `content_type`)
- `part_number` (int) — número da parte (1, 2, ...)
- `series_id` (str) — UUID do pipeline run (usado para log e rastreamento)
- `total_parts` (int, default 1) — quantas partes a história tem ao todo
- `follows_at` (datetime, opcional) — horário já agendado da parte anterior; presente só em partes 2+
- `youtube_title` (str, opcional) — título do vídeo no YouTube, vindo do refino

**Response** (`ScheduleResponse`):
- `scheduled_at` (datetime) — horário UTC agendado no Buffer, o mesmo nos dois canais
- `buffer_update_id` (str) — ID do post no TikTok (nome histórico)
- `youtube_update_id` (str | None) — ID do post no YouTube
- `youtube_error` (str | None) — por que não saiu no YouTube
- `youtube_enabled` (bool) — se o destino estava ligado

Erros: `429` com `{ error: "buffer_queue_full", pending_count, rejected_by_buffer }` se a fila do Buffer atingir o limite configurado.

⚠️ **O `429` cobre dois caminhos, e o segundo custou dois runs.** O primeiro é a pré-checagem: `next_available_slot` não acha vaga e nada é tentado. O segundo é o teto que **só aparece na recusa** do `createPost` — a contagem local filtra `status: [scheduled]` de um canal, e o teto do Buffer não é obrigado a contar do mesmo jeito. Esse caso levantava `RuntimeError` → 500, e um 500 faz o orchestrador marcar o run `failed` **para sempre**, fora do alcance da varredura de retry, que só olha `scheduling`. Aconteceu com dois runs em 15/08/2026, com o vídeo já renderizado.

Agora `create_post` levanta `BufferRejected` (tipo próprio, filho de `RuntimeError`) e a rota decide: vira `429` se a mensagem citar teto (`_looks_like_queue_limit`) **ou** se uma segunda contagem, feita no caminho de erro, mostrar a fila no limite. A mensagem crua sobe em `rejected_by_buffer` — é o que distingue os dois caminhos no log do orchestrador.

### A cota da API do Buffer (`BufferRateLimited`)

⚠️ **O plano tem 250 chamadas por dia** (e 100 a cada 15 min). Os headers da resposta dizem o estado: `ratelimit: "250-in-1day"; r=0; t=30837` e `retry-after`. Estourado, **toda** chamada volta `429` até a janela virar — no caso do teto diário, horas depois.

`_graphql` traduz esse `429` em `BufferRateLimited` **antes** do `raise_for_status`, e a rota o devolve como `429 { error: "buffer_rate_limited", retry_after }`. Sem isso ele subia como `HTTPStatusError` → 500 → run `failed` para sempre. É a explicação mais provável dos dois runs perdidos em 15/08/2026: a cota do dia tinha zerado.

**Quanto custa cada coisa**, para dimensionar contra as 250:

| Operação | Chamadas |
|---|---|
| `POST /schedule` (com YouTube) | 3 — fila + post no TikTok + post no YouTube |
| `POST /schedule` **sem** `BUFFER_ORG_ID` | 4 — o `_get_org_id` cobra uma a mais **por request** |
| `GET /health` | 1 — `verify_connection` fala com a API a cada chamada |
| Varredura de retry do orchestrador, por run parado | 1–2 a cada 15 min = **96–192 por dia** |

⚠️ **A última linha é a que morde**: um único run esperando vaga consome quase a cota diária inteira só tentando de novo. `BUFFER_ORG_ID` preenchido corta uma chamada de cada request, e é a economia mais barata que existe aqui.

**Recusa que não é teto continua sendo 500, de propósito.** Traduzir *toda* recusa em backpressure trocaria um erro visível por um loop silencioso: um run em `scheduling` é reoferecido a cada 15 minutos para sempre. A heurística erra para o lado da espera, que custa tempo, e não para o lado da perda, que custa o vídeo.

### Scheduler de slots (`src/tiktok_poster/buffer/scheduler.py`)

`next_available_slot(pending_posts, posts_per_day, preferred_times, queue_limit) -> datetime | None`

- Escaneia dias à frente até encontrar um com menos de `posts_per_day` posts
- Horários preferidos configurados em `config.ini [posting] preferred_times`, em **UTC**: hoje `14:00,18:00,22:00`, que é **11:00, 15:00 e 19:00 em Brasília** (UTC-3)
- ⚠️ **A janela de publicação é 11h–20h no horário do Brasil**, e o arquivo está em UTC — mexer nos horários sem converter joga vídeo para a madrugada sem erro nenhum. `tests/test_posting_window.py` lê o `config.ini` e falha se algum slot sair da janela
- O último slot para em 19:00 BRT de propósito: a parte 2 de uma série pendura `series_gap_minutes` depois da parte 1 e ignora os `preferred_times`, então o fim do dia precisa dessa folga para a continuação não sair da janela
- Retorna `None` se a fila já tem `queue_limit` posts (Buffer free: 10)

### Continuação de série (`continuation_slot`)

`continuation_slot(follows_at, gap_minutes, pending_posts, queue_limit, now=None) -> datetime | None`

Usado quando o request traz `follows_at` — ou seja, em partes 2+. Devolve `max(follows_at + gap, now + gap)`, com `gap` vindo de `config.ini [posting] series_gap_minutes` (padrão: 30).

- **Ignora `preferred_times` e `posts_per_day` de propósito.** Eles pacejam histórias independentes; uma história dividida é uma história continuada, e submetê-la a esse ritmo jogava a parte 2 para o dia seguinte (comportamento anterior).
- **`queue_limit` continua valendo** — é o teto do Buffer, não escolha de ritmo. Estourar devolve o mesmo `429 buffer_queue_full`, e o orchestrador cai no caminho de backpressure de sempre.
- **O intervalo é espaçamento mínimo, não deslocamento fixo.** Parte anterior no passado (run retomado tarde) pediria ao Buffer um horário vencido; a história volta a andar um intervalo a partir de agora.
- `follows_at` naive é lido como UTC.

⚠️ **`classification["parts"]` não manda mais na caption.** A chave nunca foi preenchida pelo `llm_service` (`Classification` não tem esse campo), então `compose_caption` recebia `total_parts=1` sempre e o rótulo "(Parte 1/2)" **nunca apareceu**, nem em série dividida. O número agora vem de `total_parts` no request, que o orchestrador preenche com a contagem real de parts.

### Segundo destino: YouTube (`_schedule_youtube` + `src/tiktok_poster/youtube/metadata.py`)

O **mesmo vídeo**, no **mesmo slot**, pelo **mesmo Buffer** — só o canal muda. O slot sai da fila do TikTok e é reusado: ritmo de publicação é uma decisão só, e a série continua encadeada nos dois lugares porque o `follows_at` já resolveu isso antes.

**O que o YouTube exige e o TikTok não.** `title` e `categoryId` são **obrigatórios na criação** — sem eles o Buffer recusa a mutation inteira. Daí o campo novo no refino (`youtube_title`) e o mapa de categorias.

- `youtube_category_id(content_type, default)` — `comédia`→23, `educativo`→27, `entretenimento`→24; qualquer outro cai no default (22, People & Blogs). `drama` e `suspense` **não têm** categoria própria no YouTube e ficam melhor em People & Blogs que forçados em Entertainment.
- `compose_title(title, part_number, total_parts)` — acrescenta `(Parte n/N)` em série. **O corte protege o rótulo, não o título**: numa série "(Parte 2/3)" é o que não pode faltar, então é o título que encolhe para caber nos 100 chars.
- `build_metadata(...)` — o bloco `metadata.youtube` do `createPost`.

⚠️ **A falha no YouTube é degradável e nunca levanta.** Quando `_schedule_youtube` roda, o post do TikTok **já foi criado**; derrubar o request aqui faria o orchestrador tratar como falha um run cujo destino principal saiu — e o retry republicaria a parte no TikTok, que sairia duas vezes lá. Todo desfecho ruim vira string em `youtube_error`.

⚠️ **`youtube_enabled` separa "desligado" de "falhou".** Só o segundo é degradação. Sem essa distinção, toda parte de todo run dispararia aviso no WhatsApp enquanto o canal não estivesse conectado — e um alarme que toca sempre é um alarme que ninguém lê.

⚠️ **A cláusula `metadata` é montada só quando há metadata**, em vez de mandar `metadata: null`. O caminho do TikTok publica em produção hoje sem esse argumento, e servidor GraphQL não é obrigado a tratar `null` explícito como ausente. Coberto por teste.

**Desligar**: `BUFFER_YOUTUBE_CHANNEL_ID` vazio (o normal, e o estado de quem ainda não conectou o canal) ou `config.ini [youtube] enabled = false` (freio manual, sem apagar a credencial).

**Contrato verificado por introspecção** no schema real do Buffer (`YoutubePostMetadataInput`): `title`, `categoryId`, `privacy` (`private`/`public`/`unlisted`), `madeForKids`, `notifySubscribers`, `embeddable`, `license`, `isAiGenerated`.

`ai_disclosed` sai **`false`** no `config.ini` desde 26/08/2026. O default da função continua `true`; quem decide é a config, porque é uma declaração sobre o vídeo, não uma constante do código.

⚠️ **O rótulo do YouTube mira outra coisa.** "Conteúdo alterado ou sintético" cobre fazer pessoa real parecer dizer o que não disse, alterar registro de evento real, ou gerar cena realista falsa. Um narrador genérico lendo história de terceiros não faz nada disso — ninguém é levado a crer que uma pessoa específica está falando. Estávamos declarando por excesso de zelo. **Se entrar clonagem de voz de pessoa real, volta para `true`.**

⚠️ **Não foi isto que zerou as impressões.** A mudança saiu junto da investigação do canal sem alcance, mas o YouTube afirma que o rótulo não reduz recomendação, e não há evidência de que reduza. Tratar isto como a correção daquele problema é procurar no lugar errado.

### Hashtags (`src/tiktok_poster/hashtags/selector.py`)

`select_hashtags(hints, mandatory, pool, max_total, seed=None) -> list[str]`

- Prioridade: obrigatórias → hints do LLM → pool de fallback
- Deduplicação preservando ordem
- Total limitado por `config.ini [hashtags] max_total` (padrão: 8)
- Obrigatórias configuradas em `config.ini [hashtags] mandatory` — **vazio desde 28/08/2026**
- Pool em `hashtags.json` (versionado no repo, editável sem deploy)

⚠️ **A cauda da legenda não pode ser a mesma post após post.** Eram `#tiktokbrasil,#fyp` fixas em **todo** post, e o pool preenchia o resto sempre a partir do topo da lista — 47 vídeos terminaram com as mesmas tags na mesma ordem, que é assinatura de conta automatizada. Duas mudanças: `mandatory` vazio no `config.ini`, e o **pool reordenado por post** via `seed`.

**A ordem do pool é embaralhada, não sorteada** (`sorted` por `sha256(seed:tag)`): reagendar a mesma parte tem de devolver a mesma legenda, ou um retry publica texto diferente do que foi revisado — mesmo princípio da rotação de background no orchestrador. `mandatory` e `hints` **não** são reordenados: aquelas são escolha explícita, estes descrevem a história.

**Cada destino tem seed própria** (`tiktok:{series_id}:{part}` / `youtube:{series_id}:{part}`): o mesmo vídeo nos dois lugares não pode sair com a mesma cauda.

⚠️ `hashtags.json` ainda tem uma chave `"mandatory"` — **não é lida**. Só `"pool"` é. Quem define obrigatórias é o `config.ini`.

**O YouTube tem outras obrigatórias** (`[hashtags] youtube_mandatory`, padrão `#historiasreais,#reddit`): `#tiktokbrasil` e `#fyp` não significam nada lá — hashtag de YouTube é busca, não distribuição. `#shorts` **não** está na lista: a política de divisão permite vídeo de até 30 minutos e Short é só até 3, então marcar como Short um vídeo que não é engana o espectador sem mudar a distribuição.

### Storage e URL pré-assinada (`src/tiktok_poster/storage/client.py`)

`generate_presigned_url(bucket, key, ttl_seconds) -> str`

- Gera URL GET pré-assinada via boto3 (compatível com MinIO e Cloudflare R2)
- TTL configurado em `config.ini [posting] presigned_url_ttl` (padrão: 21600s = 6h)
- **Requer storage publicamente acessível em produção** — em dev (MinIO local), o Buffer não consegue baixar o vídeo. Use R2 em produção.

## Configuração

Ver `.env.example`. Variáveis críticas:
- `BUFFER_ACCESS_TOKEN` — token da API do Buffer
- `BUFFER_PROFILE_ID` — ID do perfil TikTok no Buffer
- `BUFFER_YOUTUBE_CHANNEL_ID` — **opcional**; vazio desliga o YouTube e o serviço segue publicando só no TikTok
- `MINIO_ENDPOINT` — em dev: `http://localhost:9000`; em prod: endpoint do R2

## Produção (Cloudflare R2)

```env
MINIO_ENDPOINT=https://ACCOUNT_ID.r2.cloudflarestorage.com
MINIO_ACCESS_KEY=R2_ACCESS_KEY_ID
MINIO_SECRET_KEY=R2_SECRET_ACCESS_KEY
```

O bucket deve ser o mesmo configurado nos demais serviços (`blender-jobs`).

## Testes

78 testes em 6 arquivos: `test_queue_backpressure.py` (9 — a recusa do Buffer que é teto vira pausa, a que não é continua erro, a recontagem que falha não mascara a recusa, e a cota estourada virando espera na contagem e na criação), `test_hashtags.py` (14 — inclui a cauda que varia entre posts, a reprodutibilidade no reagendamento e as seeds dos dois destinos divergindo), `test_scheduler.py` (inclui 6 do `continuation_slot`), `test_schedule.py` (inclui o encadeamento fim-a-fim e o rótulo de parte) e `test_youtube.py` (26 — mapa de categorias, título com rótulo protegido, metadata, os dois destinos no mesmo slot, a mutation com e sem `metadata`, e cada caminho de degradação). Buffer e MinIO são sempre mockados.

⚠️ O destino do YouTube é ligado nos testes pelo fixture `youtube_on`, que troca `_youtube_channel_id`. A função existe para ser essa costura: `settings.env` é um modelo congelado e não aceita `monkeypatch.setattr`.

```bash
poetry run pytest
```
