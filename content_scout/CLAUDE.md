# CLAUDE.md — content_scout

Descobre roteiros candidatos na internet e submete ao orchestrador. É o serviço de **trigger** previsto em `docs/vision.md` — normaliza qualquer fonte para `plain text + metadata` antes de chamar `POST /pipeline`.

## Arquitetura

**Bootstrap**: igual aos demais serviços — `from src.core import settings` em `app.py` dispara `bootstrap._init()` antes de qualquer outro import ler env vars.

**Módulos:**
- `api/routes/scout.py` — `POST /scout/run` (ciclo manual, síncrono), `GET /scout/seen` (auditoria)
- `api/routes/health.py` — `GET /health`: ping no DB + flag do loop
- `api/app.py` — inclui o `lifespan` que sobe o `scout_loop` quando `SCOUT_ENABLED=true`
- `db/models.py` — `SeenItem` + enum `SeenStatus` + `ItemComment` + `ArchiveCursor`
- `clients/llm.py` — `ModerationClient` (segurança) + `StoryQualityClient` (gancho/storytelling)
- `db/engine.py` — engine async + `AsyncSessionLocal` + `get_session()`
- `schemas/scout.py` — `ScoutRunResponse`, `SeenItemResponse`
- `sources/base.py` — `Candidate`, `Comment`, `CommentThread` (dataclasses) + `Source` e `CommentCapableSource` (Protocols)
- `sources/reddit.py` — leitura e parsing dos feeds Atom do Reddit (posts e comentários) + throttle compartilhado
- `filters.py` — `evaluate()` / `find_blocked_term()`, puros
- `clients/orchestrator.py` — `create_pipeline()`, `count_active_runs()`
- `scout.py` — `run_cycle()` + `scout_loop()`
- `inbox.py` — caixa de entrada de links pelo ntfy: `inbox_loop()`, `handle_message()`, `handle_url()`
- `clients/tts.py` — `TranscribeClient` (URL de vídeo → texto, via `tts_service POST /transcribe`)

## Comandos

```bash
poetry install
python main.py                    # requer .env e db up
poetry run pytest                 # requer DB content_scout
poetry run pytest -m no_db        # parser + filtros, sem docker
alembic upgrade head
```

⚠️ **As migrations rodam no boot pelo `CMD` do Dockerfile** (`alembic upgrade head && uvicorn ...`), então subir com `--build` basta; fora do Docker é manual. **Não era assim até 26/08/2026**: o `CMD` chamava o `uvicorn` direto, e o deploy da migration `005` subiu o código novo com o banco parado em `004` sem nenhum sinal — container `Up`, `/health` em `200` (ele pinga o DB, não a versão do schema), e a quebra só apareceria no primeiro ciclo, até uma hora depois. O `&&` é deliberado: migration que falha derruba o boot, porque um scout servindo com o schema errado é pior que um scout fora do ar — só o segundo é visível.

## Variáveis de ambiente

- `ROOT_DIR`, `ENV`, `DEBUG` — padrão dos serviços
- `DATABASE_URL` — asyncpg para o banco `content_scout`
- `SCOUT_ENABLED` — `true`/`false` (default `true`): liga o loop periódico
- `SCOUT_USER_AGENT` — User-Agent das requisições ao Reddit
- `NTFY_INBOX_URL` — tópico ntfy **de entrada**, onde chegam os links compartilhados do celular. Vazio (o padrão) desliga o laço. **Tem que ser outro tópico**, não o do `NOTIFY_WEBHOOK_URL`

Nenhuma chave de API é necessária aqui — o caminho RSS não autentica, e a moderação passa pelo `llm_service`, que detém as chaves.

Config em `config.ini`: `[services] orchestrator_url` + `llm_url`, `[reddit]`, `[filters]`, `[scout]`.

Chaves novas: `[reddit] comments_limit` (0 = default do Reddit), `[scout] fetch_comments` (liga/desliga o enriquecimento) e `[scout] max_comments_stored` (quantos corpos guardar por post).

Varredura histórica: `[reddit] archive_interval_hours` (0 = desligada), `archive_time_filter` (janela paginada, padrão `all`) e `archive_limit` (posts por página).

Qualidade narrativa: `[scout] story_quality` (liga/desliga), `min_story_score` (corte da tag `weak_storytelling`, padrão 6), `story_excerpt_chars` (quanto de cada corpo vai no lote, padrão 700) e `story_timeout`.

Revolta: `[scout] outrage_weight` (peso da revolta na ordenação, padrão 2) e `min_outrage_score` (piso da revolta — rótulo e contador, nunca portão; padrão 6).

Contas de publicação: `[scout] max_pending_runs_per_account` (teto de runs em andamento por conta, padrão 2) e `account_usage_window_hours` (janela do desempate do rodízio, padrão 24h) — ver "Escolha de conta" abaixo.

## Features

### Fonte Reddit (`src/content_scout/sources/reddit.py`)

Lê o feed Atom `/r/{sub}/top/.rss?t={janela}&limit={n}` de cada subreddit configurado.

**Por que RSS e não a API oficial:** o Reddit encerrou os endpoints `.json` sem autenticação em maio/2026. Os feeds RSS continuam abertos, não exigem credencial e não caem na cláusula de uso não-comercial da Data API. O custo é não haver score no feed — por isso pedimos `/top/` numa janela de tempo e deixamos a ordenação do próprio Reddit ser o sinal de qualidade, implícito na posição.

