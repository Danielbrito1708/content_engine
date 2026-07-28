# CLAUDE.md — content_scout

Descobre roteiros candidatos na internet e submete ao orchestrador. É o serviço de **trigger** previsto em `docs/vision.md` — normaliza qualquer fonte para `plain text + metadata` antes de chamar `POST /pipeline`.

## Arquitetura

**Bootstrap**: igual aos demais serviços — `from src.core import settings` em `app.py` dispara `bootstrap._init()` antes de qualquer outro import ler env vars.

**Módulos:**
- `api/routes/scout.py` — `POST /scout/run` (ciclo manual, síncrono), `GET /scout/seen` (auditoria)
- `api/routes/health.py` — `GET /health`: ping no DB + flag do loop
- `api/app.py` — inclui o `lifespan` que sobe o `scout_loop` quando `SCOUT_ENABLED=true`
- `db/models.py` — `SeenItem` + enum `SeenStatus` + `ItemComment`
- `clients/llm.py` — `ModerationClient` (segurança) + `StoryQualityClient` (gancho/storytelling)
- `db/engine.py` — engine async + `AsyncSessionLocal` + `get_session()`
- `schemas/scout.py` — `ScoutRunResponse`, `SeenItemResponse`
- `sources/base.py` — `Candidate`, `Comment`, `CommentThread` (dataclasses) + `Source` e `CommentCapableSource` (Protocols)
- `sources/reddit.py` — leitura e parsing dos feeds Atom do Reddit (posts e comentários) + throttle compartilhado
- `filters.py` — `evaluate()` / `find_blocked_term()`, puros
- `clients/orchestrator.py` — `create_pipeline()`, `count_active_runs()`
- `scout.py` — `run_cycle()` + `scout_loop()`

## Comandos

```bash
poetry install
python main.py                    # requer .env e db up
poetry run pytest                 # requer DB content_scout
poetry run pytest -m no_db        # parser + filtros, sem docker
alembic upgrade head
```

## Variáveis de ambiente

- `ROOT_DIR`, `ENV`, `DEBUG` — padrão dos serviços
- `DATABASE_URL` — asyncpg para o banco `content_scout`
- `SCOUT_ENABLED` — `true`/`false` (default `true`): liga o loop periódico
- `SCOUT_USER_AGENT` — User-Agent das requisições ao Reddit

Nenhuma chave de API é necessária aqui — o caminho RSS não autentica, e a moderação passa pelo `llm_service`, que detém as chaves.

Config em `config.ini`: `[services] orchestrator_url` + `llm_url`, `[reddit]`, `[filters]`, `[scout]`.

Chaves novas: `[reddit] comments_limit` (0 = default do Reddit), `[scout] fetch_comments` (liga/desliga o enriquecimento) e `[scout] max_comments_stored` (quantos corpos guardar por post).

Qualidade narrativa: `[scout] story_quality` (liga/desliga), `min_story_score` (corte da tag `weak_storytelling`, padrão 6), `story_excerpt_chars` (quanto de cada corpo vai no lote, padrão 700) e `story_timeout`.

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

### Filtros determinísticos (`src/content_scout/filters.py`)

`evaluate(candidate, min_chars, max_chars) -> str | None` — devolve o motivo da rejeição ou `None`.

Só checagens baratas e determinísticas. **Limites de tamanho nas duas pontas** — curto demais não tem história; longo demais obrigaria o LLM a cortar tanto que o que vai ao ar já não é o post.

Segurança **não** mora aqui — ver abaixo.

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

⚠️ **A tag é derivada, não pedida ao modelo.** O corte mora em config, então se move contra dados reais (`GET /scout/seen?story_tag=weak_storytelling`) — mesmo motivo de `min_chars`/`max_chars`. `story_score` fica cru, então mover o corte permite re-derivar as linhas antigas. A derivação acontece na escrita: mover o corte só afeta linhas novas.

