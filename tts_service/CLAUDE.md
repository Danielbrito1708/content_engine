# CLAUDE.md — tts_service

Serviço de geração de áudio a partir de texto. Expõe `POST /generate` que o orchestrador chama após o refinamento do roteiro. Gera o áudio e faz upload direto ao MinIO.

## Arquitetura

- `api/routes/generate.py` — endpoint principal
- `api/routes/health.py` — `GET /health` com provider e voice ativos
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

Erros: `502` se o TTS falhar ou se o upload ao MinIO falhar.

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

## Testes

10 testes em `tests/test_generate.py`. edge-tts e MinIO são sempre mockados. Sem DB.

```bash
poetry run pytest
```
