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
