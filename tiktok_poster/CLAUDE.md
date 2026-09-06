# CLAUDE.md — tiktok_poster

Serviço de agendamento e publicação de vídeos via Buffer. Recebe `POST /schedule` do orchestrador com o vídeo pronto, calcula o próximo slot disponível, monta a caption e agenda no Buffer — **no TikTok e, quando o canal está conectado, também no YouTube**.

⚠️ **O nome do serviço é histórico.** Ele nasceu com um destino só; hoje publica em dois. Renomear tocaria o compose, o repo individual, as URLs de serviço e o deploy da máquina de produção, então o nome ficou. Nada aqui é específico do TikTok além do `BUFFER_PROFILE_ID` ser o canal dele.

## Arquitetura

- `api/routes/schedule.py` — endpoint principal; `_schedule_youtube()` é o segundo destino
- `api/routes/metrics.py` — `POST /metrics/sync` (coleta métricas do Buffer) e `GET /analytics/variants` (teste A/B por variante)
- `api/routes/health.py` — `GET /health`, verifica conexão com Buffer
- `api/routes/accounts.py` — `POST /accounts`, `GET /accounts` (Fase 1 do multi-account)
- `buffer/client.py` — `BufferClient(channel_id=None, access_token=None, org_id=None)`: `get_pending_posts()`, `create_post()`, `get_post_metrics()`, `verify_connection()`; exceções `BufferRejected`/`BufferRateLimited`
- `buffer/scheduler.py` — `next_available_slot()`: calcula próximo horário livre respeitando limite de fila; `continuation_slot()`; `timing_bucket()`: rotula um horário agendado pelo slot da grade mais próximo
- `storage/client.py` — `generate_presigned_url()`: gera URL pré-assinada do MinIO/R2 para o Buffer baixar o vídeo
- `hashtags/selector.py` — `select_hashtags()` + `compose_caption()`
- `youtube/metadata.py` — `youtube_category_id()`, `compose_title()`, `build_metadata()`; puro
- `db/engine.py` / `db/models.py` — engine async + `AccountCredentials`, `Publication`, `PostMetric`
- `schemas/schedule.py` — `ScheduleRequest`, `ScheduleResponse`
- `hashtags.json` — pool configurável de hashtags + obrigatórias

## Features

### Endpoint de agendamento (`src/tiktok_poster/api/routes/schedule.py`)

`POST /schedule` — agenda um vídeo no Buffer para publicação no TikTok e no YouTube.