**API pública (pura, testável sem rede):**
- `clean_body(raw_content) -> str` — extrai o corpo entre `<!-- SC_OFF -->` e `<!-- SC_ON -->`, descarta o rodapé `submitted by /u/x [link] [comments]`, desescapa o HTML duplamente escapado, preserva quebras de parágrafo e remove nbsp/zero-width/bidi.
- `parse_feed(xml_text, subreddit) -> list[Candidate]` — descarta entradas sem corpo (link posts, image posts).
- `RedditSource.feed_url(subreddit) -> str`
- `RedditSource.fetch() -> list[Candidate]` — um subreddit que falha (privado, renomeado, rate-limited) é logado e pulado; não derruba o ciclo.

⚠️ Sem o tratamento do `SC_OFF/SC_ON`, o rodapé "submitted by /u/fulano [link] [comments]" entraria no roteiro e seria **narrado em voz alta** no vídeo final.

⚠️ **Rate limit.** Leitura não autenticada permite ~1 requisição por 40s: a resposta já traz `x-ratelimit-remaining: 0` e `x-ratelimit-reset: ~40` na primeira chamada. Sem espaçamento, **só o primeiro subreddit da lista responde 200** e todos os outros tomam 429 silenciosamente. Por isso `request_delay_seconds` (padrão 60) entre requisições — medido ao vivo, 45s ainda tomou 429 com `reset=12`, então a janela desliza. Cada subreddit extra custa uma janela por ciclo: mantenha na lista só subs que rendem.

### Enriquecimento com comentários (`src/content_scout/sources/reddit.py`)

**O que o feed de posts expõe** (medido, não suposto): `author` (nome + URL), `category`, `content`, `id`, `link`, `published`, `updated`. **Não** expõe score, número de comentários, thumbnail, flair, prêmios nem NSFW.

**API pública:**
- `parse_comments(xml_text) -> CommentThread` — conta as entradas `t1_` (a primeira entrada é o próprio post, `t3_`, e não conta). Resposta sem corpo (apagada/removida) **conta em `total` mas não vai para `comments`**: ela existiu no post.
- `RedditSource.comments_url(external_id) -> str` — usa a forma curta id36 (`/comments/{id36}/.rss`), não o permalink: o permalink embute slug acentuado que precisaria de escaping.
- `RedditSource.fetch_comments(candidate) -> CommentThread | None` — `None` é "não deu para consultar", distinto de thread vazia.

⚠️ **O rate limit é por cliente, não por endpoint.** Medido: feed de comentários logo após feed de listagem responde 429 com `x-ratelimit-used: 1` — a listagem já gastou a janela. Por isso o espaçamento mora num `_Throttle` compartilhado que **toda** requisição atravessa; se estivesse dentro de `fetch()`, cada endpoint novo teria que reinventá-lo.

⚠️ **O throttle é estado de processo, não de instância** — `shared_throttle(delay)`. `build_sources()` cria uma `RedditSource` nova a cada ciclo, então um throttle por instância espaçava requisições *dentro* de um ciclo e nada mais. `delay` é reconfigurado a cada construção (último a escrever vence), o que mantém a suíte em 0 em vez de herdar os 60s de produção. O fixture autouse `fresh_throttle` zera `_last` entre testes — sem ele cada teste herdaria o timer do anterior e esperaria de verdade (a suíte levava 4 minutos).

⚠️ **Custo: uma janela (~60s) por post enriquecido.** Enriquecer todos os candidatos custaria ~45 min/ciclo. Roda só nos que vão ser publicados — mesmo ponto da moderação —, ou seja ~2/ciclo. Desligável em `[scout] fetch_comments`.

⚠️ **`comment_count IS NULL` ≠ `0`.** Nulo = nunca consultado (todo filtrado, e toda falha de feed). Zero seria a afirmação de que o post não teve reação. Falha de enriquecimento nunca impede a publicação.

`total` é **piso, não censo** — apagados e colapsados não aparecem. Sinal de repercussão, não métrica exata. `?limit=500` devolveu os mesmos 121 que sem limite, então `comments_limit = 0` (default do Reddit) é o certo.

### Varredura histórica do arquivo (`sources/reddit.py` + `scout.py`)

Pagina o `top?t=all` de um subreddit por vez, para a fila não ficar limitada ao que aconteceu nesta semana.

**API pública:**
- `RedditSource.archive_url(subreddit, after=None) -> str`
- `RedditSource.fetch_archive(subreddit, after) -> tuple[list[Candidate], str | None]` — a página e o cursor da próxima. `None` no cursor = listagem esgotada.
- `ArchiveCapableSource` (Protocol em `sources/base.py`) — capacidade opcional, no mesmo molde de `CommentCapableSource`.
- Tabela `archive_cursors` — `origin` único, `after_id`, `pages_read`, `last_swept_at`.

**Por que paginar e não só pedir `t=all`.** `top?t=all` devolve *sempre os mesmos 15 posts*. Depois da primeira passada todos estão em `seen_items`, então repetir a requisição gasta uma janela de rate limit para não achar nada. Medido ao vivo (28/07/2026): `?count=15&after=t3_…` devolveu 15 posts com **overlap zero** com a página 1 — então dá para andar para trás no arquivo indefinidamente.

⚠️ **O cursor é o id da última `<entry>`, não do último `Candidate`.** `parse_feed` descarta link e image posts; paginar a partir do último candidato sobrevivente faria a varredura re-pedir a cauda descartada em toda passagem, e o cursor andaria a passo de tartaruga.

⚠️ **A cadência mora no banco (`last_swept_at`), não num contador de processo.** Um contador zeraria em todo deploy e dispararia varredura imediata. A varredura fica "devida" quando `last_swept_at` é mais velho que `archive_interval_hours`.

