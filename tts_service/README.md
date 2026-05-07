# tts_service

Serviço de geração de áudio a partir de texto. Recebe o roteiro refinado de cada parte, gera um MP3 via TTS e faz upload direto ao MinIO/R2.

---

## Setup local

**Pré-requisitos:** Python 3.11+, Poetry.

```bash
cd tts_service
cp .env.example .env   # preencher com as variáveis abaixo
poetry install

# Iniciar (porta 8003)
python main.py
```

Ou via Docker:

```bash
# na raiz do monorepo
docker compose up tts_service
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ROOT_DIR` | Sim | Caminho absoluto para a raiz do serviço |
| `ENV` | Não | `dev` (padrão) ou `prod` |
| `MINIO_ENDPOINT` | Sim | Endpoint do MinIO ou Cloudflare R2 |
| `MINIO_ACCESS_KEY` | Sim | Access key do MinIO/R2 |
| `MINIO_SECRET_KEY` | Sim | Secret key do MinIO/R2 |
| `MINIO_BUCKET` | Sim | Nome do bucket (ex: `blender-jobs`) |
| `TTS_PROVIDER` | Não | `edge` (padrão) ou `elevenlabs` |
| `TTS_VOICE` | Não | Voz do edge-tts (padrão: `pt-BR-ThalitaNeural`) |
| `ELEVENLABS_API_KEY` | Se `TTS_PROVIDER=elevenlabs` | API key do ElevenLabs |
| `ELEVENLABS_VOICE_ID` | Se `TTS_PROVIDER=elevenlabs` | ID da voz no ElevenLabs |

**Vozes PT-BR disponíveis no provider `edge`:**
- `pt-BR-ThalitaNeural` — feminina, jovem (padrão)
- `pt-BR-FranciscaNeural` — feminina
- `pt-BR-AntonioNeural` — masculino

---

## Endpoints

### GET /health

Retorna o provider e voz ativos.

```bash
curl http://localhost:8003/health
```

```json
{ "status": "ok", "provider": "edge", "voice": "pt-BR-ThalitaNeural" }
```

---

### POST /generate — Gerar áudio

```bash
curl -X POST http://localhost:8003/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Você não vai acreditar nisso: a água quente congela mais rápido que a fria!",
    "run_id": "34319ef6-fb57-4c32-9e9e-924f074dfd92",
    "part_number": 1
  }'
```

```json
{ "audio_key": "audio/34319ef6-fb57-4c32-9e9e-924f074dfd92/part_1.mp3" }
```

O arquivo MP3 é salvo diretamente no MinIO/R2. A `audio_key` retornada é usada pelo orchestrador para repassar ao blender_worker.

**Erros:**
- `502` — falha na geração TTS ou no upload ao MinIO/R2

---

## Rodar testes

edge-tts e MinIO são sempre mockados. Sem DB.

```bash
cd tts_service
poetry run pytest
```

10 testes em `tests/test_generate.py`.
