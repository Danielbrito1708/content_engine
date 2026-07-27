# Vision — content_engine

## Visão Geral

`content_engine` é um pipeline de automação de conteúdo para TikTok. Recebe um roteiro em texto, refina e classifica o conteúdo com LLM, gera o áudio com TTS, monta e renderiza o vídeo no Blender, e agenda a publicação no TikTok. O sistema é gerenciado por um orchestrador central que coordena todos os serviços e mantém o estado de cada pipeline run.

---

## Serviços

| Serviço | Porta interna | Responsabilidade |
|---|---|---|
| `orchestrator` | 8000 | Coordenação do pipeline, API principal, DB de estado |
| `blender_worker` | 8001 | Montagem VSE e renderização de vídeo |
| `llm_service` | 8002 | Refinamento, classificação e divisão de roteiros |
| `tts_service` | 8003 | Geração de áudio a partir de texto |
| `tiktok_poster` | 8004 | Publicação, agendamento, hashtags e analytics |
| `content_scout` | 8005 | Descoberta automática de roteiros na internet |
| `dashboard` | 3000 | Interface web simples para submissão e monitoramento |
| `db` (infra) | 5433 | PostgreSQL compartilhado entre serviços |
| `minio` (infra) | 9000 | Armazenamento de arquivos (áudio, vídeo, assets) |

---

## Pipeline Completo

```
content_scout (periódico)  ─┐
Usuário → dashboard / API ─┴─ POST /pipeline  { script: "...", metadata: {...} }
       │
       ▼
  orchestrator
  ├─ cria pipeline_run no DB (status: pending)
  │
  ├─ 1. REFINAR → llm_service
  │     └─ melhora ganchos, CTAs, fluxo narrativo
  │     └─ classifica: público-alvo, tom, tipo de conteúdo
  │     └─ decide se divide em partes (e onde cortar com cliffhanger)
  │     └─ gera resumo das partes anteriores (para parte 2+)
  │
  ├─ [para cada parte do roteiro:]
  │   ├─ 2. TTS → tts_service
  │   │     └─ gera audio.mp3, salva no MinIO
  │   │
  │   └─ 3. RENDER → blender_worker
  │         └─ monta VSE (vídeo + áudio + legendas)
  │         └─ renderiza para output.mp4, salva no MinIO
  │
  └─ 4. AGENDAR → tiktok_poster
        └─ agenda 2 posts por dia
        └─ otimiza hashtags com base na classificação + histórico
        └─ publica e coleta métricas
```

---

## Estado do Pipeline

Cada `pipeline_run` no DB do orchestrador segue este ciclo:

```
pending
  → refining        (llm_service processando)
  → refined         (roteiro melhorado, classificação pronta)
  → [splitting]     (se dividido em partes)
  → tts_pending     (aguardando TTS — por parte)
  → tts_running
  → tts_done
  → render_pending  (aguardando blender_worker — por parte)
  → render_running
  → render_done
  → scheduling      (tiktok_poster agendando)
  → scheduled       (agendado, aguardando hora do post)
  → posted          (publicado)
  → failed          (erro em qualquer etapa, com mensagem)
```

Cada parte de uma série tem seu próprio sub-estado. O orchestrador só avança para o agendamento quando todas as partes estão `render_done`.

---

## Classificação de Conteúdo

O `llm_service` retorna um objeto de classificação junto com o roteiro refinado. O schema é flexível — cada tipo de conteúdo pode ter campos diferentes.

```json
{
  "content_type": "drama",
  "tone": "suspenseful",
  "target_audience": {
    "age_range": [15, 25],
    "gender": "female",
    "interests": ["relationships", "drama"]
  },
  "parts": 2,
  "split_rationale": "Cliffhanger no momento em que ela descobre as mensagens.",
  "cta_per_part": [
    "Comenta o que você acha que vai acontecer 👇",
    "Segue pra não perder o final 🔥"
  ],
  "hashtag_hints": ["#traição", "#relacionamento", "#dramadotiktok"]
}
```

O schema é armazenado como JSONB no DB do orchestrador. Novos campos são adicionados sem migração.

---

## Divisão em Partes