⚠️ **Uma varredura por ciclo, no máximo — entre todas as fontes.** O que está sendo protegido é a janela de rate limit compartilhada, que não liga para qual fonte a gastou. Subreddit nunca varrido tem prioridade sobre os já varridos, senão um sub recém-configurado esperaria o rodízio inteiro.

⚠️ **Esgotar é normal, não é erro.** Feed vazio → cursor volta a `None` e a varredura recomeça do topo (`archive_wrapped=True`). O que a nova volta relê já está em `seen_items`, então uma volta custa requisição mas **não pode republicar**.

⚠️ **Falha aqui não derruba o ciclo.** O arquivo é oferta extra em cima dos feeds ao vivo — mesma assimetria da nota de storytelling. O cursor também **não** avança quando a requisição falha: tratar 429 como esgotamento reiniciaria o sub do zero.

### Dedup por conteúdo — reposts (`filters.py` + `seen_items.content_fingerprint`)

`normalize_for_fingerprint(text)` / `content_fingerprint(text) -> str | None`, puros.

**Por que `external_id` não basta.** Ele pega o *mesmo post*. A varredura histórica alcança anos atrás e entra em subs que repostam uns aos outros (`r/story` ↔ `r/stories`), então a mesma história chega de verdade duas vezes, com dois ids. Sem isso vira vídeo duplicado.

Fingerprint = sha256 dos **1000 primeiros caracteres alfanuméricos** do corpo, minúsculo e sem acento. Descartar pontuação e caixa é o que faz um repost redigitado casar; cortar no começo é o que impede um bloco `EDIT:` no fim de derrubar a comparação. Acento sai porque a mesma história pode aparecer sem diacrítico.

⚠️ **Corpo que normaliza para vazio devolve `None`, não o hash de `""`.** Com o hash, todo candidato assim colidiria com todos os outros e o segundo seria descartado como repost do primeiro.

⚠️ **A coluna é indexada mas NÃO é única.** Um repost precisa ser gravado com a própria linha de auditoria dizendo que foi pulado (`skip_reason=duplicate_story`); uma constraint única rejeitaria exatamente essa linha.

⚠️ **`content_fingerprint IS NULL` em toda linha anterior à migration 004.** `seen_items` nunca guardou o corpo, só título/url/`char_count`, então o histórico não pode ser reprocessado. Detecção de repost cobre só o que foi visto daqui para frente.

O fingerprint é gravado **também nas linhas rejeitadas** — repost de história que foi filtrada por tamanho continua sendo repost. A checagem roda depois do filtro barato de tamanho e **antes** de moderação e nota: repost é a rejeição mais barata que existe e não pode custar chamada de modelo.

### Filtros determinísticos (`src/content_scout/filters.py`)

`evaluate(candidate, min_chars) -> str | None` — **só o piso**. Devolve `too_short:{n}` ou `None`. Roda na passagem barata, sobre todo candidato.

Segurança **não** mora aqui — ver abaixo.

⚠️ **Não há teto de tamanho — removido em 07/09/2026.** Existiu um `exceeds_length(candidate, max_chars)` que recusava posts acima de `max_chars = 30000`, derivado de `MAX_PART_WORDS` (o formato antigo, de até 30 min divididos em partes com cliffhanger). Esse formato não existe mais: desde a mudança de 31/08/2026, o refino sempre `RECONTA` qualquer roteiro — de qualquer tamanho — como **um** vídeo de 10 a 40s (`TARGET_MIN/MAX_WORDS` em `llm_service/prompts/refine.py`; `parts` tem sempre um elemento). Um post maior só significa mais texto para o refino condensar, nunca uma razão para descartá-lo antes — e o histórico media isso: os posts que passavam do teto antigo eram desproporcionalmente os de melhor nota (medição de 45 posts, 14/08/2026: nenhum candidato acima do teto tirava nota abaixo de 6). Ver `llm_service/CLAUDE.md` → "Formato: uma história completa em 10–40s".

Nada a jusante quebra com roteiro longo: `raw_script`/`script` são `Text` sem limite no banco, e o roteiro bruto inteiro (por maior que seja) vai como está no prompt de `/refine` — o custo de um post muito longo é só mais tokens na chamada, não uma falha.

### Moderação por LLM (`src/content_scout/clients/llm.py` → `llm_service POST /moderate`)

`ModerationClient.check(title, text) -> Verdict(safe, category, reason)`.

**Por que não é mais blocklist.** A versão anterior casava substring: `me matar` casava dentro de `"Eram 3 mil que não me mataria"` — figura de linguagem sobre dinheiro — e descartava uma história boa. Já `"disseram que depois de me matar iam fazer com ela..."` é ameaça real e precisa ser barrada. As duas contêm exatamente a mesma sequência de caracteres. Segurança aqui é julgamento de contexto, e isso exige um modelo.

**Custo.** A moderação roda **por publicação, não por post buscado** — só nos candidatos que já passaram tamanho, dedup e ordenação, e apenas até o orçamento do ciclo encher. Na prática, 2–3 chamadas por ciclo em vez de ~30. Usa `LLM_MODERATION_MODEL`, separado do modelo de refino.

⚠️ **"Não deu para checar" nunca vira veredito.** `ModerationError` é distinto de `safe=False`:
- `safe=False` → grava `SeenItem` com `unsafe:{categoria}`, candidato queimado para sempre.
- `ModerationError` → **não grava nada**, encerra o ciclo e o candidato continua disponível no próximo. Uma indisponibilidade do `llm_service` não pode nem publicar sem checagem, nem descartar história boa em definitivo.

O ciclo para na primeira falha de moderação em vez de tentar os demais: se o serviço caiu, todos falhariam igual.

### Qualidade narrativa: gancho + storytelling (`src/content_scout/clients/llm.py` → `llm_service POST /story-quality`)

