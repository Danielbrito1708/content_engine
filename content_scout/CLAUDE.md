# CLAUDE.md — content_scout

Descobre roteiros candidatos na internet e submete ao orchestrador. É o serviço de **trigger** previsto em `docs/vision.md` — normaliza qualquer fonte para `plain text + metadata` antes de chamar `POST /pipeline`.

## Arquitetura

**Bootstrap**: igual aos demais serviços — `from src.core import settings` em `app.py` dispara `bootstrap._init()` antes de qualquer outro import ler env vars.

**Módulos:**
- `api/routes/scout.py` — `POST /scout/run` (ciclo manual, síncrono), `GET /scout/seen` (auditoria)
- `api/routes/health.py` — `GET /health`: ping no DB + flag do loop
- `api/app.py` — inclui o `lifespan` que sobe o `scout_loop` quando `SCOUT_ENABLED=true`
- `db/models.py` — `SeenItem` + enum `SeenStatus`
- `db/engine.py` — engine async + `AsyncSessionLocal` + `get_session()`
- `schemas/scout.py` — `ScoutRunResponse`, `SeenItemResponse`
- `sources/base.py` — `Candidate` (dataclass) + `Source` (Protocol)
- `sources/reddit.py` — leitura e parsing dos feeds Atom do Reddit
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

Nenhuma chave de API é necessária — o caminho RSS não autentica.

Config em `config.ini`: `[services] orchestrator_url`, `[reddit]`, `[filters]`, `[scout]`.

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

### Filtros (`src/content_scout/filters.py`)

`evaluate(candidate, min_chars, max_chars, blocklist) -> str | None` — devolve o motivo da rejeição ou `None`.

- **Blocklist antes de tudo** — termo bloqueado desqualifica independente do resto. Match por substring sobre texto normalizado (casefold + sem acentos), então `suicíd` pega `SUICIDIO` e `suicidou`. Não é pudor: o TikTok remove contas por conteúdo de automutilação e abuso sexual.
- **Limites de tamanho nas duas pontas** — curto demais não tem história; longo demais obrigaria o LLM a cortar tanto que o que vai ao ar já não é o post.

### Ciclo do scout (`src/content_scout/scout.py`)

`run_cycle(sources=None) -> ScoutReport` — busca, deduplica, filtra e submete dentro do orçamento.

**Ordem das etapas importa:** a capacidade é checada **depois** de registrar os filtrados e **antes** de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o próximo ciclo começa de uma pilha menor.

**Backpressure** — consulta `GET /pipeline` no orchestrador e conta runs em estado ativo (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se `active_runs >= max_pending_runs`, nada é submetido. A fila free do Buffer segura 10 posts; ingerir mais rápido do que se publica só converte roteiro novo em run falho.

**Orçamento por ciclo** — `min(capacidade_restante, max_per_cycle, candidatos_frescos)`.

**Dedup** — `SeenItem.external_id` é único e guarda **todo** candidato avaliado, inclusive os rejeitados, com o motivo. Sem isso o scout reposta a mesma história toda semana que ela reaparece no top, e re-avalia o mesmo lixo para sempre. A tabela também serve de trilha de auditoria para calibrar os filtros contra dados reais.

**Falha isolada** — um submit que estoura é gravado como `failed` e o ciclo continua nos demais.

`scout_loop()` — driver periódico iniciado no `lifespan` do FastAPI. Não precisa de scheduler durável: `seen_items` torna o ciclo idempotente, então um restart no pior caso repete uma passagem que não acha nada novo. O loop sobrevive a qualquer exceção de ciclo.

### Endpoints (`src/content_scout/api/routes/scout.py`)

- `POST /scout/run` — roda um ciclo agora, síncrono, e devolve os contadores. Feito para calibrar filtros vendo o resultado na hora.
- `GET /scout/seen?status=&limit=&offset=` — trilha de auditoria; filtre por `filtered` para ver o que foi rejeitado e por quê.

**Response** (`ScoutRunResponse`): `fetched`, `already_seen`, `filtered`, `submitted`, `skipped_no_capacity`, `active_runs`, `submitted_ids`.

### Adicionando uma fonte nova (ex.: YouTube)

1. Implemente o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) em `sources/`.
2. Devolva `Candidate` com `external_id` estável — é a chave de dedup.
3. Registre em `build_sources()`.

Nada mais muda: dedup, filtros, backpressure e orçamento tratam toda fonte igual.

## Testing rules

- `tests/test_reddit_source.py` (14) e `tests/test_filters.py` (10) - marcados `no_db`, rodam sem docker.
- `tests/test_scout.py` (13) — integração, exige o banco `content_scout`. Orchestrador é mockado via `monkeypatch` nos métodos de `OrchestratorClient`.
- `conftest.py` força `SCOUT_ENABLED=false` — o loop periódico jamais pode subir sob teste, senão dispara HTTP real.