- O LLM recebe o roteiro e decide se ele cabe em um único vídeo (≤ ~60s de fala) ou precisa ser dividido.
- Se dividido, o LLM escolhe o ponto de corte que maximize a curiosidade (cliffhanger natural).
- Para partes 2+, o LLM gera um resumo curto ("Na parte anterior...") que é inserido no início do roteiro daquela parte antes de ir para o TTS.
- O orchestrador cria um `pipeline_part` por parte e processa cada uma em sequência.

---

## Legendas (word-level)

**Origem.** A legenda é derivada do áudio, não do roteiro. O `tts_service` transcreve o MP3 já gerado (e já com silêncios removidos) via `faster-whisper` com `word_timestamps=True`, e emite um SRT com **uma entrada por palavra**. Decisão: o roteiro e a narração divergem (o TTS abrevia, o corte de silêncio desloca o tempo), então estimar timing por WPM sempre dessincroniza. Transcrever o artefato final é a única fonte de verdade.

O SRT vai para `subs/{run_id}/part_{n}.srt` e o orchestrador só repassa a key — ele não gera mais SRT.

**Pré-requisito de timing — `fps_base`.** O FPS efetivo do Blender é `render.fps / render.fps_base`, e o `fps_base` é gravado no `.blend`. O `edit_video.py` define os dois (`fps = frame_rate`, `fps_base = 1.0`) para que "frame" no `template.json` e no SRT signifique a mesma coisa que na cena renderizada. Com o `fps_base` do template atual (`0.1`) e só o `fps` sobrescrito, a cena rodaria a 10× a velocidade pretendida e nenhum timing em frames bateria com o áudio.

**Renderização (`blender_worker`).** Cada entrada do SRT vira uma text strip no canal de legendas do VSE. Regras, todas em `build_subtitle_timeline`:

- **Offset de sincronia** — os timestamps do SRT são relativos ao início da narração, mas a voice strip começa em `speech_start + 1`. Toda entrada é deslocada por esse mesmo offset. Sem isso a legenda adianta pelo tamanho do intro.
- **Hold até a próxima palavra** — o fim de cada strip é estendido até o início da seguinte. Whisper deixa micro-vãos entre palavras; respeitá-los literalmente produz flicker.
- **Cap do hold (`max_hold_seconds`, padrão 0.4s)** — o hold não passa disso. Numa pausa real da narração a legenda sai da tela em vez de deixar a última palavra pendurada.
- **Sem overlap** — o fim é sempre clampado ao início da próxima. Duas strips sobrepostas no mesmo canal fazem o Blender realocar uma delas para outro canal, quebrando a composição.
- **Duração mínima de 1 frame** — palavras mais curtas que um frame existem; strip de duração zero é inválida.
- **Merge, nunca descarte** — duas palavras que caem no mesmo frame são concatenadas numa strip só. Nenhuma palavra é perdida silenciosamente.
- **Fade só na borda de vão** — `fade_frames` (padrão 3) se aplica apenas quando há vão real antes/depois, e na primeira e última strip. Entre palavras adjacentes a troca é corte seco: fade em cada palavra é justamente o que lê como piscada.
- **Fade nunca maior que ⅓ da strip** — em palavras curtas um fade fixo inverteria a ordem dos keyframes de `blend_alpha` (fade-out antes do fade-in), fazendo a palavra piscar ou nunca atingir opacidade cheia.
- **Rise em toda palavra** — cada palavra nasce `rise_offset` (padrão 0.025 da altura) abaixo da posição de repouso `SUBTITLE_Y` (0.05) e sobe até ela em `rise_frames` (padrão 4), com `SINE`/`EASE_OUT`. Decisão: o "pop" por palavra é o que dá ritmo à legenda, mas fazê-lo com opacidade pisca. Movendo a posição em vez da transparência, o efeito pode valer para **todas** as palavras — inclusive as coladas — sem o artefato visual. Fade e rise são ortogonais de propósito: o fade marca fronteira de silêncio, o rise marca troca de palavra.
- **Rise nunca maior que `duração - 1`** — a palavra precisa chegar à posição de repouso antes da strip acabar.
- **Interpolação fixada explicitamente** — keyframes novos herdam a preferência do Blender de quem executa (`keyframe_new_interpolation_type`); um dev com `CONSTANT` configurado veria um pulo em vez da subida. `_set_easing` grava `SINE`/`EASE_OUT` nos pontos após inserir.