`StoryQualityClient.score(candidates) -> dict[external_id, StoryScore]` — uma chamada em lote por ciclo, com título + abertura de **todos** os candidatos frescos.

`StoryScore(hook, score, hook_line, reason)` + `.tag(min_score)` + `.as_metadata(min_score)`.

**Por que existe.** O ranking do Reddit mede votos, não se a história se conta bem. A retenção no TikTok se decide nos 2 primeiros segundos, e isso depende da abertura. `hook` responde se o título ou as primeiras linhas prometem desfecho; `score` (0–10) responde se a história se sustenta. **As duas discordam nos dois sentidos** e cada discordância é um diagnóstico diferente — título ótimo sobre corpo que se perde, ou história boa com lide enterrado. Colapsar num número só apagaria a distinção.

**Por que em lote, e por que sobre todos.** Isto é sinal de **seleção**, não gate: pontuar só os 2–3 que vão ser publicados seria circular, porque é a nota que define quais são. Uma chamada por candidato seriam ~30/ciclo; em lote é **uma**, ~4k tokens, mais barato que a moderação. Só a abertura vai (`story_excerpt_chars`, padrão 700) — 30 posts inteiros seriam ~180k caracteres. O recorte também é o input honesto: é com essa quantidade de texto que o espectador decide.

⚠️ **Falha aqui NÃO derruba o ciclo — assimetria deliberada com a moderação.** A moderação decide se pode publicar, então "não deu para checar" para tudo. A nota decide só a ordem, então `score()` devolve **mapa vazio em vez de levantar exceção**: o ciclo cai de volta no ranking do Reddit e as colunas ficam `NULL`.

**Tags** (`SeenItem.story_tag`), derivadas da nota contra `min_story_score`:

| Tag | Condição |
|---|---|
| `weak_storytelling` | `score < min_story_score` |
| `no_hook` | nota ok, `hook = false` (lide enterrado) |
| `strong` | nota ok e `hook = true` |

`weak_storytelling` tem precedência sobre `no_hook`; `has_hook` guarda a resposta crua nos dois casos.

⚠️ **É etiqueta, não filtro.** Candidato marcado `weak_storytelling` **continua sendo publicado** se não houver nada melhor atrás. Virar corte rígido esvaziaria a fila em semana ruim — o custo de um vídeo mediano é menor que o de não publicar. A nota age na **ordem** (`rank_by_story`), a tag age como informação para o refino e para calibragem.

⚠️ **A tag é derivada, não pedida ao modelo.** O corte mora em config, então se move contra dados reais (`GET /scout/seen?story_tag=weak_storytelling`) — mesmo motivo de `min_chars`. `story_score` fica cru, então mover o corte permite re-derivar as linhas antigas. A derivação acontece na escrita: mover o corte só afeta linhas novas.

⚠️ **`story_tag IS NULL` ≠ fraco.** Nulo = não avaliado — o que foi barrado pelos filtros baratos (a nota roda **depois** deles e **depois** do backpressure: fila cheia não publica, então não paga julgamento) e todo candidato de ciclo em que o `llm_service` caiu.

`rank_by_story(candidates, scores, neutral, outrage_neutral, outrage_weight)` — ordena por `outrage_weight × outrage + story_score`, desc, **estável**: como `interleave_by_origin` preserva a ordem interna de cada grupo, um sort estável na lista plana vira "melhor primeiro dentro de cada origem" sem tocar na justiça entre origens. Empate mantém a posição do feed. **Candidato sem nota ordena nos próprios limiares**, não no fim — mandá-lo para o fim converteria falha de modelo em handicap permanente, e são as sobras de cada ciclo que herdariam isso.

Desligável em `[scout] story_quality`.

### Revolta e vilão (`outrage` / `villain`)

`StoryScore(hook, score, outrage, villain, hook_line, reason)` + `.rank_value(outrage_neutral, outrage_weight)`.

**Por que existe.** O canal vive de indignação: a história que rende comentário é a que tem alguém claramente errado — o namorado que traiu e quer voltar, a sogra que sabota, a amiga que conta o segredo. O `score` de storytelling não mede isso: um relato mal escrito pode ser revoltante e uma história muito bem contada pode não ter vilão nenhum. São eixos diferentes e o prompt manda respondê-los separado.

**A ordenação é `outrage_weight × outrage + story_score`** (`min_outrage_score` = piso e ponto neutro; `outrage_weight` = 2 por padrão).

⚠️ **Peso, não ordem de prioridade.** Ordenar só por revolta poria uma história 10 de revolta e 3 de narrativa na frente de uma 9/9 — e história que ninguém termina de assistir não rende o comentário pelo qual a revolta foi escolhida. Com peso 2, dois pontos de revolta valem mais que quatro de narrativa, e nada mais que isso. `outrage_weight = 0` reproduz exatamente a ordenação antiga, que é o teste que fixa o significado do botão.

⚠️ **`min_outrage_score` NÃO é portão** — mesma regra do `min_story_score`. Num ciclo sem nada revoltante, a melhor história disponível publica assim mesmo: dia sem vídeo é pior que vídeo mais calmo. O corte serve para (a) ordenar candidato não julgado no lugar neutro e (b) contar `low_outrage`, que é como se vigia a oferta.

⚠️ **`outrage IS NULL` ≠ 0.** Nulo = o modelo não respondeu o campo; zero = respondeu que não há com quem se indignar. Um veredito sem `outrage` **continua valendo** pelo `score` — a degradação é por campo, não por candidato — e ordena no ponto neutro. Toda linha anterior à migration 005 é nula pelo mesmo motivo.

