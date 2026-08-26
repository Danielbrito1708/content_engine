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
  tiktok_poster/            ← agendamento no TikTok e no YouTube (nome histórico: dois destinos)
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
chave e `sudo` sem senha, então funciona com `BatchMode=yes` e não pede interação.

**É o ambiente de produção desde 14/08/2026** — a stack roda em `~/content_engine` lá, e a
stack local está desligada de propósito. **Ler `docs/servidor.md` antes de qualquer trabalho
nela**: root não loga por SSH, o IP é DHCP e não está reservado, e a máquina puxa do GitHub
por uma deploy key read-only (não dá para commitar de lá).

## Comunicação entre serviços

Internamente (dentro do Docker network), os serviços se comunicam pelo nome do container:
- `http://orchestrator:8000`
- `http://blender_worker:8000`
- `http://llm_service:8000`
- `http://tts_service:8000`
- `http://tiktok_poster:8000`
- `http://content_scout:8000`

Externamente (localhost), cada um usa a porta mapeada acima.

## O `template.json` do repo não é o que roda

Vale para sempre, não só num deploy. O `blender_worker` baixa `templates/template.json` do
MinIO/R2 **no momento do render**, e o orchestrador lê `narration.rate` do mesmo objeto via
`GET /templates/{id}/config`. Editar `blender_worker/template.json` no repo não muda nada
até o objeto ser republicado no bucket.

Conferir o que está publicado — é o mesmo endpoint que o pipeline lê:

```bash
curl -s localhost:8001/templates/$BLENDER_TEMPLATE_ID/config | python -m json.tool
```

O bucket R2 é **compartilhado entre ambientes**, então republicar afeta todos de uma vez.

⚠️ **`BLENDER_TEMPLATE_ID` também é uma linha na tabela `templates`** do banco do
`blender_worker`, não só um objeto no bucket. Num banco novo ela não existe e o endpoint
acima responde `404 Template not found` — que parece erro de credencial do R2 e não é.

## Estado do projeto

**Os seis serviços estão implementados** e o pipeline fecha de ponta a ponta.

- `blender_worker` — API, DB, Blender pipeline, image compositor, semáforo de render
- `orchestrator` — pipeline completo, rotação de background, recuperação de runs órfãos, retry de agendamento, notificação
- `llm_service` — `/refine`, `/moderate`, `/story-quality` (OpenRouter / Anthropic / Chutes)
- `tts_service` — providers `edge`/`azure`, corte de silêncio + normalização de loudness, transcrição word-level
- `tiktok_poster` — agendamento via Buffer, slots, séries, hashtags e caption; **dois destinos** (TikTok + YouTube) no mesmo slot
- `content_scout` — fonte Reddit via RSS, varredura do arquivo, dedup por conteúdo, nota de storytelling **e de revolta** (YouTube previsto como minerador de tema)

**Desde 14/08/2026 a stack roda no servidor** (`192.168.0.106`), que é o ambiente de
produção — a stack da máquina Windows foi desligada para não haver dois produtores no mesmo
perfil do Buffer. Primeiro vídeo produzido de ponta a ponta lá em 14/08/2026; render medido
em ~12min42s por parte. Ver `docs/servidor.md` → "O que roda na máquina".

### Passos pendentes de deploy (YouTube, 15/08/2026)

✅ **O destino está ligado em produção desde 15/08/2026** — passos 1 a 3 aplicados no
servidor. Sobra o passo 4, que só o primeiro post responde. Em qualquer outro ambiente os
três primeiros continuam valendo, e sem eles a stack publica só no TikTok, exatamente como
antes, sem erro e sem aviso.

1. ✅ Canal conectado na conta do Buffer (`vozes.do.reddit7`) — `6a80966db2d9d5774382d6d4`,
   confirmado pela API do Buffer **com o token do servidor**. O `.env` da máquina Windows
   está numa conta antiga do Buffer e responde `FORBIDDEN` para esse ID: verificação de
   canal só vale rodada de onde o token é o dono.
2. ✅ `BUFFER_YOUTUBE_CHANNEL_ID` no `.env` do servidor.
3. ✅ `orchestrator` e `tiktok_poster` no ar com `--build`; a **migration `006`** rodou no
   boot pelo `CMD` do Dockerfile (`Running upgrade 005 -> 006`). Sem ela todo run morre no
   refino com `UndefinedColumn`.
4. ⏳ Conferir no primeiro post se o Buffer aceita vídeo **acima de 3 minutos** no canal do
   YouTube. É o único ponto não verificável sem publicar: o canal é do tipo Shorts e a
   política de divisão permite até 30 minutos de fala.

**Notificação: o destino mudou em 16/08/2026.** A cota grátis do CallMeBot esgotou e o canal morreu em silêncio (ele responde `200` mesmo recusando). Produção agora manda para o **ntfy** via `NOTIFY_WEBHOOK_URL`, com `webhook_format = text`; o CallMeBot está comentado no `.env` do servidor. Ver `docs/vision.md` → "A recusa disfarçada de sucesso".

O que falta **não é código de feature** — é monitoramento: os três checks do
Healthchecks.io, o cron do disco (script pronto, falta agendar) e o Uptime Kuma. Ver também
o TODO de backup/retenção em `docs/deploy.md` → "O que continua em aberto", adiado por
decisão e mais urgente agora que se sabe que cada MP4 pesa ~270 MB.

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

Decisão tomada em 15/08/2026:

| Decisão | Escolha |
|---|---|
| Publicação no YouTube | Pelo **Buffer**, canal novo na mesma conta. A API direta trava o vídeo como **privado** enquanto o projeto não passar pela auditoria do Google |
| Título do YouTube | **Campo novo** no refino (`youtube_title`), não o gancho reciclado — o título é lido antes do vídeo abrir, o gancho é ouvido depois |
| Horário no YouTube | **O mesmo do TikTok** — o slot sai da fila do TikTok e é reusado nos dois canais |

Decisão tomada em 25/08/2026:

| Decisão | Escolha |
|---|---|
| Janela de publicação | **11h–20h de Brasília**, três posts por dia: 11:00, 15:00 e 19:00 BRT (`preferred_times = 14:00,18:00,22:00`, que é **UTC**) |
| Último slot às 19h | A folga de uma hora é para a **continuação de série**, que pendura 30 min depois da parte 1 e ignora os `preferred_times` |
| Guarda de janela em `continuation_slot` | **Não fazer** — empurrar a parte 2 para o dia seguinte parte a história ao meio, que é o que o encadeamento existe para evitar |
| Critério de seleção de história | **Revolta com vilão claro**, público-alvo mulheres 18–35. `/story-quality` devolve `outrage` (0–10) e `villain`; o scout ordena por `2 × outrage + story_score` |
| `min_outrage_score` | **Rótulo e contador, não portão** — ciclo sem nada revoltante publica a melhor história disponível; fila vazia é o modo de falha mais caro |
