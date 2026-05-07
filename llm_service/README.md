# llm_service

Serviço de refinamento e classificação de roteiros via LLM. Recebe um roteiro bruto e retorna partes refinadas + classificação estruturada para uso no pipeline.

---

## Setup local

**Pré-requisitos:** Python 3.11+, Poetry.

```bash
cd llm_service
cp .env.example .env   # preencher com as variáveis abaixo
poetry install

# Iniciar (porta 8002)
python main.py
```

Ou via Docker:

```bash
# na raiz do monorepo
docker compose up llm_service
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ROOT_DIR` | Sim | Caminho absoluto para a raiz do serviço |
| `ENV` | Não | `dev` (padrão) ou `prod` |
| `LLM_PROVIDER` | Não | `openrouter` (padrão), `chutes` ou `anthropic` |
| `LLM_MODEL` | Não | ID do modelo (padrão: `anthropic/claude-3.5-sonnet`) |
| `OPENROUTER_API_KEY` | Se `LLM_PROVIDER=openrouter` | API key do OpenRouter |
| `CHUTES_API_KEY` | Se `LLM_PROVIDER=chutes` | API key da Chutes AI |
| `CHUTES_BASE_URL` | Se `LLM_PROVIDER=chutes` | Base URL da API Chutes |
| `ANTHROPIC_API_KEY` | Se `LLM_PROVIDER=anthropic` | API key da Anthropic |

**Modelo recomendado para produção:** `anthropic/claude-haiku-4-5` via OpenRouter (boa relação custo/qualidade para roteiros em PT-BR).

---

## Endpoints

### GET /health

Retorna o provider e modelo ativos.

```bash
curl http://localhost:8002/health
```

```json
{ "status": "ok", "provider": "openrouter", "model": "anthropic/claude-haiku-4-5" }
```

---

### POST /refine — Refinar roteiro

```bash
curl -X POST http://localhost:8002/refine \
  -H "Content-Type: application/json" \
  -d '{
    "script": "Você sabia que a água quente congela mais rápido que a fria? Esse fenômeno é o efeito Mpemba.",
    "metadata": {"source": "reddit"}
  }'
```

```json
{
  "parts": [
    "Você não vai acreditar nisso: a água QUENTE congela mais rápido que a fria! Esse fenômeno surpreendente se chama efeito Mpemba e os cientistas ainda debatem o motivo até hoje. Comenta se você já ouviu falar disso 👇"
  ],
  "classification": {
    "content_type": "educativo",
    "tone": "shocking",
    "target_audience": {
      "age_range": [16, 35],
      "gender": "all",
      "interests": ["ciencia", "curiosidades"]
    },
    "cta_per_part": ["Comenta se você já ouviu falar disso 👇"],
    "hashtag_hints": ["#ciencia", "#curiosidades", "#efeitompemba", "#fisica", "#sabiadisso"],
    "split_rationale": null
  }
}
```

**Campos da resposta:**

| Campo | Tipo | Descrição |
|---|---|---|
| `parts` | `list[str]` | Partes do roteiro refinado (1 ou mais) |
| `classification.content_type` | str | `drama`, `comédia`, `motivacional`, `educativo`, `entretenimento` ou `suspense` |
| `classification.tone` | str | `suspenseful`, `funny`, `emotional`, `educational`, `inspirational` ou `shocking` |
| `classification.target_audience` | dict | `age_range`, `gender`, `interests` |
| `classification.cta_per_part` | `list[str]` | CTA para cada parte |
| `classification.hashtag_hints` | `list[str]` | 5–8 hashtags sugeridas |
| `classification.split_rationale` | str ou null | Razão do corte (se dividido em partes) |

**Erros:**
- `502` — LLM retornou JSON inválido ou a chamada à API falhou

---

## Rodar testes

Sem dependências externas — o LLM é sempre mockado.

```bash
cd llm_service
poetry run pytest
```

9 testes em `tests/test_refine.py`.