**O que viaja para o refino** (`as_metadata`): `outrage_score` (omitido quando nulo) e `has_villain`. O refino usa isso no CTA da legenda — quando há vilão, o CTA pede o veredito do espectador sobre ele, que é o que faz alguém parar para escrever (ver `llm_service/CLAUDE.md`).

**Público-alvo no prompt.** O topo da escala (8–10) é reservado para a revolta que atinge o público principal do canal — **mulheres de 18 a 35** —, porque é ele que converte: traição, sogra invasiva, marido ausente, amiga falsa, homem que descarta e volta. Revolta que só funciona para outro público continua pontuando, só não chega ao topo.

**Colunas novas** (migration 005): `seen_items.outrage_score` (indexada) e `seen_items.has_villain`. Cruas ao lado de `story_score`, para o peso entre os dois eixos poder ser re-derivado contra o que já foi publicado.

✅ **Calibragem encerrada em 14/08/2026** — as quatro decisões de `docs/story_quality_calibration.md` estão fechadas. A decisão de corpus foi tomada antes (rota (a): trocar as fontes por subs de história-entretenimento), com o prompt de `story.py` ajustado junto — ele não desconta mais por "pergunta ao fórum" quando a pergunta *emoldura* a história, que é a forma de todo post do `EuSouOBabaca`. As três que faltavam: **`min_story_score` fica em 6** (nota 5 é fraco), **`story_excerpt_chars` fica em 700**, e o **teto 9–10 foi relaxado** — o prompt agora manda usar a escala inteira, porque nenhum post chegava lá e a régua era efetivamente 2–8.

⚠️ **A re-medição foi dispensada por decisão, não esquecida.** `docs/story_quality_baseline.json` é de um corpus que **não está mais configurado** (`desabafos`/`relacionamentos`), então os 40% `weak_storytelling` de lá não descrevem o que roda hoje — leia aquele arquivo como histórico. Se um dia `min_story_score` for mexido, refazer antes com `scripts/score_real_posts.py --subreddits EuSouOBabaca,story,stories`; enquanto o corte não se move, a régua atual vale.

### Ciclo do scout (`src/content_scout/scout.py`)

`run_cycle(sources=None) -> ScoutReport` — busca, deduplica, filtra e submete dentro do orçamento.

**Ordem das etapas importa:** a capacidade é checada **depois** de registrar os filtrados e **antes** de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o próximo ciclo começa de uma pilha menor.

