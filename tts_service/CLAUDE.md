# CLAUDE.md — tts_service

Serviço de geração de áudio a partir de texto. Expõe `POST /generate` que o orchestrador chama após o refinamento do roteiro. Gera o áudio e faz upload direto ao MinIO.

## Arquitetura

- `api/routes/generate.py` — endpoint principal
- `api/routes/health.py` — `GET /health` com provider, voice e rate ativos
- `tts/base.py` — `BaseTTSClient` com método abstrato `generate(text) -> bytes`
- `tts/edge.py` — `EdgeTTSClient` (edge-tts, Microsoft Neural TTS, gratuito)
- `tts/elevenlabs.py` — stub para implementação futura
- `tts/factory.py` — `get_tts_client()` seleciona o provider via `TTS_PROVIDER` env var
- `schemas/generate.py` — `GenerateRequest`, `GenerateResponse`
- `storage/client.py` — `upload_audio(bucket, key, data)` via boto3 + MinIO

## Features

### Endpoint de geração (`src/tts_service/api/routes/generate.py`)

`POST /generate` — gera áudio MP3 e salva no MinIO.

**Request** (`GenerateRequest`):
- `text` (str) — texto a ser narrado
- `run_id` (str) — UUID do pipeline run (usado na key MinIO)
- `part_number` (int) — número da parte (1, 2, ...)

**Response** (`GenerateResponse`):
- `audio_key` (str) — key MinIO do áudio gerado: `audio/{run_id}/part_{part_number}.mp3`
- `srt_key` (str) — key MinIO da legenda word-level: `subs/{run_id}/part_{part_number}.srt`

**Ordem das etapas:** TTS → remoção de silêncios → upload do áudio → transcrição → upload do SRT. A transcrição roda **depois** do corte de silêncio, sobre o mesmo áudio que vai ao vídeo — é o que mantém a legenda em sincronia.

Erros: `502` se o TTS, o upload ao MinIO ou a transcrição falharem.

### Providers TTS (`src/tts_service/tts/`)

Selecionado por `TTS_PROVIDER` env var:

| Provider | Env var | Status |
|---|---|---|
| `edge` (padrão) | `TTS_VOICE` | Implementado |
| `elevenlabs` | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | Stub (NotImplementedError) |

**Vozes PT-BR disponíveis no edge-tts:**
- `pt-BR-ThalitaNeural` (padrão — feminina, jovem)
- `pt-BR-FranciscaNeural` (feminina)
- `pt-BR-AntonioNeural` (masculino)

Voz configurada por `TTS_VOICE` env var.

### Velocidade da narração (`TTS_RATE`)

Acelera (ou desacelera) a narração na **própria síntese**, via `rate` do `edge_tts.Communicate` — que é `prosody rate` do SSML. O pitch fica intacto, diferente de acelerar o MP3 depois (resample deixa a voz aguda).

| Var | Padrão | Descrição |
|---|---|---|
| `TTS_RATE` | `+15%` | Percentual **com sinal** sobre o ritmo natural da voz |

Formato obrigatório: `^[+-]\d+%$` (`+15%`, `-10%`, `+0%` para desligar). Formato inválido **derruba o boot** em `TTSEnvSettings._check_rate_format` — falhar no start é melhor do que o edge-tts rejeitar na hora de sintetizar, no meio de uma request.

`EdgeTTSClient(voice=..., rate=...)` aceita override explícito; sem argumento, usa `settings.env.tts_rate`.

Como o rate age antes de tudo, a remoção de silêncio e a transcrição já rodam sobre o áudio acelerado — **o SRT sai sincronizado sem nenhum ajuste** e o `blender_worker` não muda.

O `elevenlabs.py` (stub) ainda não implementa rate; quando for implementado, o equivalente é o parâmetro `speed` do voice settings.

### Remoção de silêncios (`src/tts_service/audio/silence.py`)

Pós-processamento aplicado ao áudio gerado pelo TTS antes do upload ao MinIO. Remove silêncios do início, do fim e internos (longos demais) do MP3.

**Função pública:**
- `remove_silence(audio_bytes, min_silence_ms, silence_thresh_db, padding_ms) -> bytes` — recebe MP3 em bytes, devolve MP3 processado em bytes. Puro, sem efeitos colaterais.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `REMOVE_SILENCE` | `true` | Habilita/desabilita |
| `SILENCE_THRESH_DB` | `-40` | Nível abaixo do qual é considerado silêncio |
| `MIN_SILENCE_MS` | `500` | Duração mínima para um silêncio ser removido |
| `SILENCE_PADDING_MS` | `100` | Margem de silêncio preservada nas bordas dos cortes |

Falhas na remoção são logadas como warning e o áudio original é usado (sem interromper o pipeline).

**Dependências:** `pydub` + `ffmpeg` (adicionado ao Dockerfile).

### Transcrição / legenda word-level (`src/tts_service/audio/transcribe.py`)

Transcreve o áudio final (já sem silêncios) e gera o SRT que o `blender_worker` usa como legenda. Substituiu o SRT com timing estimado que o orchestrador gerava a partir do roteiro.

**Função pública:**
- `transcribe_to_srt(audio_bytes, language="pt", model_name="base") -> bytes` — recebe MP3 em bytes, devolve SRT em bytes com **uma entrada por palavra**.

Usa `faster-whisper` com `word_timestamps=True` (device `cpu`, `compute_type="int8"`). O modelo é carregado uma vez e reaproveitado num cache global (`_model`) — a primeira request paga o download/load.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `WHISPER_MODEL` | `base` | Tamanho do modelo (`tiny`, `base`, `small`, `medium`, ...) |
| `WHISPER_LANGUAGE` | `pt` | Idioma da transcrição |

Modelos maiores alinham as palavras melhor, ao custo de CPU. Falha na transcrição **derruba a request** com `502` (diferente da remoção de silêncio, que degrada silenciosamente).

O consumo dessa legenda (offset de sincronia, hold entre palavras, fades) é responsabilidade do `blender_worker` — ver `docs/vision.md` na raiz do monorepo.

**Sem testes** — `transcribe.py` não tem cobertura (exigiria mockar `WhisperModel` ou fixture de áudio real).

## Testes

`tests/test_generate.py` — 10 testes; edge-tts e MinIO sempre mockados; `REMOVE_SILENCE=false` no conftest (silence removal não afeta os testes do endpoint).

`tests/test_silence.py` — 10 testes; testa a função `remove_silence` diretamente com áudio gerado por pydub + 3 testes de integração com o endpoint. Requer `ffmpeg` instalado.

`tests/test_rate.py` — 16 testes; `edge_tts.Communicate` mockado. Cobre o repasse do `rate` na síntese, o override explícito no construtor, a validação de formato do `TTS_RATE` (via `TTSEnvSettings()` direto, que relê o env) e o campo `rate` no `/health`.

```bash
poetry run pytest
```
