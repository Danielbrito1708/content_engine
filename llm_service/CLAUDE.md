# CLAUDE.md — llm_service

Serviço de refinamento e classificação de roteiros via LLM. Expõe `POST /refine` que o orchestrador chama como primeira etapa do pipeline.

## Arquitetura

- `api/routes/refine.py` — endpoint principal
- `api/routes/health.py` — `GET /health` com provider e model ativos
- `llm/base.py` — `BaseLLMClient` com método `refine()` que faz parse do JSON
- `llm/openai_compat.py` — `OpenAICompatClient` (OpenRouter + Chutes AI via `openai` package)
- `llm/anthropic_client.py` — `AnthropicClient` (API Anthropic direta)
- `llm/factory.py` — `get_llm_client()` seleciona o provider via `LLM_PROVIDER` env var
- `prompts/refine.py` — `SYSTEM_PROMPT` + `build_user_prompt(script, metadata)`
- `schemas/refine.py` — `RefineRequest`, `RefineResponse`, `Classification`, `TargetAudience`

## Features

### Endpoint de refinamento (`src/llm_service/api/routes/refine.py`)

`POST /refine` — recebe roteiro bruto, retorna roteiro refinado + classificação.

**Request** (`RefineRequest`):
- `script` (str) — roteiro bruto
- `metadata` (dict, opcional) — dados extras (ex: `{"source": "reddit"}`)

**Response** (`RefineResponse`):
- `parts` (list[str]) — partes do roteiro refinado (1 ou mais)
- `classification.content_type` — drama / comédia / motivacional / educativo / entretenimento / suspense
- `classification.tone` — suspenseful / funny / emotional / educational / inspirational / shocking
- `classification.target_audience` — `{age_range, gender, interests}`
- `classification.cta_per_part` — CTA para cada parte
- `classification.hashtag_hints` — 5–8 hashtags sugeridas
- `classification.split_rationale` — razão do corte ou `null`

Erros: `502` se o LLM retornar JSON inválido ou se a chamada à API falhar.

### Providers LLM (`src/llm_service/llm/`)

Selecionado por `LLM_PROVIDER` env var:

| Provider | Env var | Pacote |
|---|---|---|
| `openrouter` (padrão) | `OPENROUTER_API_KEY` | `openai` |
| `chutes` | `CHUTES_API_KEY`, `CHUTES_BASE_URL` | `openai` |
| `anthropic` | `ANTHROPIC_API_KEY` | `anthropic` |

Modelo configurado por `LLM_MODEL` (padrão: `anthropic/claude-3.5-sonnet`).

## Testes

9 testes em `tests/test_refine.py`. LLM é sempre mockado — não há chamadas reais à API. Sem DB, sem MinIO.

```bash
poetry run pytest
```