**Backpressure** — consulta `GET /pipeline` no orchestrador e conta runs em estado ativo (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se `active_runs >= max_pending_runs`, nada é submetido. Ingerir mais rápido do que se publica só converte roteiro novo em run falho — mesmo raciocínio por trás do `[posting] buffer_queue_limit` apertado do `tiktok_poster` (hoje 1, não os 10 que o plano free do Buffer aceitaria): ver `tiktok_poster/docs/scheduling.md` → "Por que o teto é menor que o do Buffer".

**Orçamento por ciclo** — `min(capacidade_restante, max_per_cycle)`. A lista ordenada é percorrida **além** do orçamento, porque candidato rejeitado pela moderação não consome vaga — a próxima história assume.

**Seleção: `interleave_by_origin(rank_by_story(...))`** — rodízio entre origens, e dentro de cada origem a nota de qualidade narrativa (ver acima) como ranking interno, com a posição do feed como desempate. O rodízio decide *de quem* é a vez; a nota decide *qual* história daquela origem.

As fontes são concatenadas na ordem do config, então pegar o começo da lista dava todas as vagas ao primeiro subreddit. Medido ao vivo: as duas submissões vieram de `r/desabafos` enquanto `r/relacionamentos` contribuiu 5 candidatos e não ganhou nenhuma — configurar mais subreddits era decorativo.

Intercalar mantém o ranking do Reddit como sinal de qualidade (continua pegando o melhor *disponível* de cada) e garante que nenhuma comunidade monopolize. Junto com o dedup, o rodízio entre ciclos emerge sozinho, sem guardar estado de rotação.

**Dedup** — `SeenItem.external_id` é único e guarda **todo** candidato avaliado, inclusive os rejeitados, com o motivo. Sem isso o scout reposta a mesma história toda semana que ela reaparece no top, e re-avalia o mesmo lixo para sempre. A tabela também serve de trilha de auditoria para calibrar os filtros contra dados reais.

**Falha isolada** — um submit que estoura é gravado como `failed` e o ciclo continua nos demais.

### Escolha de conta (round-robin, 08/09/2026)

Até aqui, todo `POST /pipeline` automático saía sem `account_id` — a descoberta automática
publicava sempre na conta default, mesmo depois de uma segunda conta (`redditors-inc`) ter
sido cadastrada na Fase 1 do multi-account (06/09/2026). Resultado medido: dois dias em
produção sem um único run direcionado a ela. Este é o "próximo passo natural" que
`docs/multi_account.md` já previa.

**Dois sinais, dois problemas.** Elegibilidade (`active_by_account`, lido de
`GET /pipeline` a cada ciclo, igual a `count_active_runs()` mas agrupado por conta) diz
*quais* contas podem receber trabalho agora — uma conta no teto (`max_pending_runs_per_account`)
não recebe mais nada neste ciclo, mesmo com orçamento global sobrando.
Desempate (`pick_account`, `usage_by_account`) diz *qual* das elegíveis recebe a próxima
história — a menos usada recentemente, empate a favor da conta default.

⚠️ **O desempate usa uma janela de 24h (`_submitted_per_account`), não a vida inteira —
ao contrário de `_submitted_per_origin`.** Toda linha de `seen_items` anterior a esta
feature tem `account_id = NULL` (foi para a conta default, porque rodízio não existia
ainda) — são bem mais de 100 linhas `submitted`. Se o desempate contasse desde sempre,
uma conta nova (contagem 0) ganharia **todo** desempate por dezenas de ciclos até alcançar
a contagem da conta default, o que na prática pararia de publicar na conta principal por
dias. Isso nunca aconteceu com origem porque todas as origens nasceram em zero juntas —
aqui é uma dimensão nova sendo retrofitada sobre histórico assimétrico. A janela de 24h
resolve isso: o histórico pré-feature sai de vista em menos de um dia.

⚠️ **Elegibilidade é checagem em memória e roda antes da moderação**, mesmo princípio de
custo do resto do ciclo — moderação é chamada de LLM, elegibilidade é comparação de dict.
Contas todas no teto interrompe o loop (`skipped_no_account_capacity`) mesmo com orçamento
global sobrando — distinto de `skipped_no_capacity`, que é o teto global.

**Trava estrutural para conta única.** Com `GET /accounts` devolvendo `[]` (todo deploy
hoje, e toda a suíte de testes que não chama `accounts_on`), a checagem de elegibilidade
nunca roda — o comportamento é idêntico ao de antes desta feature **independente** do
valor de `max_pending_runs_per_account`. Mais robusto que confiar num default numérico
coincidir com `max_pending_runs`.

⚠️ **Falha em `GET /accounts` degrada para a conta default**, mesmo espírito de toda
degradação silenciosa do orchestrador — `list_accounts()` não é serviço essencial para
publicar, então uma indisponibilidade não pode travar o ciclo.

**`max_pending_runs`/`max_per_cycle` continuam intocados** — o teto global de trabalho em
andamento, protegendo a capacidade compartilhada do `blender_worker`, é ortogonal ao teto
por conta.

`SeenItem.account_id` (migration `006`) é só auditoria — `GET /scout/seen?account_id=`
mostra para onde cada história foi. A decisão em si nunca olha essa coluna isolada, só a
agregação recente via `_submitted_per_account`.

**Fora de escopo, de propósito**: a caixa de entrada manual (`inbox.py`) continua sempre na
conta default — quem já escolheu o vídeo ali foi uma pessoa, e distribuir isso entre contas
não foi pedido.

- Testes: seção "contas de publicação (round-robin)" em `test_scout.py` — desempate puro
  (`pick_account`), a janela de 24h ignorando histórico antigo, conta única inalterada,
  duas contas alternando dentro de um ciclo, teto por conta interrompendo o loop com
  orçamento global sobrando, a checagem rodando antes da moderação, persistência em
  `SeenItem`, filtro por `status`, degradação quando `GET /accounts` falha, e o campo
  exposto em `GET /scout/seen`.

`scout_loop()` — driver periódico iniciado no `lifespan` do FastAPI. Não precisa de scheduler durável: `seen_items` torna o ciclo idempotente, então um restart no pior caso repete uma passagem que não acha nada novo. O loop sobrevive a qualquer exceção de ciclo.

**Um ciclo por vez.** `run_cycle` é serializado por `_cycle_lock` (lock de processo). Quem chega no meio recebe `ScoutReport(already_running=True)` na hora, em vez de esperar minutos ou correr em paralelo.

⚠️ **Dois ciclos simultâneos eram destrutivos**, e acontecia sozinho: o loop dispara um ciclo no startup, e um `POST /scout/run` logo após um deploy corria junto. Medido ao vivo — espaçamento do Reddit caindo para 34s com 429 nos dois, e `UniqueViolationError` em `seen_items.external_id` derrubando o ciclo com 500 **depois** de já ter criado runs no orchestrador (4 runs criados, 2 registrados).

**`_record(session, row)`** grava e commita cada linha na hora, tolerando `IntegrityError`. O commit em lote no fim era o que transformava um duplicado em perda de dados: desfazia todas as linhas do ciclo, inclusive `submitted` cujos runs já existiam — e essas histórias voltariam como inéditas no ciclo seguinte, virando vídeo duplicado.

**`sources=[]` significa "nenhuma fonte".** `run_cycle` testa `is None`, não truthiness — a versão anterior caía em `build_sources()` e transformava um chamador pedindo nada em tráfego real para o Reddit.

### Endpoints (`src/content_scout/api/routes/scout.py`)

- `POST /scout/run` — roda um ciclo agora, síncrono, e devolve os contadores. Feito para calibrar filtros vendo o resultado na hora.
- `GET /scout/seen?status=&story_tag=&account_id=&limit=&offset=` — trilha de auditoria; filtre por `filtered` para ver o que foi rejeitado e por quê, por `story_tag=weak_storytelling` para calibrar `min_story_score`, e por `account_id` para ver o que uma conta específica recebeu.

**Response** (`ScoutRunResponse`): `fetched`, `already_seen`, `filtered`, `unsafe`, `submitted`, `skipped_no_capacity`, `skipped_no_account_capacity`, `moderation_unavailable`, `active_runs`, `submitted_ids`, `comments_fetched`, `story_scored`, `weak_storytelling`, `low_outrage`, `with_villain`, `story_quality_unavailable`, `archive_swept`, `archive_fetched`, `archive_wrapped`, `duplicate_story`, `already_running`.

`archive_swept` é a origem varrida no ciclo (`None` quando nenhuma estava devida); `archive_wrapped` diz que a listagem acabou e voltou ao topo; `duplicate_story` conta candidatos pulados por já existir a mesma história sob outro id.

`already_running=true` (com todos os contadores em zero) significa que já havia um ciclo em andamento e esta chamada não fez nada — não é erro.

`weak_storytelling`, `low_outrage` e `with_villain` contam tudo que foi julgado no ciclo, não só o que foi publicado. Nenhum dos três rejeita nada — são a leitura da oferta.

`GET /scout/seen` devolve também `author`, `comment_count` (nulo = não consultado), `has_hook`, `story_score`, `outrage_score`, `has_villain`, `story_tag`, `hook_line`, `story_reason` (todos nulos = não avaliado), `account_id` (nulo = conta default, incluindo toda linha anterior à migration 006) e `comments[]` com `external_id`, `author`, `text`, `position`, `published`.

### Notificação de operação (`src/core/notify.py`)

Arquivo **idêntico** ao de `orchestrator/src/core/notify.py` — mesmo padrão de `bootstrap.py`/`logger.py`. Ao editar um, copiar para o outro. API e regras completas no `CLAUDE.md` do orchestrator; aqui ficam só os enganches deste serviço.

⚠️ **O destino em produção é o ntfy pelo `NOTIFY_WEBHOOK_URL`**, com `[monitoring] webhook_format = text` no `config.ini` (o ntfy mostra o corpo como veio; em `json` o celular receberia o literal com chaves e aspas). O CallMeBot está desligado desde 16/08/2026 — cota grátis esgotada.

| Evento | Nível | Local |
|---|---|---|
| 🟢 content_scout no ar (com o estado do loop) | info | `api/app.py` `lifespan` |
| 🔎 Pesquisa de roteiros iniciada | debug | `run_cycle`, dentro do lock |
| 📝 História enviada ao pipeline (origem, título, nota, chars) | info | `_run_cycle`, após o submit |
| ⏸️ Fila cheia | debug | `_run_cycle`, no backpressure |
| ⚠️ Moderação fora do ar | warning | `_run_cycle`, no `ModerationError` |
| 📊 Pesquisa concluída (contadores do ciclo) | debug | `scout_loop` |
| ❌ Ciclo do scout falhou | error | `scout_loop` |
| ⬇️ Baixando e transcrevendo | debug | `inbox.handle_url` |
| 📥 Link virou roteiro (origem, título, chars, views) | info | `inbox.handle_url` |
| 🚧 Link recusado — fila cheia | warning | `inbox.handle_url` |
| ⚠️ Não consegui transcrever | warning | `inbox.handle_url` |
| 🔁 História repetida | info | `inbox.handle_url` |
| ✂️ Transcrição curta demais | warning | `inbox.handle_url` |
| 📴 Caixa de entrada de links caiu | error | `inbox.inbox_loop` |

**Por que o scout é o serviço que mais precisa disto.** Ele não tem run para ficar `failed`, não tem endpoint que passe a responder 500, e um loop morto é indistinguível de uma semana sem material bom. Os avisos são o único sinal de que o ciclo aconteceu — daí também o dead-man's switch `scout`, pingado ao fim de cada ciclo.

⚠️ **O resumo do ciclo sai só de `scout_loop`, não de `run_cycle`.** Um `POST /scout/run` manual devolve os mesmos números na resposta HTTP, para quem está olhando na hora; quem precisa do aviso é o ciclo automático.

⚠️ **`SCOUT_ENABLED=false` vai na mensagem de boot.** É a falha mais silenciosa que este serviço tem: sobe, responde `/health` e simplesmente nunca busca nada.

Config em `config.ini [monitoring]`. Em `debug` sai uma mensagem por ciclo mesmo sem achar nada — com `interval_seconds = 3600`, 24 mensagens/dia de "nada novo". É a primeira coisa a cortar quando o volume incomodar.

- Testes: 5 em `tests/test_scout.py` (seção "notificação de operação") — ciclo anunciado, história submetida com origem e título, fila cheia, moderação fora do ar, e nada enfileirado sem destino configurado.
- ⚠️ O fixture autouse `notify_off` (`conftest.py`) apaga as vars de destino: `bootstrap` chama `load_dotenv()`, então sem ele a suíte dispararia WhatsApp de verdade.

### Caixa de entrada de links (`src/content_scout/inbox.py`)

A **terceira porta de entrada** do sistema, ao lado do `POST /pipeline` manual e do ciclo
automático. Você compartilha o link de um vídeo pelo celular e ele vira roteiro.

**Por que existe.** O sinal que ela carrega não existe nas outras duas: o Reddit diz quantas
pessoas votaram, e a visualização de um vídeo diz que a história **prendeu**. Quem escolhe é uma
pessoa olhando o número; o trabalho do módulo é só não perder o link entre o celular e a fila.

**Por que ntfy e não endpoint.** O ntfy já está no ar como destino das notificações que saem, o app
já está no celular e ele aparece na aba de compartilhar do Android — o caminho é TikTok →
Compartilhar → ntfy, sem digitar. Um endpoint HTTP exigiria estar na LAN de casa; um bot de
Telegram exigiria token, dependência e um serviço a mais.

**API pública:**
- `inbox_loop()` — assina `{NTFY_INBOX_URL}/json` e trata cada mensagem. Iniciado no `lifespan`.
- `handle_message(message) -> list[str]` — extrai as URLs e processa **uma de cada vez**.
- `handle_url(url) -> str` — o desfecho: `submitted` / `duplicate_url` / `duplicate_video` /
  `duplicate_story` / `too_short:{n}` / `no_capacity` / `transcribe_failed` /
  `submit_failed`.
- `extract_urls(message) -> list[str]` — puro.

**Ordem das checagens, do barato ao caro** — a mesma lógica do ciclo:

| Ordem | Checagem | Custo |
|---|---|---|
| 1 | Capacidade (`max_pending_runs`) | uma consulta HTTP |
| 2 | Reenvio do mesmo link (`seen_items.url`) | uma consulta |
| 3 | Transcrição (`tts_service POST /transcribe`) | **~70s de CPU** |
| 4 | Dedup por `external_id` e por fingerprint | duas consultas |
| 5 | `min_chars` | grátis |

⚠️ **A checagem 2 existe só para não pagar a 3.** O dedup de verdade é `external_id` +
fingerprint, e os dois só ficam disponíveis **depois** de transcrever. Como reenviar o mesmo link é
o engano mais provável de quem compartilha do celular, a consulta barata paga por si.

⚠️ **O fingerprint cruza os dois caminhos.** Uma história que o scout já achou no Reddit é
reconhecida quando chega pelo TikTok, e vice-versa — é a mesma coluna `content_fingerprint`.

⚠️ **Falha de transcrição NÃO grava linha em `seen_items`.** Rate limit do TikTok é transitório;
gravar aqui faria o dedup recusar o mesmo link no reenvio, que é exatamente o que a notificação
pede para a pessoa fazer. Duplicado e filtrado gravam; falha de plataforma não.

⚠️ **Não há fila de espera para `no_capacity`.** O link é recusado e quem mandou é avisado para
reenviar. Guardá-lo exigiria uma tabela de pendências que não existe, e engolir a capacidade
converteria o freio de memória do `blender_worker` em sugestão. É a limitação conhecida do módulo.

⚠️ **Links da mesma mensagem são sequenciais, com pausa** (`delay_between_urls_seconds`). O rate
limit do TikTok é por IP: duas transcrições em paralelo derrubariam as duas.

⚠️ **Sem nota de storytelling e sem moderação.** A nota é sinal de **seleção** — ela ordena
candidatos entre si, e aqui não há ordenação: uma pessoa já escolheu. A moderação continua valendo
para o ciclo automático, onde ninguém leu o texto antes.

⚠️ **O tópico de entrada é outro tópico.** No mesmo do `NOTIFY_WEBHOOK_URL` o serviço leria as
próprias notificações de saída e tentaria achar link nelas.

⚠️ **O laço é independente de `SCOUT_ENABLED`.** Desligar a busca automática e publicar só o que se
escolhe à mão é modo de operação legítimo — provavelmente o mais usado numa semana em que a fila do
Buffer está apertada. O estado dos **dois** laços vai na mensagem de boot.

**A queda do stream é rotina, não falha.** O ntfy.sh recicla conexões ociosas; o laço reconecta em
silêncio e só avisa depois de `failures_before_alert` quedas seguidas.

**A regra de produto é automática aqui.** Tudo que passa pelo `POST /pipeline` passa pelo refino,
então o texto transcrito **não tem como** virar roteiro final — que é a regra de `docs/vision.md`
→ "Roteiro viral entra como matéria-prima". O módulo não poderia pular essa etapa nem se quisesse.

Config em `config.ini [inbox]`: `enabled`, `transcribe_timeout` (900 — a transcrição roda a ~0.4x
tempo real), `delay_between_urls_seconds`, `reconnect_delay_seconds`, `connect_timeout`,
`failures_before_alert`. Serviço em `[services] tts_url`.

### Adicionando uma fonte nova (ex.: YouTube)

1. Implemente o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) em `sources/`.
2. Devolva `Candidate` com `external_id` estável — é a chave de dedup.
3. Registre em `build_sources()`.
4. **Opcional:** para enriquecer com reações, implemente também `async fetch_comments(candidate) -> CommentThread | None` (`CommentCapableSource`). Sem isso a fonte segue válida — o scout detecta e pula.

