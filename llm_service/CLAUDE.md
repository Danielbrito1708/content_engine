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
- `narrator_gender` (str) — `male` / `female` / `unknown`. Quem narra, não o público (ver abaixo)
- `youtube_title` (str) — título do vídeo no YouTube, até 100 chars. **Sempre preenchido** (ver abaixo)
- `classification.content_type` — drama / comédia / motivacional / educativo / entretenimento / suspense
- `classification.tone` — suspenseful / funny / emotional / educational / inspirational / shocking
- `classification.target_audience` — `{age_range, gender, interests}`
- `classification.cta_per_part` — CTA para cada parte, **só para a legenda do post** (ver abaixo)
- `classification.hashtag_hints` — 5–8 hashtags sugeridas
- `classification.split_rationale` — razão do corte ou `null`

Erros: `502` se o LLM retornar JSON inválido ou se a chamada à API falhar.

### Política de divisão do roteiro (`src/llm_service/prompts/refine.py`)

O padrão é **uma parte só**: a história completa num vídeo. Dividir é exceção e só acima de 30 minutos de fala.

**API pública** (constantes exportadas):
- `MAX_PART_MINUTES = 30` — teto de duração de um vídeo
- `NARRATION_WPM = 195` — voz neural pt-BR (~150 wpm) acelerada pelo `narration.rate` do template (`+30%`). **Anda junto com o rate**: mexer num sem o outro desloca o teto real de 30 minutos
- `MAX_PART_WORDS = MAX_PART_MINUTES * NARRATION_WPM` (5850) — o número que vai no prompt, porque palavra é o que o modelo conta

O prompt anterior cortava em 600 palavras (~1 min), o que fatiava uma história de 6000 caracteres em seis vídeos. O prompt também proíbe **resumir para caber** — sem isso o modelo troca a divisão por perda de conteúdo, que é pior e invisível.

**Não há guarda determinística.** Reunir partes devolvidas contra a regra exigiria remover os "Na parte anterior..." e os CTAs de meio de história — reescrita, não validação. A obediência é auditável em `parts` e `split_rationale`.

`tests/test_split_policy.py` (8) fixa o teto, sua derivação a partir de minutos e as cláusulas do prompt.

### Idioma de saída do refino

O prompt manda **sempre** devolver o roteiro em português do Brasil, traduzindo quando o roteiro bruto vier em outro idioma — recontando em português, não ao pé da letra (gírias, medidas e moeda viram o equivalente brasileiro; nomes próprios ficam).

**Por que existe.** O `content_scout` passou a buscar em `r/story` e `r/stories`, que são em inglês e é onde mora o gênero "história escrita para entreter". Sem essa regra o prompt só dizia "preserve o conteúdo e a essência", e o roteiro sairia em inglês — indo direto para um TTS configurado em pt-BR. A regra é inócua para as fontes em português, que já chegam no idioma certo.

O `POST /story-quality` também foi avisado de que as aberturas podem vir em inglês: julga a história, nunca o idioma. `reason` continua saindo em português; `hook_line` sai copiada do original, no idioma dele — a tradução acontece depois, no refino.

### Frase gancho (`hook`)

O gancho era só uma regra de escrita no prompt; virou campo porque o orchestrador narra essa frase num arquivo próprio (`audio/{run_id}/hook.mp3`) e, para isso, precisa saber onde ela termina.

**API pública** (`schemas/refine.py`):
- `derive_hook(parts) -> str` — primeira frase de `parts[0]`, com teto de `MAX_HOOK_CHARS` (200) cortado na última palavra inteira
- `RefineResponse._fill_hook` — validator `mode="after"`: `hook` vazio/branco cai em `derive_hook`

**O campo nunca volta vazio quando há roteiro.** O prompt pede o `hook` copiado literal da primeira frase da parte 1, mas o contrato não pode depender de o modelo obedecer — daí o fallback. Fim de frase = pontuação terminal (`. ! ? …` + aspas/parênteses de fechamento) **seguida de espaço**; exigir o espaço é o que impede `R$ 3.5 mil` de virar fim de frase.

O gancho **não** é removido de `parts[0]` — o campo é uma cópia identificada, não um recorte. Quem monta o vídeo usa o áudio da parte; o áudio do gancho é artefato à parte.

### Título do YouTube (`youtube_title`)

Campo de topo do `RefineResponse`, ao lado do `hook`. Existe porque o mesmo vídeo passou a ser publicado também no YouTube, e lá o post tem **título obrigatório** — o Buffer recusa a criação sem ele. O TikTok não tem título, só legenda, então até aqui o refino não produzia nenhum.

**Não é o gancho reaproveitado.** Os dois textos são lidos em momentos diferentes: o gancho é a primeira coisa **ouvida** depois que o vídeo abre, escrito para prender quem já está assistindo; o título é a única coisa **lida** antes de o vídeo abrir, por quem ainda não sabe nada da história e está decidindo se clica. O prompt proíbe explicitamente copiar o gancho.

**API pública** (`schemas/refine.py`):
- `MAX_TITLE_CHARS = 100` — teto rígido da API do YouTube, não escolha editorial
- `truncate_title(text) -> str` — normaliza espaço, corta na última palavra inteira e limpa pontuação pendurada (`,;:-–—`) para o título não terminar no meio de uma enumeração
- `derive_youtube_title(hook, parts) -> str` — fallback: o gancho truncado
- `RefineResponse._fill_youtube_title` — validator `mode="after"`

