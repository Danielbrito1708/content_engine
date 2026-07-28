# CLAUDE.md — llm_service

Serviço de refinamento e classificação de roteiros via LLM. Expõe `POST /refine` que o orchestrador chama como primeira etapa do pipeline.

## Arquitetura

- `api/routes/refine.py` — endpoint principal
- `api/routes/moderate.py` — `POST /moderate`, segurança de publicação
- `api/routes/story.py` — `POST /story-quality`, gancho + storytelling em lote
- `api/routes/health.py` — `GET /health` com provider e model ativos
- `llm/base.py` — `BaseLLMClient` com método `refine()` que faz parse do JSON
- `llm/openai_compat.py` — `OpenAICompatClient` (OpenRouter + Chutes AI via `openai` package)
- `llm/anthropic_client.py` — `AnthropicClient` (API Anthropic direta)
- `llm/factory.py` — `get_llm_client()` seleciona o provider via `LLM_PROVIDER` env var
- `prompts/refine.py` — `SYSTEM_PROMPT` + `build_user_prompt(script, metadata)`
- `prompts/moderate.py`, `prompts/story.py` — idem para os outros dois endpoints
- `schemas/refine.py` — `RefineRequest`, `RefineResponse`, `Classification`, `TargetAudience`
- `schemas/story.py` — `StoryItem`, `StoryQualityRequest`, `StoryVerdict`, `StoryQualityResponse`

## Features

### Endpoint de refinamento (`src/llm_service/api/routes/refine.py`)

`POST /refine` — recebe roteiro bruto, retorna roteiro refinado + classificação.

**Request** (`RefineRequest`):
- `script` (str) — roteiro bruto
- `metadata` (dict, opcional) — dados extras (ex: `{"source": "reddit"}`)

**Response** (`RefineResponse`):
- `parts` (list[str]) — partes do roteiro refinado (1 ou mais)
- `hook` (str) — a frase gancho, isolada. **Sempre preenchida** (ver abaixo)
- `classification.content_type` — drama / comédia / motivacional / educativo / entretenimento / suspense
- `classification.tone` — suspenseful / funny / emotional / educational / inspirational / shocking
- `classification.target_audience` — `{age_range, gender, interests}`
- `classification.cta_per_part` — CTA para cada parte
- `classification.hashtag_hints` — 5–8 hashtags sugeridas
- `classification.split_rationale` — razão do corte ou `null`

Erros: `502` se o LLM retornar JSON inválido ou se a chamada à API falhar.

### Frase gancho (`hook`)

O gancho era só uma regra de escrita no prompt; virou campo porque o orchestrador narra essa frase num arquivo próprio (`audio/{run_id}/hook.mp3`) e, para isso, precisa saber onde ela termina.

**API pública** (`schemas/refine.py`):
- `derive_hook(parts) -> str` — primeira frase de `parts[0]`, com teto de `MAX_HOOK_CHARS` (200) cortado na última palavra inteira
- `RefineResponse._fill_hook` — validator `mode="after"`: `hook` vazio/branco cai em `derive_hook`

**O campo nunca volta vazio quando há roteiro.** O prompt pede o `hook` copiado literal da primeira frase da parte 1, mas o contrato não pode depender de o modelo obedecer — daí o fallback. Fim de frase = pontuação terminal (`. ! ? …` + aspas/parênteses de fechamento) **seguida de espaço**; exigir o espaço é o que impede `R$ 3.5 mil` de virar fim de frase.

O gancho **não** é removido de `parts[0]` — o campo é uma cópia identificada, não um recorte. Quem monta o vídeo usa o áudio da parte; o áudio do gancho é artefato à parte.

### Endpoint de moderação (`src/llm_service/api/routes/moderate.py`)

`POST /moderate` — decide se uma história pode virar vídeo publicado, do ponto de vista de risco de remoção da conta. Chamado pelo `content_scout`.

**Request** (`ModerateRequest`): `text` (str), `title` (str, opcional).
**Response** (`ModerateResponse`): `safe` (bool), `category` (str | null), `reason` (str | null).

Erros: `502` se o LLM falhar ou devolver JSON sem veredito. O chamador precisa distinguir "inseguro" de "não deu para checar" — o segundo é retry, nunca aprovação.

