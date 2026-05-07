# CLAUDE.md — content_engine

Guia para o Claude Code no monorepo `content_engine`.

## Estrutura do Monorepo

```
content_engine/
  docker-compose.yml        ← orquestra toda a infra + serviços
  .env.example              ← vars de todos os serviços
  docs/
    vision.md               ← visão geral do sistema, pipeline, decisões
  orchestrator/             ← coordenação do pipeline (FastAPI + DB próprio)
  blender_worker/           ← montagem VSE + renderização (FastAPI + Blender 4.2)
  llm_service/              ← refinamento e classificação de roteiros
  tts_service/              ← geração de áudio (edge-tts → ElevenLabs)
  tiktok_poster/            ← publicação, agendamento e analytics do TikTok
```

Cada serviço tem seu próprio `Dockerfile`, `pyproject.toml`, `CLAUDE.md` e `docs/`.

## Pipeline

Leia `docs/vision.md` antes de qualquer trabalho que envolva mais de um serviço. O pipeline é:

```
Trigger (plain text) → orchestrator → llm_service → tts_service → blender_worker → tiktok_poster
```

O orchestrador é o único serviço que conhece o fluxo completo. Os demais são stateless em relação ao pipeline — recebem uma tarefa, executam, retornam resultado.

## Regras de desenvolvimento

- Antes de qualquer commit: `git fetch origin && git status`. Se estiver atrás, fazer rebase primeiro.
- Mudanças que afetam a interface entre serviços (request/response schemas) devem ser documentadas em `docs/vision.md` antes de serem implementadas.
- Cada novo serviço deve ter seu próprio `CLAUDE.md` antes de começar a implementação.
- Quando um serviço do monorepo for alterado, sincronizar a alteração no repo individual correspondente (se existir).

## Comandos

```bash
# Subir toda a stack
docker compose up --build

# Subir só a infra (db + minio)
docker compose up db minio

# Subir serviços específicos
docker compose up db minio orchestrator llm_service

# Ver logs de um serviço
docker compose logs -f orchestrator
```

## Portas (localhost)

| Serviço | Porta |
|---|---|
| orchestrator | 8000 |
| blender_worker | 8001 |
| llm_service | 8002 |
| tts_service | 8003 |
| tiktok_poster | 8004 |
| PostgreSQL | 5433 |
| MinIO API | 9000 |
| MinIO Console | 9001 |

## Comunicação entre serviços

Internamente (dentro do Docker network), os serviços se comunicam pelo nome do container:
- `http://orchestrator:8000`
- `http://blender_worker:8000`
- `http://llm_service:8000`
- `http://tts_service:8000`
- `http://tiktok_poster:8000`

Externamente (localhost), cada um usa a porta mapeada acima.

## Estado do projeto

- `blender_worker` — implementado (MVP completo: API, DB, Blender pipeline, image compositor)
- `orchestrator` — planejado, em implementação
- `llm_service` — planejado
- `tts_service` — planejado
- `tiktok_poster` — planejado

## Decisões em aberto

Ver seção "Decisões em Aberto" em `docs/vision.md`.