**Tipografia.** A fonte, a cor e o contorno são resolvidos uma vez por job em `resolve_subtitle_style()` (função pura, sem `bpy`) e aplicados a cada strip por `apply_text_style()`.

- **Futura Bold como padrão, com fallback em cadeia** — `resolve_font_path()` testa, em ordem: `subtitles.font_path` do template → `assets/fonts/Futura-Bold.ttf` → `.otf` → `DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`, já na imagem). Se nada existir, retorna `None` e a strip fica com a fonte embutida do Blender. Decisão: fonte ausente é problema de estilo, não motivo para falhar um render que já consumiu LLM, TTS e transcrição — degrada o visual, nunca o job.
- **A fonte mora em `blender_worker/assets/fonts/`, não na raiz do monorepo** — o compose usa `build: ./blender_worker`, então o build context da imagem é o diretório do serviço. Um `.ttf` na raiz do monorepo é invisível para o `COPY . .` do Dockerfile e o container renderizaria em DejaVu sem nenhum erro visível. O nome do arquivo é case-sensitive no Linux (`Futura-Bold.ttf`).
- **Binários marcados no `.gitattributes`** — o repo é desenvolvido no Windows com `core.autocrlf=true`. Sem regra explícita, o git decide por heurística de conteúdo se converte newlines; um `.ttf` ou `.blend` convertido quebra em tempo de render, não em tempo de commit.
- **Datablock carregado uma vez** — `bpy.data.fonts.load(..., check_existing=True)` fora do loop. Um vídeo tem centenas de strips word-level; carregar por strip criaria centenas de datablocks duplicados no `.blend`.
- **Branco com contorno preto** — o fundo é vídeo em movimento, então não há cor de texto que funcione sozinha: texto branco desaparece em cena clara. O contorno resolve isso sem tarja/caixa atrás do texto, que roubaria área da tela num formato vertical.
- **`outline_width` padrão 0.24, não 0.05** — 0.05 é o padrão do Blender e renderiza como um fio de cabelo que some sobre fundo claro. 0.24 é o limite superior que ainda preserva as formas das letras: acima de ~0.30 os contornos se fundem entre glifos vizinhos e as contraformas das letras redondas começam a fechar, o que custa legibilidade na velocidade de uma palavra por vez. O valor é clampado em 0..1 na leitura do template (o Blender clampa em silêncio; clampar aqui evita que um valor errado renderize como outra coisa).
- **`font_size` não tem padrão no código** — quando ausente, o tamanho que o Blender deu à strip (60) é preservado, e 60 é pequeno demais para 1080×1920. O `template.json` define 140, que ocupa a largura útil sem encostar nas bordas. A decisão fica no template e não no código porque corpo é escolha de design por template, não invariante do pipeline.
- **Requer Blender 4.2+** — `use_outline`/`outline_color`/`outline_width` só existem a partir do 4.2 (versão fixada no Dockerfile). Em build anterior o script levanta `AttributeError` em vez de descartar o contorno silenciosamente: legenda sem contorno é ilegível, então falhar alto é o comportamento correto.
- **View transform importa** — o `template.blend` usa `Standard`, então branco 1.0 sai branco 1.0. Sob `AgX` (padrão de fábrica do Blender) o mesmo branco renderiza em ~0.78 e o contorno perde contraste. Um template novo precisa manter `Standard`.

**Configuração** — bloco opcional `subtitles` no `template.json`:

```json
"subtitles": {
  "fade_frames": 3,
  "max_hold_seconds": 0.4,
  "rise_frames": 4,
  "rise_offset": 0.025,
  "font_path": "assets/fonts/Futura-Bold.ttf",
  "font_size": 90,
  "color": [1.0, 1.0, 1.0, 1.0],
  "use_outline": true,
  "outline_color": [0.0, 0.0, 0.0, 1.0],
  "outline_width": 0.12
}
```

Cores aceitam `[r, g, b]` ou `[r, g, b, a]` (alfa assume 1.0); qualquer outro número de canais levanta `ValueError` na leitura do template, não no meio do render.

`fade_frames: 0` desliga o fade (corte seco); `rise_frames: 0` desliga a subida.

---

## Agendamento (tiktok_poster)

