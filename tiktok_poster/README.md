# tiktok_poster

Serviço de agendamento de vídeos no TikTok via Buffer. Recebe o vídeo renderizado do orchestrador, calcula o próximo slot disponível, monta a caption com hashtags e agenda o post.

---

## Setup local

**Pré-requisitos:** Python 3.11+, Poetry.

```bash
cd tiktok_poster
cp .env.example .env   # preencher com as variáveis abaixo
poetry install

# Iniciar (porta 8004)
python main.py
```

Ou via Docker:

```bash
# na raiz do monorepo
docker compose up tiktok_poster
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ROOT_DIR` | Sim | Caminho absoluto para a raiz do serviço |
| `ENV` | Não | `dev` (padrão) ou `prod` |
| `BUFFER_ACCESS_TOKEN` | Sim | Token de acesso da API do Buffer |
| `BUFFER_PROFILE_ID` | Sim | ID do canal TikTok no Buffer (formato: `69fc...`) |
| `BUFFER_ORG_ID` | Não | ID da organização no Buffer (auto-detectado se omitido) |
| `MINIO_ENDPOINT` | Sim | Endpoint do MinIO ou Cloudflare R2 |
| `MINIO_ACCESS_KEY` | Sim | Access key do MinIO/R2 |
| `MINIO_SECRET_KEY` | Sim | Secret key do MinIO/R2 |
| `R2_PUBLIC_URL` | Em produção | URL pública do bucket R2 (ex: `https://pub-xxx.r2.dev`). Quando definida, substitui URLs pré-assinadas por links públicos diretos. |

**Como obter o `BUFFER_PROFILE_ID`:** acesse `https://publish.buffer.com/channels` — o ID está na URL ao selecionar o canal TikTok.

**Nota sobre storage em produção:** o Buffer precisa baixar o vídeo via URL pública. Com MinIO local o download não funciona. Use Cloudflare R2 com `R2_PUBLIC_URL` configurada.

---

## Configuração de agendamento (`config.ini`)

```ini
[posting]
posts_per_day = 3          # máximo de posts por dia
preferred_times = 14:00,18:00,22:00  # horários UTC = 11h, 15h e 19h em Brasília
presigned_url_ttl = 21600  # TTL da URL em segundos (6h)
buffer_queue_limit = 10    # limite de posts na fila (Buffer free: 10)

[hashtags]
mandatory = #tiktokbrasil,#fyp  # sempre incluídas
max_total = 8              # total máximo de hashtags por post
```

O pool de hashtags de fallback fica em `hashtags.json` na raiz do serviço.

---

## Endpoints

### GET /health

Verifica conexão com o Buffer.

```bash
curl http://localhost:8004/health
```

```json
{ "status": "ok", "checks": { "buffer": "ok" } }
```

---

### POST /schedule — Agendar vídeo

```bash
curl -X POST http://localhost:8004/schedule \
  -H "Content-Type: application/json" \
  -d '{
    "video_key": "outputs/34319ef6-fb57-4c32-9e9e-924f074dfd92/part_1.mp4",
    "classification": {
      "content_type": "educativo",
      "tone": "shocking",
      "hashtag_hints": ["#ciencia", "#curiosidades", "#efeitompemba"],
      "cta_per_part": ["Comenta se você já ouviu falar disso 👇"],
      "parts": 1
    },
    "part_number": 1,
    "series_id": "34319ef6-fb57-4c32-9e9e-924f074dfd92"
  }'
```

```json
{
  "scheduled_at": "2026-05-08T08:00:00+00:00",
  "buffer_update_id": "abc123xyz"
}
```

**Erros:**
- `429` — fila do Buffer atingiu o limite configurado

```json
{
  "detail": {
    "error": "buffer_queue_full",
    "message": "Buffer queue has reached the 10-post limit. Retry later.",
    "pending_count": 10
  }
}
```

---

## Rodar testes

Buffer e MinIO são sempre mockados. Sem DB.

```bash
cd tiktok_poster
poetry run pytest
```

21 testes em 3 arquivos: `tests/test_hashtags.py`, `tests/test_scheduler.py`, `tests/test_schedule.py`.