⚠️ **`story_tag IS NULL` ≠ fraco.** Nulo = não avaliado — todo filtrado (a nota roda **depois** dos filtros baratos e **depois** do backpressure: fila cheia não publica, então não paga julgamento) e todo candidato de ciclo em que o `llm_service` caiu.

`rank_by_story(candidates, scores, neutral)` — ordena por nota, desc, **estável**: como `interleave_by_origin` preserva a ordem interna de cada grupo, um sort estável na lista plana vira "melhor abertura primeiro dentro de cada origem" sem tocar na justiça entre origens. Empate mantém a posição do feed. **Candidato sem nota ordena no próprio limiar**, não no fim — mandá-lo para o fim converteria falha de modelo em handicap permanente, e são as sobras de cada ciclo que herdariam isso.

Desligável em `[scout] story_quality`.

🚧 **A régua ainda não foi validada.** A medição feita até agora pontuou relatos reais (`r/desabafos`, `r/relacionamentos`), não histórias escritas para entreter — gêneros diferentes, então a taxa de 40% `weak_storytelling` pode estar medindo a fonte e não o corte. Decisões abertas (corpus, corte em 5 vs 6, tamanho do recorte, teto 9–10 nunca usado) e os dados brutos estão em **`content_scout/docs/story_quality_calibration.md`**. Não mexer em `min_story_score` nem no prompt de `story.py` antes de ler esse arquivo.

### Ciclo do scout (`src/content_scout/scout.py`)

`run_cycle(sources=None) -> ScoutReport` — busca, deduplica, filtra e submete dentro do orçamento.

**Ordem das etapas importa:** a capacidade é checada **depois** de registrar os filtrados e **antes** de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o próximo ciclo começa de uma pilha menor.