Nada mais muda: dedup, filtros, backpressure e orçamento tratam toda fonte igual.

## Testing rules

- `tests/test_reddit_source.py` (34), `tests/test_filters.py` (11) e `tests/test_story_quality.py` (34) — marcados `no_db`, rodam sem docker. O último usa `respx` para o cliente HTTP.
- `tests/test_inbox.py` (19) — 9 puros (`no_db`: extração de URL e montagem do candidato) e 10 de integração, que exigem o banco. `TranscribeClient` e `OrchestratorClient` são mockados via `patch.object`.
- `tests/test_scout.py` (88 — inclui 10 da seção "contas de publicação (round-robin)") — integração, exige o banco `content_scout`. Orchestrador é mockado via `monkeypatch` nos métodos de `OrchestratorClient`.
- ⚠️ **Nunca rodar a suíte com DB contra o banco vivo**: o fixture autouse `clean_db` apaga `seen_items` e `archive_cursors`, ou seja, o histórico de dedup inteiro — o scout voltaria a republicar tudo. Criar um banco descartável: `docker exec content_engine-db-1 psql -U postgres -c "CREATE DATABASE content_scout_wt;"`, `alembic upgrade head` nele e rodar com `DATABASE_URL=…/content_scout_wt`.
- O helper `_candidate` em `test_scout.py` costura o `external_id` dentro do corpo. Corpos iguais fazem o dedup por fingerprint tratar todo candidato depois do primeiro como repost — um helper com `"aaa…"` para todos quebraria a suíte inteira.
- A fixture autouse `archive_off` desliga a varredura histórica por padrão; use `archive_on` para exercitá-la.
- A fixture autouse `story_quality` não pontua ninguém por padrão, então toda a suíte antiga exercita o caminho de `llm_service` inacessível — que é justamente o caso que não pode mudar de comportamento.
- Rodar com `poetry run python -m pytest` (não `poetry run pytest`): os imports são `src.*` e dependem do cwd no `sys.path`, que só o `-m` insere.
- `conftest.py` força `SCOUT_ENABLED=false` — o loop periódico jamais pode subir sob teste, senão dispara HTTP real.