⚠️ **`_fill_youtube_title` é definido DEPOIS de `_fill_hook`.** Validators `mode="after"` rodam na ordem de definição, e o fallback do título é o gancho — inverter os dois derivaria o título de um gancho ainda vazio. Coberto por teste.

**Nunca volta vazio quando há roteiro**, pela mesma razão do `hook`: um modelo que ignora o campo novo não pode produzir vídeo sem título, porque sem título não há post.

O prompt também proíbe `(Parte 1/2)` no título — o rótulo de parte é acrescentado pelo `tiktok_poster`, que é quem sabe quantas partes a história tem.

### Gênero do narrador (`narrator_gender`)

Campo de topo do `RefineResponse`, ao lado do `hook` — não entra em `classification`. É o gênero de **quem conta** a história em primeira pessoa, e é o que faz o `tts_service` escolher a voz da narração.

**API pública** (`schemas/refine.py`):
- `NARRATOR_GENDERS = ("male", "female", "unknown")`
- `normalize_narrator_gender(value) -> str` — qualquer entrada para um desses três
- `RefineResponse._normalize_narrator` — validator `mode="after"`; campo ausente é `"unknown"`

**Por que existe.** As histórias são narradas em primeira pessoa e a voz precisa concordar com quem fala; até aqui toda narração saía na mesma voz feminina, inclusive a de narrador homem. Quem lê o roteiro inteiro é este serviço, então é aqui que a dedução acontece — nenhum outro ponto do pipeline vê o texto completo antes do TTS.

⚠️ **Não é `target_audience.gender`.** Um é quem narra, o outro é para quem se narra, e os dois divergem o tempo todo (história de homem com público majoritariamente feminino é o caso comum do corpus). O prompt separa os dois explicitamente, e há teste fixando a distinção.

O prompt manda deduzir do texto — concordância (`"fiquei cansada"`, `"eu estava sozinho"`), como chamam o narrador, papel declarado (`"meu marido"`, `"sou pai de dois"`) — e **preferir `unknown` a chutar**: errar o gênero é a primeira coisa que o espectador percebe, e `unknown` apenas mantém a voz padrão.

**Normaliza em vez de rejeitar**, pela mesma razão do fallback do `hook`: o campo sai de um modelo, e um `"masculino"` não pode derrubar o refino de um roteiro que está inteiro e correto.

- Testes: `tests/test_narrator.py` (24 — normalização, default do schema, as cláusulas do prompt, o campo no exemplo de JSON fora de `classification`, e o endpoint).

### O texto narrado não tem finalização (`prompts/refine.py`)

O prompt pedia que **cada parte terminasse com um CTA** ("Comenta o que você faria 👇"). As partes vão literais para o TTS (`text=part.script` no orchestrador), então esse CTA era **falado no vídeo**, depois do desfecho da história. Regra removida, junto com qualquer outra forma de finalização — despedida, moral, "e é isso", pedido de like/follow. A última frase narrada é a última coisa que acontece na história.

`cta_per_part` **continua existindo como campo**: quem o consome é o `tiktok_poster` em `compose_caption()`, na legenda do post. São dois artefatos com o mesmo nome, e só um deles estava no lugar errado. O prompt agora diz isso explicitamente, e o exemplo de JSON no user prompt marca o campo como "só para a legenda".

O corte com cliffhanger não foi afetado — um corte no meio da tensão é parte da história.

- Testes: `tests/test_refine.py` — dois testes de prompt, um garantindo que a regra do CTA no texto narrado não voltou, outro que o campo continua sendo pedido.

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

`prompts/story.py` traz a anatomia do gancho em quatro partes (relação concreta, conflito em curso, promessa de desfecho, curiosidade não resolvida), com exemplos fortes e fracos, e a régua de 0–10 que põe post comum de fórum em 4–6. O prompt é explícito em separar qualidade narrativa de aceitabilidade do assunto — essa decisão é da moderação.

⚠️ **A pergunta ao fórum só desconta quando SUBSTITUI a história.** A versão anterior descontava por "pergunta direta ao fórum" sem qualificar, e isso passou a ser um autogol quando o corpus virou `r/EuSouOBabaca`: *todo* post de lá é literalmente "Sou babaca por…?". A pergunta que vem **depois** do conflito e pede um veredito sobre ele é estrutura de história e das boas — o que desconta é a pergunta que aparece no lugar da cena. Sem essa distinção o melhor corpus disponível tiraria nota baixa pelo motivo errado.

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

108 testes: `tests/test_refine.py` (11), `tests/test_moderate.py` (12), `tests/test_story.py` (17), `tests/test_split_policy.py` (8 — teto de 30 min e as cláusulas do prompt), `tests/test_narrator.py` (24 — o gênero de quem narra), `tests/test_hook.py` (15 — derivação do gancho, teto de 200 chars, decimal que não quebra frase, fallback quando o modelo omite o campo), `tests/test_youtube_title.py` (18 — corte em 100 chars, fallback pelo gancho, a ordem dos validators e as cláusulas do prompt). LLM é sempre mockado — não há chamadas reais à API. Sem DB, sem MinIO.

```bash
poetry run pytest
```
