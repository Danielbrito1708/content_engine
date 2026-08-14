# CLAUDE.md — content_engine

Guia para o Claude Code no monorepo `content_engine`.

## Estrutura do Monorepo

```
content_engine/
  docker-compose.yml        ← orquestra toda a infra + serviços
  .env.example              ← vars de todos os serviços
  docs/
    vision.md               ← visão geral do sistema, pipeline, decisões
    deploy.md               ← plano de deploy na máquina de casa (dimensionamento, monitoramento)
    servidor.md             ← como acessar a máquina por SSH + estado verificado dela
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

## Servidor (máquina de casa)

`ssh server@192.168.0.106` — Debian 13 bare metal, sem interface gráfica, autenticação por
chave e `sudo` sem senha, então funciona com `BatchMode=yes` e não pede interação. É a
máquina de deploy do `deploy.md`. **Ler `docs/servidor.md` antes de qualquer trabalho
nela**: root não loga por SSH, o IP é DHCP e não está reservado, e o Docker ainda não está
instalado.

## Comunicação entre serviços

Internamente (dentro do Docker network), os serviços se comunicam pelo nome do container:
- `http://orchestrator:8000`
- `http://blender_worker:8000`
- `http://llm_service:8000`
- `http://tts_service:8000`
- `http://tiktok_poster:8000`
- `http://content_scout:8000`

Externamente (localhost), cada um usa a porta mapeada acima.

## Passos pendentes de deploy

> Da branch `worktree-voz-narrador-fim-video`, mergeada no `main`. **Os dois passos foram aplicados no ambiente local em 14/08/2026** — o upload do template vale para todos os ambientes (o bucket R2 é compartilhado), a migration é por ambiente e ainda não rodou em nenhum outro. Apagar esta seção quando não houver mais ambiente sem ela; até lá o procedimento abaixo continua valendo para os que faltam.

### 1. Republicar o `template.json` no bucket

✅ **Feito em 14/08/2026** — `templates/template.json` no R2 agora é a cópia do repo. O objeto publicado estava muito mais defasado do que só a `narration`: não tinha `narration`, `card`, `music`, nem `channels.hook`/`channels.card` (todos vinham de default no código), e trazia `timing.outro_start`/`outro_end`/`music_fade_out`, chaves que nenhum código lê mais.

⚠️ **O upload também aplicou a legenda de 100px**, que estava commitada desde `6ca851b` (28/07) e nunca tinha subido — o bucket ainda servia `font_size: 160`. É mudança visual real e não fazia parte desta branch; veio junto porque o repo é a fonte da verdade do template. Se 160 for o valor desejado, editar `blender_worker/template.json` e republicar.

⚠️ **O `template.json` do repo não é o que roda.** O `blender_worker` baixa `templates/template.json` do MinIO/R2 no momento do render, e o orchestrador lê `narration.rate` do mesmo objeto via `GET /templates/{id}/config`. Editar a cópia do repo não muda nada até o upload.

Duas mudanças desta branch dependem disso:

| Chave | Valor | O que muda sem o upload |
|---|---|---|
| `narration.rate` | `+30%` | A narração continua no rate do template publicado |
| `narration.tail_seconds` | `0.5` | Nada — o default de 0,5s está no código e já vale |

O rate é o único que **exige** o upload. Conferir o que está publicado, antes e depois — é o mesmo endpoint que o orchestrador usa, então responde exatamente o que o pipeline vai ler:

```bash
curl -s localhost:8001/templates/$BLENDER_TEMPLATE_ID/config | python -m json.tool
```

### 2. Migration `005` do orchestrator

✅ **Feito no local em 14/08/2026** via `docker compose up -d --build orchestrator`; coluna conferida no banco. Continua pendente em qualquer outro ambiente.

**`005_add_narrator_gender_to_pipeline_runs`.** Adiciona `narrator_gender` em `pipeline_runs`, coluna que o `_refine` passou a escrever. Sem ela, todo run morre no refino com `UndefinedColumn`.

**No Docker não há passo manual**: o `CMD` do `orchestrator/Dockerfile` é `alembic upgrade head && uvicorn ...`, então a migration roda sozinha ao subir o container — **desde que a imagem seja reconstruída**:

```bash
docker compose up -d --build orchestrator
```

⚠️ `docker compose up -d` **sem `--build`** sobe a imagem antiga em silêncio: o código novo não entra, a migration não roda, e o sintoma é o run falhando no refino como se fosse bug de código. Conferir depois de subir:

```bash
docker compose exec db psql -U postgres -d orchestrator -c "\d pipeline_runs" | grep narrator_gender
```

Rodando o orchestrador **fora** do Docker, aí sim é manual, com `DATABASE_URL` apontando para o banco `orchestrator`:

```bash
cd orchestrator && poetry run alembic upgrade head
```

Nenhum outro serviço desta branch tem migration — `tts_service`, `llm_service` e `blender_worker` mudaram só em código e config.

## Estado do projeto

**Os seis serviços estão implementados** e o pipeline fecha de ponta a ponta.

- `blender_worker` — API, DB, Blender pipeline, image compositor, semáforo de render
- `orchestrator` — pipeline completo, rotação de background, recuperação de runs órfãos, retry de agendamento, notificação
- `llm_service` — `/refine`, `/moderate`, `/story-quality` (OpenRouter / Anthropic / Chutes)
- `tts_service` — providers `edge`/`azure`, corte de silêncio + normalização de loudness, transcrição word-level
- `tiktok_poster` — agendamento via Buffer, slots, séries, hashtags e caption
- `content_scout` — fonte Reddit via RSS, varredura do arquivo, dedup por conteúdo, nota de storytelling (YouTube previsto como minerador de tema)

O que falta para produção **não é código de feature** — é a máquina (`docs/deploy.md`:
Docker não está instalado no servidor) e as credenciais de alerta. Ver também o TODO de
backup/retenção em `docs/deploy.md` → "O que continua em aberto", adiado por decisão.

## Decisões em aberto

Ver seção "Decisões em Aberto" em `docs/vision.md`.

Decisões tomadas em 14/08/2026, para não serem reabertas sem motivo novo:

| Decisão | Escolha |
|---|---|
| Provider de TTS | Fica no **`edge`**. Azure implementado, não ativado — não vale a conta a manter |
| Corte `min_story_score` | Fica em **6** — nota 5 conta como fraco |
| `story_excerpt_chars` | Fica em **700** |
| Teto 9–10 da nota | **Relaxado** — o prompt manda usar a escala inteira |
| Re-medir a régua no corpus novo | **Não fazer** — segue com a régua atual |
| Fila do `blender_worker` | **Semáforo**, não Celery/Redis |
| Backup e retenção | **Adiado**, registrado como TODO no `deploy.md` |
