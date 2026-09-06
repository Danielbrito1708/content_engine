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
- `parts` (list[str]) — o roteiro refinado. **Sempre exatamente um elemento** (ver abaixo)
- `hook` (str) — a frase gancho, isolada. **Sempre preenchida** (ver abaixo)
- `narrator_gender` (str) — `male` / `female` / `unknown`. Quem narra, não o público (ver abaixo)
- `youtube_title` (str) — título do vídeo no YouTube, até 100 chars. **Sempre preenchido** (ver abaixo)
- `mood` (str) — `sad` / `tense` / `hopeful` / `neutral`. Escolhe a trilha sonora, não é leitura de audiência (ver abaixo)
- `classification.content_type` — drama / comédia / motivacional / educativo / entretenimento / suspense
- `classification.tone` — suspenseful / funny / emotional / educational / inspirational / shocking
- `classification.target_audience` — `{age_range, gender, interests}`
- `classification.cta_per_part` — a pergunta que **fecha o texto narrado**, copiada literal (ver abaixo)
- `classification.binary_cta` — pergunta de **escolha binária para a legenda**, texto novo e independente de `cta_per_part` (ver abaixo). `""` quando a história não tem dois lados claros
- `classification.hashtag_hints` — 5–8 hashtags sugeridas
- `classification.split_rationale` — vestigial: sempre `null` no formato curto, e o rastro de auditoria quando o modelo desobedece e a guarda junta as partes

Erros: `502` se o LLM retornar JSON inválido ou se a chamada à API falhar.

### Formato: uma história completa em 10–40s (`src/llm_service/prompts/refine.py`)

**Sempre uma parte.** A história é *recontada condensada* para caber na janela; não existe
mais divisão nem cliffhanger de continuação.

**API pública** (constantes exportadas):
- `TARGET_MIN_SECONDS = 10` / `TARGET_MAX_SECONDS = 40` — a banda do formato
- `NARRATION_WPM = 225` — voz neural pt-BR (~150 wpm) acelerada pelo `narration.rate` do
  template (`+50%`). ⚠️ **Anda junto com o rate publicado no bucket**: mexer num sem o outro
  desloca a banda inteira sem erro nenhum
- `TARGET_MIN_WORDS` (37) / `TARGET_MAX_WORDS` (150) — derivados de segundos × wpm, porque
  palavra é o que o modelo conta

Substituiu `MAX_PART_MINUTES = 30` / `MAX_PART_WORDS = 5850`, e **inverteu a regra de
conservação**: o prompt dizia *"Não resuma, não encurte"* e agora manda `RECONTAR a história
condensada`. A regra antiga existia porque o modo de falha de então era resumir em vez de
dividir; sem divisão, resumir virou o trabalho. O que a substituiu não é "resuma", é uma
instrução sobre **o que** cortar (preserva conflito, detalhe concreto e virada; corta
personagem secundário e contexto que não muda o julgamento).

**Agora há guarda determinística** — `RefineResponse._collapse_to_single_part` (ver abaixo).

`tests/test_split_policy.py` (17) fixa a banda, sua derivação e a guarda.

### Uma parte só, garantida no schema (`schemas/refine.py`)

`RefineResponse._collapse_to_single_part` — validator `mode="after"`, **definido antes de
todos os outros**: junta qualquer `parts` de tamanho N>1 num elemento só e descarta os
brancos.

⚠️ **Junta, nunca descarta o excedente.** Os dois modos de falha não se equivalem: vídeo
longo demais é ruim, visível na hora e auditável em `split_rationale`; vídeo sem o fim da
história é produto quebrado e ninguém repara até assistir. O corte de tamanho é do prompt,
não daqui — no validator não há como cortar sem partir frase ao meio.

A mecânica de série a jusante (`PipelinePart`, rótulo "(Parte n/N)", `follows_at`) não foi
removida: com `parts` sempre de tamanho 1 ela não dispara, e é o que sustenta esta guarda
quando o modelo desobedece.

### A narração termina numa pergunta em primeira pessoa

A última frase do texto narrado é a decisão em aberto da história, feita por quem narra:
*"devo me separar?"*, *"devo processar meu ex-marido?"*. O modelo copia a mesma frase,
literal, em `classification.cta_per_part` — mesmo padrão do `hook`, que também é cópia
identificada de um trecho que continua dentro do roteiro.

⚠️ **Reverte a decisão de 27/08/2026**, que tirou o CTA do texto narrado. Aquela regra
existia por um motivo específico: o CTA era genérico ("Comenta o que você faria 👇") e vinha
*depois* do desfecho. A pergunta agora não fecha o vídeo — ela **é** onde a história para.
Daí a cláusula que exige a decisão `AINDA ESTÁ ABERTA`: perguntar "devo me separar?" numa
história já terminada na separação é incoerente, e é o modo de falha a vigiar.

