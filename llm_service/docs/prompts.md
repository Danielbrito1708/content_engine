# Prompts — llm_service

Documentação do system prompt, estrutura do user prompt e orientações para tuning.

**Arquivo:** `src/llm_service/prompts/refine.py`

---

## System prompt

```
Você é um especialista em criação de conteúdo viral para TikTok.
Sua função é receber um roteiro bruto e retornar um JSON com o roteiro refinado e classificado.
```

### Regras de refinamento

| Regra | Detalhe |
|---|---|
| Idioma | Sempre português do Brasil — roteiro bruto em outro idioma é traduzido, recontado e não ao pé da letra |
| Gancho obrigatório | A primeira frase deve prender o espectador em até 2 segundos |
| Linguagem | Coloquial, direta e envolvente |
| Tamanho por parte | Máximo `MAX_PART_WORDS` (5100 palavras ≈ 30 minutos de fala) |
| Divisão em partes | Exceção, não padrão: a história completa vai numa parte só. Só divide acima do teto acima, com cliffhanger no corte |
| Continuidade | Partes 2+ iniciam com resumo curto: "Na parte anterior, [1-2 frases]..." |
| CTA | **Nunca** no texto narrado — vive só no campo `cta_per_part`, que vira legenda do post |
| Final | O texto narrado acaba quando a história acaba: sem despedida, moral ou "e é isso" |
| Fidelidade | O conteúdo e a essência do roteiro original devem ser preservados — sem resumir para caber |

### Regras de classificação

| Campo | Valores aceitos |
|---|---|
| `content_type` | `drama`, `comédia`, `motivacional`, `educativo`, `entretenimento`, `suspense` |
| `tone` | `suspenseful`, `funny`, `emotional`, `educational`, `inspirational`, `shocking` |
| `target_audience.gender` | `female`, `male`, `all` |
| `hashtag_hints` | 5 a 8 hashtags em português e inglês relevantes ao conteúdo |

O LLM deve retornar **apenas** JSON válido, sem markdown e sem texto fora do JSON.

---

## User prompt

Construído por `build_user_prompt(script, metadata)`. Estrutura:

```
Roteiro:
{script}

Metadados adicionais: {metadata}   ← omitido se metadata for vazio

Retorne um JSON com esta estrutura exata:
{
  "parts": ["texto completo da parte 1", "texto completo da parte 2"],
  "classification": {
    "content_type": "drama",
    "tone": "suspenseful",
    "target_audience": {
      "age_range": [15, 25],
      "gender": "female",
      "interests": ["relationships", "drama"]
    },
    "cta_per_part": ["CTA da parte 1", "CTA da parte 2"],
    "hashtag_hints": ["#hashtag1", "#hashtag2"],
    "split_rationale": "razão do corte ou null se não dividido"
  }
}
```

O schema explícito no user prompt reduz alucinações de estrutura. Se o LLM retornar JSON inválido, o endpoint responde `502`.

---

## Configuração via variáveis de ambiente

| Variável | Padrão | Efeito |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` | Seleciona `OpenAICompatClient` (OpenRouter), `OpenAICompatClient` (Chutes) ou `AnthropicClient` |
| `LLM_MODEL` | `anthropic/claude-3.5-sonnet` | ID do modelo enviado à API |

O modelo é passado diretamente para a chamada de API — qualquer modelo suportado pelo provider pode ser usado.

---

## Parâmetros de geração

Definidos em `OpenAICompatClient.complete()` e `AnthropicClient.complete()`:

| Parâmetro | Valor | Justificativa |
|---|---|---|
| `temperature` | `0.7` | Balanceia criatividade e coerência. Valores acima de 0.9 aumentam erros de JSON. |
| `response_format` | `{"type": "json_object"}` | Força saída JSON no OpenRouter/Chutes (não aplicável ao Anthropic) |

---

## Modelos testados

| Provider | Modelo | Resultado |
|---|---|---|
| OpenRouter | `anthropic/claude-haiku-4-5` | Bom — rápido, barato, JSON consistente em PT-BR |
| OpenRouter | `anthropic/claude-3.5-sonnet` | Não disponível no OpenRouter em 05/2026 |
| Anthropic direto | `claude-sonnet-4-6` | Excelente qualidade, maior latência |

---

## Tuning e boas práticas

**Roteiros curtos (< 200 palavras):** sempre retornam uma parte. O gancho e o CTA são os campos mais impactados pela qualidade do modelo.

**Roteiros longos:** o normal é continuarem numa parte só — o teto de divisão é de 30 minutos de narração, bem acima do que um roteiro típico ocupa. Quando a divisão acontecer, o `split_rationale` indica onde o LLM escolheu cortar; revisar se o cliffhanger está em uma posição narrativa adequada.

**Metadados úteis:** passar `{"source": "reddit", "subreddit": "relacionamentos"}` melhora a segmentação de `target_audience` e a relevância das `hashtag_hints`.

**Falhas comuns:**
- `split_rationale` preenchido mesmo com uma única parte — não impacta o pipeline, apenas desconsiderar.
- Hashtags em inglês misturadas com PT-BR — comportamento esperado e desejado para alcance maior.
- CTA genérico ("curtir e seguir") — acontece com modelos menores; preferir `claude-haiku-4-5` ou superior.

---

## O que ainda falta implementar

- **Validação de `content_type` e `tone`**: o endpoint aceita qualquer string — não valida contra os valores permitidos.
- **Retry em JSON inválido**: uma segunda tentativa com temperatura reduzida (0.3) poderia recuperar casos de JSON malformado sem expor o `502` ao orchestrador.
- **Suporte a `metadata` tipado**: atualmente `metadata` é `dict` opaco — um schema explícito (ex: `source`, `subreddit`, `url`) permitiria prompts mais direcionados.