- Ritmo: 2 posts por dia.
- Séries: partes são agendadas em dias consecutivos, mesmo horário.
- O `tiktok_poster` é responsável por:
  - Escolher hashtags finais (com base na classificação + performance histórica)
  - Postar no horário agendado via TikTok API
  - Coletar métricas (views, likes, shares, watch time) após publicação
  - Armazenar métricas no próprio DB para informar decisões futuras de hashtag e horário
- O orchestrador delega completamente — só recebe confirmação de `scheduled` e `posted`.

---

## Trigger (entrada)

Duas portas de entrada, ambas terminando no mesmo `POST /pipeline`:

1. **Manual** — `POST /pipeline` no orchestrador com `{ "script": "...", "metadata": {} }`.
2. **Automática** — o `content_scout` descobre roteiros sozinho e chama o mesmo endpoint.

O trigger sempre normaliza para `plain text + metadata` antes de enviar ao orchestrador, independente da origem.

---

## Descoberta de conteúdo (content_scout)

### Escolha da fonte

**Reddit, via feeds RSS.** Foram avaliadas três opções:

| Opção | Veredito |
|---|---|
| Reddit RSS | **Escolhida.** Sem credencial, sem aprovação, fora da cláusula não-comercial da Data API |
| Reddit Data API (OAuth) | Descartada por ora. 100 req/min sobrariam, mas exige pré-aprovação e o free tier proíbe uso comercial |
| YouTube (baixar + transcrever) | Descartada como fonte de roteiro — ver abaixo |

O `.json` sem autenticação do Reddit foi desativado em maio/2026 e responde 403. Os feeds RSS continuam abertos.

**Por que o YouTube não vira roteiro.** A transcrição de um vídeo *é* o roteiro de outra pessoa — republicá-lo com outra voz é cópia, não inspiração. Além disso, visualizações medem o canal, a thumbnail e o algoritmo, não o texto: otimizar por elas é perseguir o proxy errado. E o custo é ordens de grandeza maior (download + Whisper por vídeo, contra texto já pronto). Se o YouTube entrar, entra como **minerador de tema** — `search.list` para descobrir assuntos em alta, e o `llm_service` escreve roteiro original a partir do tema. Sem download, sem transcrição, sem risco de cópia.

### Sinal de qualidade

O feed RSS **não carrega score**. Por isso pedimos `/r/{sub}/top/.rss?t=week`: a ordenação é feita pelo próprio Reddit e chega implícita na posição das entradas. É um sinal mais fraco que o upvote numérico, mas suficiente — o gargalo real é a fila de publicação, não a escassez de candidatos.

### Rate limit

Leitura não autenticada é limitada a aproximadamente **uma requisição por 40s** — a resposta traz `x-ratelimit-remaining: 0` e `x-ratelimit-reset: ~40` já na primeira chamada. Requisições em sequência fazem só o primeiro subreddit responder 200; o resto toma 429. Daí o espaçamento obrigatório entre subreddits (`request_delay_seconds`, padrão 60s — 45s ainda tomou 429 em teste real, a janela desliza). Cada subreddit extra custa uma janela por ciclo, então a lista deve conter só subs que rendem.

### Seleção entre candidatos

As fontes são buscadas e concatenadas na ordem do config. Pegar o começo dessa lista dava **todas** as vagas ao primeiro subreddit: medido ao vivo, as duas submissões vieram de `r/desabafos` enquanto `r/relacionamentos` contribuiu cinco candidatos e não ganhou nenhuma. Configurar mais subreddits era decorativo.

A seleção é por **rodízio entre origens** (`interleave_by_origin`), preservando o ranking interno de cada uma:

```
desabafos[0], relacionamentos[0], conselhos[0], desabafos[1], ...
```

Isso mantém o ranking do Reddit como critério — continuamos pegando o melhor *disponível* de cada — e garante variedade de origem e tom entre vídeos consecutivos. Combinado com o dedup, o rodízio entre ciclos emerge sozinho, sem estado de rotação persistido.

**Não há score composto** (posição × tamanho × recência). Sem upvotes reais, qualquer peso seria inventado. Quando `seen_items` acumular histórico de performance, dá para ranquear com base em evidência.

### Filtros

Duas etapas, separadas de propósito por custo e por natureza:

**1. Determinística (`filters.py`).** Tamanho nas duas pontas: curto demais não sustenta um vídeo; longo demais obrigaria o LLM a cortar tanto que o que vai ao ar já não é o post. Roda sobre todo candidato, é grátis.

**2. Moderação por LLM (`llm_service POST /moderate`).** Decide se publicar coloca a conta em risco de remoção.

A versão anterior era uma blocklist por substring, e ela errou de forma instrutiva: `me matar` casou dentro de `"Eram 3 mil que não me mataria"` — figura de linguagem sobre dinheiro — descartando uma história boa. Enquanto `"disseram que depois de me matar iam fazer com ela..."` é ameaça real e precisa ser barrada. **As duas contêm a mesma sequência de caracteres.** Segurança é julgamento de contexto, não casamento de padrão.

O prompt é explícito em aprovar histórias pesadas — término, traição, briga de família, demissão, dívida, luto — porque esse é o material do produto. O que barra é automutilação, abuso sexual, violência gráfica, ódio e conteúdo envolvendo menores.

**Custo.** Roda por publicação, não por post buscado: só nos candidatos que já passaram tamanho, dedup e ordenação, e apenas até o orçamento do ciclo encher. Duas a três chamadas por ciclo em vez de ~30. Modelo configurado em `LLM_MODERATION_MODEL`, separado do modelo de refino — a chamada é um sim/não.

**Falha de moderação não é veredito.** `ModerationError` é distinto de `safe=false`: o candidato **não** é gravado em `seen_items`, o ciclo encerra, e a história continua disponível depois. Uma indisponibilidade não pode nem publicar sem checagem, nem queimar história boa em definitivo.

### Dedup e auditoria

`seen_items` guarda **todo** candidato avaliado — inclusive os rejeitados, com o motivo. Serve a dois propósitos: impedir que a mesma história vire um segundo vídeo quando reaparece no top da semana seguinte, e permitir calibrar os limiares contra dados reais em vez de chute.

### Backpressure

Antes de submeter, o scout conta os runs ativos no orchestrador (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se atingiu `max_pending_runs`, o ciclo não submete nada.

A razão é a fila do Buffer, que segura 10 posts: ingerir mais rápido do que se publica não gera mais vídeos, só converte roteiro bom em run falho. Enquanto a feature "Fila de espera quando o Buffer está cheio" (ver Backlog) não existir, o backpressure é a única proteção contra isso.

**Ordem das etapas.** A capacidade é verificada *depois* de registrar os filtrados e *antes* de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o ciclo seguinte parte de uma pilha menor.

### Periodicidade

Loop `asyncio` iniciado no `lifespan` do serviço, intervalo configurável. Não precisa de scheduler durável — diferente do retry do Buffer — porque `seen_items` torna o ciclo idempotente: um restart no pior caso repete uma passagem que não encontra nada novo.

### Adicionando fontes

Toda fonte implementa o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) e devolve `Candidate` com `external_id` estável, que é a chave de dedup. Dedup, filtros, orçamento e backpressure tratam todas as fontes igualmente.

---

## Tech Stack por Serviço

| Serviço | Stack |
|---|---|
| `orchestrator` | FastAPI + SQLAlchemy + PostgreSQL |
| `llm_service` | FastAPI + OpenRouter / Claude API / Chutes AI (configurável por env) |
| `tts_service` | FastAPI + edge-tts (→ ElevenLabs futuramente) |
| `blender_worker` | FastAPI + Blender 4.2 LTS + Pillow (existente) |
| `tiktok_poster` | FastAPI + TikTok API |
| `content_scout` | FastAPI + SQLAlchemy + PostgreSQL + httpx |
| `dashboard` | HTML/JS servido pelo orchestrador (MVP) |

---

## Fluxo de Arquivos (MinIO)

```
tts_service     → audio/{pipeline_run_id}/part_{n}.mp3
                → subs/{pipeline_run_id}/part_{n}.srt   (legenda word-level, via Whisper)
blender_worker  → outputs/{job_id}.mp4   (vídeo renderizado)
                → outputs/{job_id}.blend  (cena Blender montada, para inspeção/reuso)
```

O orchestrador armazena as keys MinIO de cada artefato no `pipeline_run` para passá-las para os próximos serviços.