**O que foi removido em 27/08 continua removido**: despedida, moral, "e é isso",
like/follow/inscrição. O prompt também proíbe a pergunta genérica que serviria para qualquer
vídeo, e exige a que cita o que aconteceu ali.

⚠️ **A pergunta não vai na legenda** — `compose_caption` no `tiktok_poster` deixou de
escrever o `cta`. Ver `tiktok_poster/CLAUDE.md`.

### CTA de votação binária na legenda (`classification.binary_cta`, 02/09/2026)

Campo novo dentro de `classification`, ao lado de `cta_per_part` — e **independente** dele.
Existe para tentar aumentar o volume de comentários sem reabrir a REGRA DO FECHAMENTO: a
pergunta que fecha a narração continua nunca indo para a legenda, mas a legenda pode levar
uma pergunta *diferente*, pensada só para quem lê antes de assistir.

O prompt pede uma **escolha entre duas opções** sobre o dilema da história — "quem errou
mais: o marido ou a sogra?", "comenta 1 se perdoaria, 2 se terminava na hora" — nunca sobre o
desfecho. Escolher entre duas coisas custa menos atrito do que formular uma opinião do zero,
que é a aposta por trás do campo (ver `docs/comentarios.md` — Frente 1).

**API pública** (`schemas/refine.py`):
- `MAX_BINARY_CTA_CHARS = 100` — teto do campo, porque ele abre a legenda do TikTok e só os
  primeiros ~50–80 caracteres aparecem antes do corte de "...mais"
- `truncate_binary_cta(text) -> str` — corta em `MAX_BINARY_CTA_CHARS` sem partir palavra,
  mesmo padrão de `truncate_title`
- `Classification._truncate_binary_cta` — validator `mode="after"` em `Classification`, não em
  `RefineResponse`: o campo não depende de nenhum outro (ao contrário do `hook`/`youtube_title`,
  que se derivam um do outro), então não precisa de ordem entre validators

**Vazio é resultado normal, não falha.** Sem dois lados claros para escolher entre, o prompt
manda deixar `""` — a legenda segue só com hashtags, como já era antes deste campo existir.
Não há fallback derivado do roteiro, ao contrário de `hook`/`youtube_title`: forçar uma
pergunta binária numa história sem dilema produziria pergunta artificial, o exato problema que
a REGRA DO FECHAMENTO já evita do lado da narração.

⚠️ **`classification` trafega opaco.** O `tiktok_poster` lê o dict inteiro sem schema próprio
e o orchestrador não remapeia campo a campo — então este campo não exigiu mudança nenhuma no
orchestrador nem migração de banco. Ver `tiktok_poster/CLAUDE.md` → "CTA de votação binária".

Testes: `tests/test_binary_cta.py` (15 — truncamento, independência de `cta_per_part`, vazio
por padrão, as cláusulas do prompt e o campo na rota).

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

### Mood da história (`mood`)

Campo de topo do `RefineResponse`, ao lado do `hook` e do `narrator_gender` — não entra em `classification`. É o clima emocional da história, e é o que o orchestrador usa para escolher a trilha sonora do vídeo.

**API pública** (`schemas/refine.py`):
- `MOODS = ("sad", "tense", "hopeful", "neutral")`
- `normalize_mood(value) -> str` — qualquer entrada para um desses quatro
- `RefineResponse._normalize_mood` — validator `mode="after"`; campo ausente é `"neutral"`

**Por que existe.** Até 01/09/2026 todo vídeo saía com a mesma trilha, história triste ou de final feliz. A música precisa combinar com o clima da história, e como este serviço já é quem lê o roteiro inteiro para deduzir `narrator_gender`, é aqui que o clima também é lido — nenhum outro ponto do pipeline vê o texto completo.

⚠️ **Não é `classification.tone`.** `tone` (`suspenseful`/`funny`/`emotional`/`educational`/`inspirational`/`shocking`) informa hashtag e edição, e mistura registros que não mapeiam para música — `emotional` cobre tanto luto quanto reconciliação, climas opostos para uma trilha. `mood` responde só "que cama sonora combina com isto".

**Na dúvida, `neutral`** — mesma lógica do `unknown` do narrador: errar o mood põe a música errada debaixo da história, e `neutral` é o chão seguro (a trilha que todo vídeo já usava antes deste campo existir).

**Normaliza em vez de rejeitar**, pela mesma razão dos outros campos de topo: o valor sai de um modelo, e um `"feliz"` fora do contrato não pode derrubar o refino de um roteiro correto.

- Testes: `tests/test_mood.py` (22 — normalização, default do schema, a distinção de `classification.tone`, as cláusulas do prompt, o campo no exemplo de JSON fora de `classification`, e o endpoint).

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
**Response** (`StoryQualityResponse`): `results[]` com `index`, `hook` (bool), `score` (0–10), `outrage` (0–10 | null), `villain` (bool), `hook_line` (str | null), `reason` (str | null).

