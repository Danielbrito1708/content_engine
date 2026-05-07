# blender_worker

Worker de automação de conteúdo. Expõe uma API REST com dois pipelines independentes:

- **Montagem de vídeo** — recebe assets (vídeo, música, voz, legendas), monta as trilhas no Blender 4.2 LTS e salva o `.blend` resultante no MinIO.
- **Geração de imagem** — gera cards de comentário (fundo + assets posicionados + texto) com Pillow e salva o PNG no MinIO.

## Requisitos

- [Docker](https://docs.docker.com/get-docker/) e [Docker Compose](https://docs.docker.com/compose/)
- [Python 3.11+](https://www.python.org/downloads/) e [Poetry](https://python-poetry.org/docs/#installation) (apenas para desenvolvimento local)
- Git

## Rodando com Docker (recomendado)

### 1. Clone o repositório

```bash
git clone git@github.com:Danielbrito1708/blender_editor.git
cd blender_editor
```

### 2. Configure o `.env`

```bash
cp .env.example .env
```

Edite o `.env` e ajuste `ROOT_DIR` para o caminho absoluto do projeto:

```env
ROOT_DIR=/caminho/absoluto/para/blender_editor
ENV=dev
DEBUG=true
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/blender_worker
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=blender-jobs
```

> As URLs de `DATABASE_URL` e `MINIO_ENDPOINT` usam `localhost` para acesso local. Dentro do Docker, as variáveis são sobrescritas automaticamente pelo `docker-compose.yml`.

### 3. Suba o stack

```bash
docker compose up --build
```

O primeiro build baixa o Blender 4.2 LTS (~250MB) — pode demorar alguns minutos.

Serviços disponíveis após o start:

| Serviço | URL |
|---|---|
| API | http://localhost:8000 |
| Docs (Swagger) | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |
| PostgreSQL | localhost:5432 |

Login do MinIO: `minioadmin` / `minioadmin`

> **Atenção:** o bucket MinIO (`blender-jobs`) precisa ser criado manualmente na primeira vez:
> ```bash
> docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
> docker compose exec minio mc mb local/blender-jobs
> ```

---

## Endpoints disponíveis

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status do DB e MinIO |
| `POST` | `/videos` | Registrar assets de um vídeo |
| `GET` | `/videos/{id}` | Buscar vídeo |
| `POST` | `/templates` | Registrar template Blender |
| `GET` | `/templates/{id}` | Buscar template |
| `POST` | `/jobs` | Criar job de montagem (async) |
| `GET` | `/jobs/{id}` | Consultar status do job |
| `POST` | `/images/render` | Gerar comment card PNG (síncrono) |

Documentação interativa: http://localhost:8000/docs

---

## Desenvolvimento local (sem Docker)

### 1. Instale as dependências

```bash
poetry install
```

### 2. Suba apenas a infraestrutura

```bash
docker compose up db minio
```

### 3. Aplique as migrations

```bash
poetry run alembic upgrade head
```

### 4. Rode a aplicação

```bash
python main.py
```

---

## Criando e renderizando um vídeo

Este guia usa os arquivos de exemplo que já existem no repositório (`template.blend`, `template.json`).

### 1. Faça o upload dos assets para o MinIO

O script `scripts/upload_to_minio.py` usa boto3 para enviar arquivos ao bucket `blender-jobs`.

```bash
# Templates
python scripts/upload_to_minio.py template.blend templates/template.blend
python scripts/upload_to_minio.py template.json templates/template.json

# Assets do vídeo (substitua pelos seus arquivos)
python scripts/upload_to_minio.py /caminho/para/video.mp4    videos/ep01/video.mp4
python scripts/upload_to_minio.py /caminho/para/musica.mp3   videos/ep01/musica.mp3
python scripts/upload_to_minio.py /caminho/para/voz.mp3      videos/ep01/voz.mp3
python scripts/upload_to_minio.py /caminho/para/legendas.srt videos/ep01/legendas.srt
```

> Com Poetry: `poetry run python scripts/upload_to_minio.py ...`

### 2. Registre o template

```bash
curl -X POST http://localhost:8000/templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Reels 30s",
    "blend_key": "templates/template.blend",
    "json_key": "templates/template.json"
  }'
```

Guarde o `id` retornado — é o `TEMPLATE_ID`.

### 3. Registre o vídeo

```bash
curl -X POST http://localhost:8000/videos \
  -H "Content-Type: application/json" \
  -d '{
    "video_file_key": "videos/ep01/video.mp4",
    "music_key": "videos/ep01/musica.mp3",
    "voice_key": "videos/ep01/voz.mp3",
    "subtitle_key": "videos/ep01/legendas.srt"
  }'
```

Guarde o `id` retornado — é o `VIDEO_ID`.

### 4. Crie o job de renderização

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "VIDEO_ID",
    "template_id": "TEMPLATE_ID"
  }'
```

O job retorna com `status: pending` e começa a processar em background.

### 5. Acompanhe o status

```bash
curl http://localhost:8000/jobs/JOB_ID
```

Ciclo de vida: `pending` → `running` → `completed` / `failed`

Quando `completed`, o campo `output_key` contém o caminho do `.mp4` no MinIO, ex:

```json
{
  "status": "completed",
  "output_key": "outputs/JOB_ID.mp4"
}
```

### 6. Baixe o vídeo

Acesse o console do MinIO em http://localhost:9001 (usuário: `minioadmin`, senha: `minioadmin`), navegue até o bucket `blender-jobs` → pasta `outputs/` e baixe o arquivo.

---

## Gerando um comment card

```bash
curl -X POST http://localhost:8000/images/render \
  -H "Content-Type: application/json" \
  -d '{
    "template": "comment_default",
    "text": "Esse produto é incrível! Super recomendo para quem quer qualidade.",
    "assets": {
      "avatar": "uploads/user42/avatar.png"
    }
  }'
```

Resposta:
```json
{ "output_key": "renders/677b1bef-8c67-4a05-98b2-06b0f42d997d.png" }
```

Baixe o PNG pelo console do MinIO em http://localhost:9001 → bucket `blender-jobs` → pasta `renders/`.

---

## Testes

O stack completo precisa estar rodando (`docker compose up db minio`).

```bash
poetry run pytest
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ROOT_DIR` | Sim | Caminho absoluto para a raiz do projeto |
| `ENV` | Não | `dev` (padrão) ou `prod` |
| `DEBUG` | Não | `true` ou `false` |
| `DATABASE_URL` | Sim | Connection string asyncpg do Postgres |
| `MINIO_ENDPOINT` | Sim | URL do MinIO (ex: `http://localhost:9000`) |
| `MINIO_ACCESS_KEY` | Sim | Access key do MinIO |
| `MINIO_SECRET_KEY` | Sim | Secret key do MinIO |
| `MINIO_BUCKET` | Sim | Nome do bucket (ex: `blender-jobs`) |

---

## Documentação

- [`docs/vision.md`](docs/vision.md) — visão geral e fluxos do sistema
- [`docs/features.md`](docs/features.md) — descrição detalhada de cada feature