**Backpressure** — consulta `GET /pipeline` no orchestrador e conta runs em estado ativo (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se `active_runs >= max_pending_runs`, nada é submetido. A fila free do Buffer segura 10 posts; ingerir mais rápido do que se publica só converte roteiro novo em run falho.

**Orçamento por ciclo** — `min(capacidade_restante, max_per_cycle)`. A lista ordenada é percorrida **além** do orçamento, porque candidato rejeitado pela moderação não consome vaga — a próxima história assume.

**Seleção: `interleave_by_origin(rank_by_story(...))`** — rodízio entre origens, e dentro de cada origem a nota de qualidade narrativa (ver acima) como ranking interno, com a posição do feed como desempate. O rodízio decide *de quem* é a vez; a nota decide *qual* história daquela origem.

As fontes são concatenadas na ordem do config, então pegar o começo da lista dava todas as vagas ao primeiro subreddit. Medido ao vivo: as duas submissões vieram de `r/desabafos` enquanto `r/relacionamentos` contribuiu 5 candidatos e não ganhou nenhuma — configurar mais subreddits era decorativo.

Intercalar mantém o ranking do Reddit como sinal de qualidade (continua pegando o melhor *disponível* de cada) e garante que nenhuma comunidade monopolize. Junto com o dedup, o rodízio entre ciclos emerge sozinho, sem guardar estado de rotação.

**Dedup** — `SeenItem.external_id` é único e guarda **todo** candidato avaliado, inclusive os rejeitados, com o motivo. Sem isso o scout reposta a mesma história toda semana que ela reaparece no top, e re-avalia o mesmo lixo para sempre. A tabela também serve de trilha de auditoria para calibrar os filtros contra dados reais.

**Falha isolada** — um submit que estoura é gravado como `failed` e o ciclo continua nos demais.

`scout_loop()` — driver periódico iniciado no `lifespan` do FastAPI. Não precisa de scheduler durável: `seen_items` torna o ciclo idempotente, então um restart no pior caso repete uma passagem que não acha nada novo. O loop sobrevive a qualquer exceção de ciclo.

**Um ciclo por vez.** `run_cycle` é serializado por `_cycle_lock` (lock de processo). Quem chega no meio recebe `ScoutReport(already_running=True)` na hora, em vez de esperar minutos ou correr em paralelo.

⚠️ **Dois ciclos simultâneos eram destrutivos**, e acontecia sozinho: o loop dispara um ciclo no startup, e um `POST /scout/run` logo após um deploy corria junto. Medido ao vivo — espaçamento do Reddit caindo para 34s com 429 nos dois, e `UniqueViolationError` em `seen_items.external_id` derrubando o ciclo com 500 **depois** de já ter criado runs no orchestrador (4 runs criados, 2 registrados).

**`_record(session, row)`** grava e commita cada linha na hora, tolerando `IntegrityError`. O commit em lote no fim era o que transformava um duplicado em perda de dados: desfazia todas as linhas do ciclo, inclusive `submitted` cujos runs já existiam — e essas histórias voltariam como inéditas no ciclo seguinte, virando vídeo duplicado.

**`sources=[]` significa "nenhuma fonte".** `run_cycle` testa `is None`, não truthiness — a versão anterior caía em `build_sources()` e transformava um chamador pedindo nada em tráfego real para o Reddit.

### Endpoints (`src/content_scout/api/routes/scout.py`)

- `POST /scout/run` — roda um ciclo agora, síncrono, e devolve os contadores. Feito para calibrar filtros vendo o resultado na hora.
- `GET /scout/seen?status=&story_tag=&limit=&offset=` — trilha de auditoria; filtre por `filtered` para ver o que foi rejeitado e por quê, e por `story_tag=weak_storytelling` para calibrar `min_story_score`.

**Response** (`ScoutRunResponse`): `fetched`, `already_seen`, `filtered`, `unsafe`, `submitted`, `skipped_no_capacity`, `moderation_unavailable`, `active_runs`, `submitted_ids`, `comments_fetched`, `story_scored`, `weak_storytelling`, `story_quality_unavailable`, `already_running`.

`already_running=true` (com todos os contadores em zero) significa que já havia um ciclo em andamento e esta chamada não fez nada — não é erro.

`weak_storytelling` conta tudo que foi julgado no ciclo, não só o que foi publicado.

`GET /scout/seen` devolve também `author`, `comment_count` (nulo = não consultado), `has_hook`, `story_score`, `story_tag`, `hook_line`, `story_reason` (todos nulos = não avaliado) e `comments[]` com `external_id`, `author`, `text`, `position`, `published`.

### Adicionando uma fonte nova (ex.: YouTube)

1. Implemente o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) em `sources/`.
2. Devolva `Candidate` com `external_id` estável — é a chave de dedup.
3. Registre em `build_sources()`.
4. **Opcional:** para enriquecer com reações, implemente também `async fetch_comments(candidate) -> CommentThread | None` (`CommentCapableSource`). Sem isso a fonte segue válida — o scout detecta e pula.

Nada mais muda: dedup, filtros, backpressure e orçamento tratam toda fonte igual.

## Testing rules

- `tests/test_reddit_source.py` (25), `tests/test_filters.py` (5) e `tests/test_story_quality.py` (23) — marcados `no_db`, rodam sem docker. O último usa `respx` para o cliente HTTP.
- `tests/test_scout.py` (52) — integração, exige o banco `content_scout`. Orchestrador é mockado via `monkeypatch` nos métodos de `OrchestratorClient`.
- A fixture autouse `story_quality` não pontua ninguém por padrão, então toda a suíte antiga exercita o caminho de `llm_service` inacessível — que é justamente o caso que não pode mudar de comportamento.
- Rodar com `poetry run python -m pytest` (não `poetry run pytest`): os imports são `src.*` e dependem do cwd no `sys.path`, que só o `-m` insere.
- `conftest.py` força `SCOUT_ENABLED=false` — o loop periódico jamais pode subir sob teste, senão dispara HTTP real.