**Por que recebe lista e não uma história.** Ao contrário da moderação — que só roda nos 2–3 candidatos que vão ser publicados —, isto é sinal de **seleção**: precisa ver todos os candidatos do ciclo para poder ordená-los, e pontuar só a cabeça da lista seria circular. Uma chamada por candidato seriam ~30 por ciclo; uma chamada com as 30 aberturas são alguns milhares de tokens e sai mais barato que a moderação.

**O `index` é o contrato.** Cada item leva o próprio índice e os vereditos o devolvem, então um modelo que reordena ou omite entradas não desloca nota para a história errada. Índice não pedido, ou repetido, é descartado com log.

⚠️ **Degrada por item, não por lote.** Veredito malformado é descartado e os outros voltam — o chamador trata veredito ausente como "não avaliado" e cai de volta no ranking da fonte. Nota fora de 0–10 é **clampada**, não rejeitada: um número ruim não pode custar o veredito de todos os outros. Já uma resposta que não rende **nenhum** veredito utilizável é `502` — isso é falha, não resultado vazio. Lista vazia na entrada devolve 200 sem chamar o LLM.

`prompts/story.py` traz a anatomia do gancho em quatro partes (relação concreta, conflito em curso, promessa de desfecho, curiosidade não resolvida), com exemplos fortes e fracos, e a régua de 0–10 que põe post comum de fórum em 4–6. O prompt é explícito em separar qualidade narrativa de aceitabilidade do assunto — essa decisão é da moderação.

⚠️ **A pergunta ao fórum só desconta quando SUBSTITUI a história.** A versão anterior descontava por "pergunta direta ao fórum" sem qualificar, e isso passou a ser um autogol quando o corpus virou `r/EuSouOBabaca`: *todo* post de lá é literalmente "Sou babaca por…?". A pergunta que vem **depois** do conflito e pede um veredito sobre ele é estrutura de história e das boas — o que desconta é a pergunta que aparece no lugar da cena. Sem essa distinção o melhor corpus disponível tiraria nota baixa pelo motivo errado.

**Modelo próprio.** `LLM_STORY_MODEL`, com fallback para `LLM_MODEL`. Não compartilha o modelo da moderação: julgar craft narrativo sobre um lote é mais difícil que um sim/não.

### Revolta e vilão (`outrage` / `villain`, em `/story-quality`)

O canal seleciona por **indignação**: a história que rende comentário é a que tem alguém claramente errado. O prompt pede dois campos a mais por candidato — `villain` (existe alguém indefensável?) e `outrage` (0–10, quanta revolta a história provoca) — e o `content_scout` ordena o ciclo por `outrage_weight × outrage + score`.

**São eixos separados de `score`, de propósito.** Um relato mal escrito pode ser revoltante e uma história muito bem contada pode não ter vilão nenhum. O prompt diz isso explicitamente e manda responder os dois com sinceridade, porque quem decide o peso entre eles é quem chama — não o modelo.

**O topo da escala tem público.** 8–10 é reservado para a revolta que atinge o público principal do canal, **mulheres de 18 a 35**: traição, sogra invasiva, marido ausente, amiga falsa, homem que descarta e volta. Revolta que só funciona para outro público pontua, mas não chega ao topo. Sem essa cláusula o modelo dá 9 para briga de trânsito, que é revolta de verdade e não é a do canal.

⚠️ **`outrage` ausente é `null`, não `0`.** Zero é um julgamento ("não há com quem se indignar"); ausente é o modelo não ter respondido. O chamador ordena o ausente no ponto neutro, e um veredito sem `outrage` continua valendo pelo `score` — a degradação é por campo. Nota fora de 0–10 é clampada pelo mesmo validator de `score`.

⚠️ **Não é decisão de segurança.** O prompt é explícito: julgar potencial de reação não é aprovar o que o vilão fez, e não é decidir se o assunto pode ir ao ar — isso continua sendo `POST /moderate`.


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

165 testes: `tests/test_refine.py` (14 — inclui as cláusulas da pergunta final), `tests/test_moderate.py` (12), `tests/test_story.py` (28), `tests/test_split_policy.py` (17 — a banda de 10–40s, sua derivação e a guarda de parte única), `tests/test_narrator.py` (24 — o gênero de quem narra), `tests/test_mood.py` (22 — o clima da história, que escolhe a trilha), `tests/test_hook.py` (15 — derivação do gancho, teto de 200 chars, decimal que não quebra frase, fallback quando o modelo omite o campo), `tests/test_youtube_title.py` (18 — corte em 100 chars, fallback pelo gancho, a ordem dos validators e as cláusulas do prompt), `tests/test_binary_cta.py` (15 — o CTA de votação binária na legenda, independente de `cta_per_part`). LLM é sempre mockado — não há chamadas reais à API. Sem DB, sem MinIO.

```bash
poetry run pytest
```
