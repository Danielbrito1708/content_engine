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
  content_scout/            ← descoberta automática de roteiros na internet (Reddit)
```

Cada serviço tem seu próprio `Dockerfile`, `pyproject.toml`, `CLAUDE.md` e `docs/`.

## Pipeline

Leia `docs/vision.md` antes de qualquer trabalho que envolva mais de um serviço. O pipeline é:

```
content_scout (automático) ─┐
Trigger manual (plain text) ─┴→ orchestrator → llm_service → tts_service → blender_worker → tiktok_poster
```

O orchestrador é o único serviço que conhece o fluxo completo. Os demais são stateless em relação ao pipeline — recebem uma tarefa, executam, retornam resultado.

## Regras de desenvolvimento

- Antes de qualquer commit: `git fetch origin && git status`. Se estiver atrás, fazer rebase primeiro: `git pull --rebase origin main`.
- Nunca adicionar linhas `Co-Authored-By` em mensagens de commit.
- Mudanças que afetam a interface entre serviços (request/response schemas) devem ser documentadas em `docs/vision.md` antes de serem implementadas.
- Cada novo serviço deve ter seu próprio `CLAUDE.md` antes de começar a implementação.
- Quando um serviço do monorepo for alterado, sincronizar a alteração no repo individual correspondente (se existir).

## Regras de testes

- Toda nova funcionalidade deve ter testes — nenhum feature, rota, model ou comportamento está completo sem testes correspondentes.
- Cobrir tanto o happy path quanto edge cases (404s, falhas, dados ausentes).
- Testes ficam em `tests/` dentro de cada serviço e espelham o módulo testado.
- Rodar `poetry run pytest` antes de considerar qualquer trabalho concluído.

## Regras de documentação

- Todo novo feature deve ser documentado no `CLAUDE.md` do serviço correspondente antes de ser considerado pronto.
- Documentar: caminho do módulo, API pública (funções/classes/endpoints), inputs/outputs e como se encaixa no pipeline.
- Manter as entradas concisas — suficiente para uma sessão futura entender o que existe sem precisar ler o fonte.
- **A cada nova implementação, atualizar os dois arquivos de produto nesta ordem:**
  1. `docs/product.md` — descrever o que mudou em linguagem natural, sem detalhes técnicos, do ponto de vista do usuário.
  2. `docs/vision.md` — com base no que foi descrito no `product.md`, detalhar as regras de negócio, decisões de design e como o novo comportamento se encaixa no pipeline.

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
| content_scout | 8005 |
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
- `http://content_scout:8000`

Externamente (localhost), cada um usa a porta mapeada acima.

## Estado do projeto

- `blender_worker` — implementado (MVP completo: API, DB, Blender pipeline, image compositor)
- `orchestrator` — planejado, em implementação
- `llm_service` — planejado
- `tts_service` — implementado (providers `azure`/`edge`, corte de silêncio + normalização de loudness, transcrição word-level)
- `tiktok_poster` — planejado
- `content_scout` — implementado (fonte Reddit via RSS; YouTube previsto como minerador de tema)

## Decisões em aberto

Ver seção "Decisões em Aberto" em `docs/vision.md`.
