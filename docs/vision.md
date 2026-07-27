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
| `dashboard` | 3000 | Interface web simples para submissão e monitoramento |
| `db` (infra) | 5433 | PostgreSQL compartilhado entre serviços |
| `minio` (infra) | 9000 | Armazenamento de arquivos (áudio, vídeo, assets) |

---

## Pipeline Completo

```
Usuário → dashboard / API
  └─ POST /pipeline  { script: "...", metadata: {...} }
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

- **Futura Bold como padrão, com fallback em cadeia** — Futura é licenciada e não é redistribuída no repo. `resolve_font_path()` testa, em ordem: `subtitles.font_path` do template → `assets/fonts/Futura-Bold.ttf` → `.otf` → `DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`, já na imagem). Se nada existir, retorna `None` e a strip fica com a fonte embutida do Blender. Decisão: fonte ausente é problema de estilo, não motivo para falhar um render que já consumiu LLM, TTS e transcrição — degrada o visual, nunca o job.
- **Datablock carregado uma vez** — `bpy.data.fonts.load(..., check_existing=True)` fora do loop. Um vídeo tem centenas de strips word-level; carregar por strip criaria centenas de datablocks duplicados no `.blend`.
- **Branco com contorno preto** — o fundo é vídeo em movimento, então não há cor de texto que funcione sozinha: texto branco desaparece em cena clara. O contorno resolve isso sem tarja/caixa atrás do texto, que roubaria área da tela num formato vertical.
- **`outline_width` padrão 0.12, não 0.05** — 0.05 é o padrão do Blender e renderiza como um fio de cabelo que some sobre fundo claro. 0.12 é a menor espessura que ainda separa o texto do fundo sem virar contorno de adesivo. O valor é clampado em 0..1 na leitura do template (o Blender clampa em silêncio; clampar aqui evita que um valor errado renderize como outra coisa).
- **`font_size` não tem padrão** — quando ausente, o tamanho que o Blender deu à strip é preservado. Tipografia e corpo são decisões separadas; definir um padrão aqui redimensionaria todo render existente.
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

Por enquanto: `POST /pipeline` no orchestrador com `{ "script": "...", "metadata": {} }`.

Futuramente o trigger pode ser expandido para receber:
- Posts do Reddit (URL → scraping → extração de texto)
- Vídeos do YouTube (URL → transcrição)
- Outros formatos

Em todos os casos, o trigger sempre normaliza para `plain text + metadata` antes de enviar ao orchestrador. Quando o trigger virar serviço próprio, ele expõe a mesma interface para o orchestrador.

---

## Tech Stack por Serviço

| Serviço | Stack |
|---|---|
| `orchestrator` | FastAPI + SQLAlchemy + PostgreSQL |
| `llm_service` | FastAPI + OpenRouter / Claude API / Chutes AI (configurável por env) |
| `tts_service` | FastAPI + edge-tts (→ ElevenLabs futuramente) |
| `blender_worker` | FastAPI + Blender 4.2 LTS + Pillow (existente) |
| `tiktok_poster` | FastAPI + TikTok API |
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

## Decisões em Aberto

- **Schema de classificação por tipo de conteúdo**: o LLM recebe um schema fixo ou gera livremente e o orchestrador valida? → Definir quando implementar o llm_service.
- **TikTok API**: autenticação OAuth vs. token estático de longa duração → Definir quando implementar o tiktok_poster.
- **Dashboard**: servido pelo orchestrador (FastAPI + Jinja2) ou container Next.js separado → MVP usa Jinja2, pode migrar depois.
- **Retry automático**: se TTS ou render falhar, o orchestrador retenta automaticamente ou só marca como `failed`? → MVP marca como failed.