---

## Backlog de Features

Features planejadas, ainda não implementadas. Cada entrada descreve o problema, o comportamento proposto e o que precisa mudar — o suficiente para uma sessão futura implementar sem redescobrir o contexto.

### Fila de espera quando o Buffer está cheio

**Problema.** O Buffer free aceita no máximo 10 posts agendados por vez (`config.ini [posting] buffer_queue_limit`). Quando o limite é atingido, `POST /schedule` no `tiktok_poster` responde `429` com `{"error": "buffer_queue_full"}`, o orchestrador marca o `pipeline_run` como `failed` e o vídeo — já renderizado, já pago em tempo de LLM, TTS e render — fica parado. A recuperação hoje é manual: esperar a fila baixar e resubmeter.

Isso é o único ponto do pipeline onde uma condição **temporária e esperada** produz uma falha terminal. Com 2–3 posts/dia e séries de múltiplas partes, a fila enche em poucos dias de operação normal.

**Comportamento proposto.** Fila cheia deixa de ser falha e passa a ser espera:

- O orchestrador ganha o estado `awaiting_slot` — o run terminou render, tem os vídeos no MinIO, e só aguarda vaga no Buffer. Distinto de `failed`: nada deu errado.
- Um worker periódico varre os runs em `awaiting_slot` (ordem FIFO por `created_at`) e retenta o agendamento. Ao conseguir, o run segue para `scheduled` normalmente.
- Séries são atômicas: só agenda se houver vaga para **todas** as partes restantes. Agendar a parte 1 e deixar a parte 2 na espera publica um cliffhanger sem continuação.
- O intervalo de varredura é configurável; algo na ordem de horas é suficiente — a fila só abre quando o Buffer publica.

**O que muda:**

| Onde | Mudança |
|---|---|
| `orchestrator/db/models.py` | novo valor `awaiting_slot` em `PipelineStatus` + migration |
| `orchestrator/worker.py` | `_schedule` trata `429 buffer_queue_full` como `awaiting_slot`, não `failed` |
| `orchestrator` (novo) | worker de retry periódico varrendo `awaiting_slot` |
| `tiktok_poster/api/routes/schedule.py` | expor vagas livres na resposta do `429`, para o orchestrador decidir sobre séries sem tentativa e erro |
| `docs/product.md` | reescrever "Fila cheia" na Feature 6 — deixa de ser falha |

**Pré-requisito.** O retry periódico exige um agendador que sobreviva a restart do container. Hoje o orchestrador usa `BackgroundTasks`, que não serve — a mesma limitação já registrada como tech debt no `blender_worker`. Resolver os dois juntos (fila real: Celery + Redis, ou APScheduler com store no Postgres).

**Alternativas consideradas.**

- **Buffer pago** — resolve por dinheiro (limite muito maior), não resolve o caso de a fila encher mesmo assim. Vale como mitigação, não como solução.
- **Publicar direto na TikTok Content Posting API** — elimina o Buffer e o limite de fila, mas troca um problema por outro: OAuth, refresh de token, e o agendamento passa a ser responsabilidade nossa. Ver "TikTok API" em Decisões em Aberto. A fila de espera é útil de qualquer forma, porque o limite de ritmo (posts/dia) continua existindo.

**Critério de aceite.** Submeter runs além do limite da fila: nenhum vai para `failed`, todos ficam em `awaiting_slot`, e cada um é agendado sozinho conforme a fila abre — sem resubmissão manual e sem perder a ordem.

---

## Decisões em Aberto

- **Schema de classificação por tipo de conteúdo**: o LLM recebe um schema fixo ou gera livremente e o orchestrador valida? → Definir quando implementar o llm_service.
- **TikTok API**: autenticação OAuth vs. token estático de longa duração → Definir quando implementar o tiktok_poster.
- **Dashboard**: servido pelo orchestrador (FastAPI + Jinja2) ou container Next.js separado → MVP usa Jinja2, pode migrar depois.
- **Retry automático**: se TTS ou render falhar, o orchestrador retenta automaticamente ou só marca como `failed`? → MVP marca como failed. O caso de fila cheia do Buffer é diferente (falha temporária, não erro) e já tem solução desenhada em "Fila de espera quando o Buffer está cheio", no Backlog de Features.