**Request** (`ScheduleRequest`):
- `video_key` (str) — MinIO/R2 key do vídeo renderizado
- `classification` (dict) — objeto de classificação do orchestrador (com `hashtag_hints`, `cta_per_part`, `binary_cta`, `content_type`). ⚠️ `cta_per_part` **não vai mais para a legenda** — ver "A legenda não repete a pergunta narrada". `binary_cta`, quando presente, vai — ver "CTA de votação binária na legenda"
- `part_number` (int) — número da parte (1, 2, ...)
- `series_id` (str) — UUID do pipeline run (usado para log e rastreamento)
- `total_parts` (int, default 1) — quantas partes a história tem ao todo
- `follows_at` (datetime, opcional) — horário já agendado da parte anterior; presente só em partes 2+
- `youtube_title` (str, opcional) — título do vídeo no YouTube, vindo do refino
- `template_id` (UUID, opcional) — template VSEL/Blender usado no render desta parte, vindo do orchestrador; guardado em `Publication.template_id` para o teste A/B por variante — ver "Persistência de publicações" abaixo
- `tts_voice` (str, opcional) — voz do narrador usada no TTS desta parte (`tts_service`'s `GenerateResponse.voice`, repassada pelo orchestrador); guardado em `Publication.tts_voice`

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

**O YouTube tem outras obrigatórias** (`[hashtags] youtube_mandatory`, padrão `#historiasreais,#reddit`): `#tiktokbrasil` e `#fyp` não significam nada lá — hashtag de YouTube é busca, não distribuição. `#shorts` **não** está na lista — mas a razão caducou: ela era que a política de divisão permitia vídeo de até 30 minutos contra os 3 do teto de Short. ⚠️ Com o formato de 10–40s, **todo vídeo é Short de fato**. Entrar é edição de `config.ini`, sem código; está pendente de decisão.

### A legenda não repete a pergunta narrada (31/08/2026)

`compose_caption(cta, hashtags, part_number, total_parts, binary_cta="")` **não escreve o
`cta`**. `cta` é a pergunta que fecha a *narração* ("devo me separar?", vinda de
`classification.cta_per_part`) — ela nunca vai para a legenda.

**Por quê.** Com o formato curto, essa pergunta passou a ser a última frase narrada no vídeo —
ver `llm_service/CLAUDE.md`. Imprimi-la também no post faz o espectador **ler agora o que vai
ouvir em 30 segundos**: entrega o desfecho antes da história, no único texto que o TikTok
mostra antes do play.

⚠️ **O parâmetro `cta` continua na assinatura, de propósito.** `_schedule_youtube` ainda o usa
como fallback de `title` quando um orchestrador antigo não manda `youtube_title`
(`schedule.py:129`) — e sem título o Buffer recusa a criação do post inteiro. Tirar o
argumento mudaria a chamada nos dois destinos para não mudar nada no resultado.

O rótulo de parte virou linha própria (antes era colado no fim do CTA). ⚠️ Na prática ele
**não dispara**: `total_parts` é sempre 1 desde que a divisão saiu do refino.

### CTA de votação binária na legenda (`binary_cta`, 02/09/2026)

`compose_caption` ganhou um quinto parâmetro, `binary_cta`, que é texto **diferente** de
`cta`: vem de `classification.binary_cta` (novo campo do `llm_service`, ver
`llm_service/CLAUDE.md`), uma pergunta de escolha entre duas opções sobre o *dilema* da
história — nunca sobre o desfecho —, pensada para quem só lê a legenda antes de assistir.
Quando presente, abre a legenda, antes do rótulo de parte e das hashtags: é o trecho que
precisa caber antes do corte de "...mais" do TikTok.

`schedule.py` lê `body.classification.get("binary_cta", "")` e passa para os dois destinos
(TikTok e YouTube) — mesmo padrão de `cta_list`/`hints`, já lidos do mesmo dict opaco.
`""` (padrão quando o campo está ausente, incl. em requests de um orchestrador antigo) faz
`compose_caption` se comportar exatamente como antes deste campo existir.

⚠️ **Não é o retorno da regra de 31/08.** `binary_cta` nunca é o texto narrado — é um campo
próprio, com sua própria regra no prompt do `llm_service` (nunca entregar o desfecho). Ver
`docs/comentarios.md` (Frente 1) para a proposta original e as tensões consideradas antes de
implementar.

### Banco de credenciais por conta (`account_credentials`) — Fase 1 do multi-account

O serviço ganhou banco próprio (`db/engine.py`, `db/models.py`, `alembic/`) — não tinha
nenhum até 06/09/2026. Guarda uma tabela só, `account_credentials`: `account_id` (mesmo UUID
da linha `accounts` do orchestrador, sem FK — bancos diferentes), `slug`, `buffer_token_enc`
(Fernet), `buffer_org_id`, `tiktok_channel_id`, `youtube_channel_id`. Ver
`docs/multi_account.md` e `docs/vision.md` → "Contas de publicação — Fase 1 do
multi-account".

**Cadastro é endpoint, não `.env`.** `POST /accounts` (`api/routes/accounts.py`, upsert por
`account_id`) cifra o token na hora com `accounts/crypto.py::encrypt_token` (Fernet, chave em
`ACCOUNT_CREDENTIALS_KEY`) — o token cru nunca é persistido nem devolvido em nenhuma resposta.
`GET /accounts` lista as contas cadastradas sem decifrar nada. Trocar um token expirado é o
mesmo `POST`, sem endpoint de edição separado.

**`BufferClient` ganhou `access_token`/`org_id` opcionais** (`buffer/client.py`), ao lado do
`channel_id` que já existia. Omitidos, caem no `settings.env` de sempre — toda chamada
existente continua idêntica. `ScheduleRequest.account_id` (opcional) é o gatilho:
`_resolve_account` (`api/routes/schedule.py`) busca a credencial, decifra o token e monta o
`BufferClient` com ela, para o TikTok e (se a conta tiver canal) para o `_schedule_youtube`
também.

⚠️ **`account_id` desconhecido ou inválido nunca derruba o `/schedule`.** O vídeo já está
renderizado quando o request chega — cai na conta default com um `log.warning`, mesmo
espírito das degradações silenciosas do orchestrador (ver `orchestrator/CLAUDE.md`), só que
aqui é falha de operador (id errado), não estado normal esperado.

⚠️ **`/health` é a exceção — aqui uma conta desconhecida É erro.** Sem vídeo em jogo, cair
silenciosamente na conta default enganaria quem está diagnosticando justamente a conta que
pediu. `account_id` presente e não encontrado devolve `404`.

⚠️ **Requer a migration `001` deste serviço aplicada** — primeira vez que o `tiktok_poster`
tem migration; o `Dockerfile` passou a rodar `alembic upgrade head && uvicorn ...` no `CMD`,
mesmo padrão do orchestrator e do content_scout.

- Testes: `tests/test_account_credentials.py` — cifra/decifra puro, o token nunca aparecendo
  em nenhuma resposta (cifrado ou não), upsert por `account_id`, `/schedule` usando a conta
  certa quando `account_id` é conhecido, caindo no default quando não é, e `/health` com
  conta desconhecida devolvendo `404`. Os que tocam o banco exigem `DATABASE_URL` para um
  Postgres de verdade — mesma convenção do orchestrator/content_scout.

  ⚠️ **Desde a persistência de `Publication` (06/09/2026, ver abaixo), isso deixou de ser
  "os demais testes continuam sem depender de banco".** `POST /schedule` grava uma linha em
  todo caminho de sucesso, então qualquer teste que chega até um `create_post` bem-sucedido —
  a maior parte de `test_schedule.py`/`test_youtube.py`/`test_queue_backpressure.py` — agora
  também exige Postgres de verdade. Só o que nunca chama a rota (`test_hashtags.py`,
  `test_scheduler.py`, `test_posting_window.py`, e os testes de função pura dentro dos
  arquivos acima) continua livre de banco.

### Persistência de publicações e o teste A/B por variante (`db/models.py`, 06/09/2026)

O serviço passou a gravar **qual variante** cada parte publicada usou — hashtag, horário,
template de edição, voz do narrador —, base para comparar desempenho entre elas. Antes disso o
serviço era "agenda e esquece": nada registrava que hashtags saíram, em que slot, ou com qual
template/voz, e não havia como responder "a variante X performa melhor que a Y". Duas tabelas
novas (migration `002`, ao lado de `account_credentials` da Fase 1 do multi-account):

- **`Publication`** — uma parte publicada com sucesso, num canal: `series_id`/`part_number`
  (a parte do orchestrador), `channel` (`tiktok`/`youtube`), `buffer_post_id` (único — o ID do
  post no Buffer), `scheduled_at`, `hashtag_variant` (a seed usada em
  `select_hashtags(..., seed=...)`, ex. `tiktok:{series_id}:{part}` — guardada como string
  porque é a seed, não o resultado: duas publicações com a mesma seed tiveram a mesma cauda de
  hashtags, mesmo que o pool tenha mudado desde então), `timing_bucket` (ver abaixo),
  `template_id`/`tts_voice` (nullable — vêm prontos do `ScheduleRequest`, ver acima).
- **`PostMetric`** — um snapshot de uma métrica do Buffer (`metric_type`, `value`, `unit`,
  `metrics_updated_at`) para uma `Publication`, num instante (`fetched_at`). **Snapshot, não
  valor único**: `POST /metrics/sync` roda repetidamente e cada chamada grava uma linha nova,
  em vez de sobrescrever — permite ver evolução; a análise usa só a mais recente.

**Gravada só no caminho de sucesso.** `api/routes/schedule.py::_record_publication` é chamada
depois de `create_post` devolver um id de verdade — para o TikTok, na rota principal; para o
YouTube, dentro de `_schedule_youtube`, só depois do `if not update_id` que já existia. Um
`BufferRejected`/`BufferRateLimited` sai do fluxo antes de chegar lá, e o caminho de "sem id"
do YouTube também retorna antes: sem linha, sem publicação. Isso é o que torna `publications` a
fonte confiável para `/analytics/variants` — uma linha ali é uma publicação que de fato saiu.

`timing_bucket(scheduled_at, preferred_times, tolerance_minutes=15) -> str`
(`buffer/scheduler.py`) rotula o horário agendado em BRT pelo slot mais próximo de
`[posting] preferred_times` — `"11h"`/`"15h"`/`"19h"` na grade atual, ou `"other"` fora da
tolerância. **A tolerância (15 min) é menor que `series_gap_minutes` (30) de propósito**:
`next_available_slot` sempre devolve o horário exato da grade (sem jitter), então qualquer
tolerância menor que o intervalo de continuação de série já basta para não confundir "pendurou
30 min depois do slot" com "é o slot" — uma continuação sempre cai em `"other"`. Pura: recebe
`preferred_times` já lidos do config, para reusar a mesma lista que `next_available_slot` já lê
e não exigir `config.ini` nos testes.

⚠️ **Requer a migration `002` deste serviço aplicada** — mesmo `CMD` do Dockerfile
(`alembic upgrade head && uvicorn ...`) já cobre isto no boot.

- Testes: `tests/test_publications.py` — a linha gravada nos dois destinos, a seed batendo com
  a hashtag realmente escolhida, `template_id`/`tts_voice` chegando à linha, `timing_bucket`
  para cada horário da grade e para uma continuação de série, e nenhuma escrita quando o Buffer
  recusa ou a fila está cheia.

### Sincronização de métricas (`POST /metrics/sync`, `api/routes/metrics.py`)

Busca métricas no Buffer (views, likes, shares, ...) para publicações elegíveis e grava um
`PostMetric` por tipo devolvido. **Elegível** = `scheduled_at` há mais de 24h (o atraso
documentado pelo Buffer para computar métricas — ver
[developers.buffer.com/guides/post-metrics.html](https://developers.buffer.com/guides/post-metrics.html))
**e** sem `PostMetric` gravado nas últimas 20h (não re-sondar dentro da mesma janela do cron).

**Pensado para ser chamado 1x/dia por cron externo no servidor** — mesma decisão do cron de
disco pendente em `docs/deploy.md`. Nenhum loop novo dentro do processo.

`limit` (query param, default 50) protege a cota de 250 chamadas/dia do Buffer (ver "A cota da
API do Buffer" acima): este endpoint não pode competir com `/schedule` pela mesma cota.

**Uma falha (rede/HTTP) numa publicação não aborta o lote** — conta em `failed` e a varredura
segue para a próxima. `BufferClient.get_post_metrics(post_id)` devolve `None` quando o Buffer
ainda não tem métricas para aquele post (documentado como normal, não erro) — tratado como "sem
nada a gravar ainda", nunca como falha.

**Resposta**: `{synced, metrics_written, failed, skipped}` — `synced` conta publicações com
pelo menos uma métrica gravada nesta chamada; `skipped` conta as que eram velhas o bastante mas
já tinham `PostMetric` recente (fora do cooldown de re-sync, não é erro).

- Testes: `tests/test_metrics_sync.py` — publicação nova demais não é sincronizada, uma
  elegível e nunca sincronizada é sincronizada, uma já sincronizada há menos de 20h é pulada, um
  erro numa publicação não impede as demais do lote, e `get_post_metrics` devolvendo `None` não
  quebra nada.

### Análise por variante (`GET /analytics/variants`, `api/routes/metrics.py`)

Compara o desempenho médio das publicações agrupadas por uma dimensão: `dimension`
(`hashtag_variant`/`timing_bucket`/`template_id`/`tts_voice`, obrigatório), `metric_type`
(default `views`), `channel` (opcional, `tiktok`/`youtube`).

**Só entram publicações com pelo menos uma `PostMetric` do `metric_type` pedido.** Ausência de
métrica não é a mesma coisa que métrica zero — contar como zero enviesaria a média para baixo
exatamente nas variantes menos sincronizadas (as mais recentes), não nas piores. O snapshot usado
por publicação é sempre o **mais recente** (`ROW_NUMBER() OVER (PARTITION BY publication_id
ORDER BY fetched_at DESC)`), já que `PostMetric` guarda um histórico, não um valor só.

**Descritivo, sem teste de significância estatística** — decisão já tomada (conta única,
comparação por média/contagem). Resposta: lista de `{variant, sample_size, avg, min, max, sum}`,
ordenada por `sample_size` decrescente. `variant` é `"none"` quando a coluna era nula
(`template_id`/`tts_voice` em publicações de antes desses campos existirem).

`dimension` inválido, ausente, ou `metric_type`/`channel` fora do esperado devolvem `422` — só
validação de schema (`Literal`/`Query(min_length=1)`), sem lógica própria.

- Testes: `tests/test_analytics.py` — agregação por cada dimensão, filtro por canal, uma
  publicação sem a métrica pedida excluída da agregação (não contada como zero), só o snapshot
  mais recente contando quando há vários ao longo do tempo, valor nulo virando `"none"`, e
  `dimension`/`metric_type`/`channel` inválidos devolvendo `422`.

### Rampa de publicação — aquecimento de conta (`buffer/scheduler.py`, 06/09/2026)

Canal novo ou parado publicando no ritmo cheio desde o primeiro dia é o padrão que queima
conta (ver `docs/aquecimento.md`). A rampa reduz o teto de posts/dia em degraus crescentes,
por canal — a **Parte 1** (software) da proposta; a Parte 2 (rotina manual de uso real,
perfil completo, sem automação de engajamento) continua sendo trabalho humano, documentado
lá, e não pede código.

**Onde a data de início mora**, e por quê os dois lugares:
- **Conta extra** (Fase 1 do multi-account): `account_credentials.tiktok_warmup_started_on`
  / `.youtube_warmup_started_on` (migration `003`, `Date` nullable) — cadastradas pelo mesmo
  `POST /accounts` de sempre.
- **Conta default** (env vars, sem `account_id`): `config.ini [warmup] tiktok_started_on`
  / `youtube_started_on` — ela nunca ganhou linha em `account_credentials` na Fase 1, e não
  é o caso que está mudando aqui. `[warmup] steps` (`"1x7,2x7,3"`, degrau final sem `xN` =
  regime permanente) é compartilhado entre contas e canais — é política, não dado por conta.
  Sem medição própria por trás; editável sem deploy.
- `_warmup_started_on(account, platform)` / `_warmup_steps()` (`api/routes/schedule.py`)
  resolvem qual dos dois vale.

**`next_available_slot` ganhou suporte a teto por dia, não só fixo.** A varredura já anda
dia a dia internamente; um `int` calculado uma vez na rota acertaria só o primeiro dia
examinado e erraria ao cruzar um degrau da rampa no meio da varredura. Agora
`posts_per_day` aceita `int` **ou** `Callable[[date], int]` — `warmup_cap_for_day(started_on,
steps, day, default_cap)` é essa função quando a conta está em rampa.
**Retrocompatível**: todo chamador com `int` (a suíte de testes inteira, antes desta
mudança) sempre tinha `cap == len(preferred_times)`, então a rotação abaixo nunca era
acionada e o resultado não muda.

**O horário sob teto reduzido roda por dia, não fixa no primeiro.** Com teto 1 e três
horários possíveis, `_slots_for_day` escolhe um subconjunto rotacionado por
`check_day.toordinal()` — senão a conta publicaria sempre no mesmo horário todo dia de
rampa, a mesma assinatura de conta automatizada que a rotação de hashtags já corrigiu
(28/08/2026).

**Unidade do teto = história, não post.** Uma continuação de série (`follows_at` presente)
nunca é limitada pela rampa — mesma exceção que `continuation_slot` já faz para
`preferred_times`/`posts_per_day`. Por isso o teto só entra no ramo de `next_available_slot`
(exclusivo de parte 1) e, no YouTube, só quando `body.follows_at is None`.

**O YouTube tem teto próprio, contado contra a fila própria dele.** TikTok e YouTube da
mesma conta podem estar em fases de rampa diferentes — `_schedule_youtube` conta quantos
posts o **canal do YouTube** já tem agendados no dia do `slot` (via o `BufferClient`
específico dele, que a função já constrói) e pula, sem falhar, quando o teto do dia já foi
atingido: `youtube_enabled=True`, `youtube_error="fora do teto de aquecimento do dia"` —
mesmo padrão de toda degradação do YouTube já documentada acima.

⚠️ **Correção incluída, motivada por isto.** `upsert_credentials` sobrescrevia
`buffer_org_id`/`youtube_channel_id` com `None` sempre que `POST /accounts` os omitia — um
bug latente que passaria a apagar a rampa em andamento também, já que trocar só o token é o
caso de uso documentado do endpoint. Agora a rota manda só os campos que vieram de verdade
no corpo (`body.model_fields_set`), e `upsert_credentials` só sobrescreve o que foi
explicitamente enviado — `null` explícito ainda apaga, omissão não.

⚠️ **Aviso de canal trocado sem data de rampa — só para contas extras.** `POST /accounts`
já tem a linha antiga e a nova à mão no upsert: se `tiktok_channel_id`/`youtube_channel_id`
mudou e não veio `warmup_started_on` nem já havia um, loga `warning`. Para a conta default
isso exigiria persistir "qual canal era antes" em algum estado novo — o `config.ini` não
guarda histórico —, e ficou deliberadamente de fora (`docs/aquecimento.md` deixa essa parte
em aberto).

⚠️ Requer a migration `003` deste serviço aplicada.

- Testes: `tests/test_scheduler.py` (`parse_warmup_steps`, `warmup_cap_for_day` — degraus,
  regime permanente, fallback sem permanente e com `steps` vazio, `elapsed` negativo — e
  `next_available_slot`/`_slots_for_day` com `Callable`, incluindo a retrocompatibilidade
  com `int`) e `tests/test_account_credentials.py` (datas persistindo por conta, upsert não
  apagando campo omitido vs. `null` explícito apagando de propósito, `next_available_slot`
  recebendo `Callable` só quando a conta está em rampa, e o YouTube pulando — ou não, numa
  continuação — quando o teto do dia já foi atingido).

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

85 testes em 6 arquivos: `test_queue_backpressure.py` (9 — a recusa do Buffer que é teto vira pausa, a que não é continua erro, a recontagem que falha não mascara a recusa, e a cota estourada virando espera na contagem e na criação), `test_hashtags.py` (19 — inclui a legenda sem a pergunta narrada, o CTA de votação binária abrindo a legenda antes do rótulo de parte, a cauda que varia entre posts, a reprodutibilidade no reagendamento e as seeds dos dois destinos divergindo), `test_scheduler.py` (inclui 6 do `continuation_slot`), `test_schedule.py` (inclui o encadeamento fim-a-fim, o rótulo de parte e o `binary_cta` chegando até a legenda) e `test_youtube.py` (26 — mapa de categorias, título com rótulo protegido, metadata, os dois destinos no mesmo slot, a mutation com e sem `metadata`, e cada caminho de degradação). Buffer e MinIO são sempre mockados.

⚠️ O destino do YouTube é ligado nos testes pelo fixture `youtube_on`, que troca `_youtube_channel_id`. A função existe para ser essa costura: `settings.env` é um modelo congelado e não aceita `monkeypatch.setattr`.

Três arquivos novos para a persistência/teste A/B (06/09/2026): `test_publications.py` (10 —
`Publication` gravada nos dois destinos, a seed batendo com a hashtag real, `template_id`/
`tts_voice` chegando à linha, `timing_bucket` puro para cada slot da grade e para continuação
de série, e nenhuma escrita quando o Buffer recusa ou a fila está cheia), `test_metrics_sync.py`
(6 — elegibilidade por idade e cooldown, erro numa publicação não aborta o lote, `None` do
Buffer tratado sem erro, `limit` respeitado) e `test_analytics.py` (11 — agregação por
dimensão, filtro por canal, métrica ausente excluída da agregação, só o snapshot mais recente
contando, valor nulo virando `"none"`, validação de query params). Todos usam o fixture
`session` de `conftest.py` para semear/ler `Publication`/`PostMetric` direto no banco — exigem
`DATABASE_URL` para um Postgres de verdade, mesma convenção do `test_account_credentials.py`.

```bash
poetry run pytest
```