**Por que existe.** Substituiu uma blocklist por substring no `content_scout`, que não distinguia `"3 mil que não me mataria"` (figura de linguagem sobre dinheiro) de uma ameaça real de violência. O prompt em `prompts/moderate.py` traz esses casos como exemplos, e é explícito em marcar como seguro histórias pesadas — término, traição, luto, dívida — que são o material normal do produto.

**Modelo próprio.** Usa `LLM_MODERATION_MODEL`, que cai de volta para `LLM_MODEL` quando não definido. A chamada é um sim/não, então não precisa do modelo de refino — apontar para um mais barato reduz o custo por candidato.

### Endpoint de qualidade narrativa (`src/llm_service/api/routes/story.py`)

`POST /story-quality` — avalia, **em lote**, se cada candidato tem gancho e se se conta bem. Chamado pelo `content_scout` uma vez por ciclo.

**Request** (`StoryQualityRequest`): `items[]` com `index` (int), `opening` (str), `title` (str, opcional).
**Response** (`StoryQualityResponse`): `results[]` com `index`, `hook` (bool), `score` (0–10), `hook_line` (str | null), `reason` (str | null).

**Por que recebe lista e não uma história.** Ao contrário da moderação — que só roda nos 2–3 candidatos que vão ser publicados —, isto é sinal de **seleção**: precisa ver todos os candidatos do ciclo para poder ordená-los, e pontuar só a cabeça da lista seria circular. Uma chamada por candidato seriam ~30 por ciclo; uma chamada com as 30 aberturas são alguns milhares de tokens e sai mais barato que a moderação.

**O `index` é o contrato.** Cada item leva o próprio índice e os vereditos o devolvem, então um modelo que reordena ou omite entradas não desloca nota para a história errada. Índice não pedido, ou repetido, é descartado com log.

⚠️ **Degrada por item, não por lote.** Veredito malformado é descartado e os outros voltam — o chamador trata veredito ausente como "não avaliado" e cai de volta no ranking da fonte. Nota fora de 0–10 é **clampada**, não rejeitada: um número ruim não pode custar o veredito de todos os outros. Já uma resposta que não rende **nenhum** veredito utilizável é `502` — isso é falha, não resultado vazio. Lista vazia na entrada devolve 200 sem chamar o LLM.

`prompts/story.py` traz a anatomia do gancho em quatro partes (relação concreta, conflito em curso, promessa de desfecho, curiosidade não resolvida), com exemplo forte e exemplos fracos, e a régua de 0–10 que põe post comum de fórum em 4–6. O prompt é explícito em separar qualidade narrativa de aceitabilidade do assunto — essa decisão é da moderação.

**Modelo próprio.** `LLM_STORY_MODEL`, com fallback para `LLM_MODEL`. Não compartilha o modelo da moderação: julgar craft narrativo sobre um lote é mais difícil que um sim/não.

### Providers LLM (`src/llm_service/llm/`)

Selecionado por `LLM_PROVIDER` env var:

| Provider | Env var | Pacote |
|---|---|---|
| `openrouter` (padrão) | `OPENROUTER_API_KEY` | `openai` |
| `chutes` | `CHUTES_API_KEY`, `CHUTES_BASE_URL` | `openai` |
| `anthropic` | `ANTHROPIC_API_KEY` | `anthropic` |

Modelo configurado por `LLM_MODEL` (padrão: `anthropic/claude-3.5-sonnet`). `get_llm_client(model=...)` aceita override — usado pela moderação via `LLM_MODERATION_MODEL`.

`BaseLLMClient.complete_json()` faz parse de JSON tolerando cercas de código. Providers sem modo JSON nativo marcam `needs_json_hint = True` (caso da Anthropic) e a instrução vai junto no prompt.

## Testes

53 testes: `tests/test_refine.py` (9), `tests/test_moderate.py` (12), `tests/test_story.py` (17), `tests/test_hook.py` (15 — derivação do gancho, teto de 200 chars, decimal que não quebra frase, fallback quando o modelo omite o campo). LLM é sempre mockado — não há chamadas reais à API. Sem DB, sem MinIO.

```bash
poetry run pytest
```
