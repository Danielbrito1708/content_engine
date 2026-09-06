# Multi-conta — proposta de arquitetura

**Status: Fase 1 implementada em 06/09/2026** (ver `docs/vision.md` → "Contas de
publicação — Fase 1 do multi-account"). As Fases 2 e 3 abaixo continuam proposta — nada
delas está implementado. O documento existe para que a decisão de quando avançar seja
tomada com os números na mesa, e para que uma sessão futura não precise redescobrir por
que o desenho é este.

Data: 26/08/2026. Escrito contra o estado do repo nessa data — uma conta do Buffer
(`vozes.do.reddit7`), um canal de TikTok, um canal de YouTube, tudo em variável de ambiente.
Esse estado já não é mais o estado atual (ver Fase 1, abaixo) — mas o resto do documento
segue descrevendo as Fases 2 e 3, que ainda não existem.

---

## O objetivo

Operar dezenas a centenas de contas de publicação (TikTok + YouTube), começando por umas
poucas, **reaproveitando o mesmo roteiro em mais de uma conta** — porque roteiro de
qualidade é escasso e é o insumo que não escala junto com o resto.

Cada conta nasce de um e-mail Google próprio, que dá origem à conta do YouTube e à do
TikTok, e a uma conta do Buffer própria para agendar as duas.

---

## O que trava primeiro, e não é o banco

O schema é a parte fácil. Os limites reais, medidos no que já roda:

| Recurso | Capacidade hoje | Onde estoura |
|---|---|---|
| Render | ~12min42s por parte → **~113 partes/dia** com a máquina 100% ocupada | ~37 contas a 3 posts/dia; ~113 contas a 1 post/dia, e isso sem nenhuma falha |
| Disco | ~270 MB por MP4 → **~30 GB/dia** rendendo no talo, ~900 GB/mês | o TODO de retenção do `deploy.md` deixa de ser adiável |
| Oferta de roteiro | o scout submete 2–3 por ciclo | 100 contas × 1 post/dia = 100 publicações/dia |
| Notificação | ~14 mensagens por run, `min_interval_seconds = 3` | quebra em ~20 contas: 300 runs/dia seriam ~4200 mensagens |
| Buffer | 250 chamadas/dia e fila de 10 **por token** | **não trava** — a cota é por conta, e cada conta tem a sua |

O Buffer é o único que escala de graça, justamente porque cada conta traz o próprio teto.
O render não escala de jeito nenhum: é uma máquina, e o custo por parte é fixo.

### A conta da capacidade de render

86400s / 762s = 113 partes por dia com a máquina ocupada o tempo inteiro, sem falha, sem
re-render e sem folga para manutenção. Cem contas a três posts por dia pediriam 300 partes,
ou **63,5 horas de render por dia** — 2,6× a capacidade física.

Todo o resto desta proposta é consequência desse número.

---

## A tensão central: reuso economiza roteiro, não render

Reaproveitar o roteiro resolve a escassez de história. Não resolve nada a jusante:

- Se dez contas publicam **o mesmo MP4**, o hash perceptual é idêntico nas dez. É esse o
  sinal que derruba a rede inteira de uma vez, e derruba junto — não uma conta por vez.
- Se cada conta recebe **uma variante distinta**, voltou-se a pagar um render por conta, e
  o teto de 113/dia é o teto de contas ativas.

Então a pergunta que decide a arquitetura não é o schema, é: **qual é a unidade mais barata
de "distinto"?** O Blender VSE a 12min42s é caro demais para ser essa unidade.

Dois caminhos plausíveis, ambos por medir:

1. **Compor em vez de renderizar.** Renderizar a camada de narração+legenda+card uma vez e
   compor sobre fundos diferentes com ffmpeg, que roda perto de tempo real. Barato, mas o
   áudio continua idêntico entre as contas — e áudio também tem fingerprint.
2. **Variar a voz por conta.** O `edge-tts` é barato e o `tts_service` já escolhe voz por
   `narrator_gender`. Voz diferente muda a narração, os timestamps da legenda e portanto o
   render inteiro: distinção real, custo cheio.

⚠️ **Medir isso é pré-requisito da Fase 2, não detalhe de implementação.** É esse número
que define quantas contas o sistema comporta, e ele não depende de nenhuma linha de schema.

---

## Risco de plataforma, dito uma vez

Rede de contas publicando material reaproveitado é o padrão que o TikTok e o YouTube
detectam e punem em bloco. O desenho abaixo mitiga onde dá — variação por conta, cooldown
entre reusos, separação por nicho, aquecimento antes do ritmo cheio — mas não elimina.

A consequência de arquitetura é uma só: **a perda de uma conta tem de ser um evento
ordinário e barato**, não um incidente. Daí `accounts.status` com `banned`, daí a
publicação ser uma linha própria em vez de uma coluna no run, daí nenhuma credencial
morar em arquivo de ambiente.

---

## Modelo de dados

### O que hoje está colapsado

`pipeline_runs` mistura três coisas que passam a ter cardinalidades diferentes:

| Hoje, no mesmo lugar | Cardinalidade com N contas |
|---|---|
| o **roteiro** — `raw_script`, `refined_script`, `hook`, `classification`, `youtube_title`, `narrator_gender` | 1 por história |
| o **artefato** — `pipeline_parts.video_key`, `audio_key`, `srt_key` | 1 por (história, variante) |
| a **publicação** — `pipeline_parts.scheduled_at`, `tiktok_video_id`, `youtube_video_id` | 1 por (história, conta, parte) |

Enquanto há uma conta só, as três cardinalidades são iguais a 1 e o colapso não custa nada.
Com N contas elas divergem, e qualquer coisa construída em cima do modelo atual passa a
duplicar roteiro para poder publicar de novo — o que quebra o dedup do scout, que é por
conteúdo.

### As tabelas

```
stories          id, source, external_ref, content_fingerprint (unique),
                 raw_script, refined_script, hook, narrator_gender,
                 classification, story_score, outrage_score, villain,
                 parts_count, created_at

story_parts      id, story_id, part_number, script

accounts         id, slug, status(warming|active|paused|banned), niche, persona,
                 buffer_org_id, tiktok_channel_id, youtube_channel_id,
                 template_id, voice_id, background_set, timezone,
                 posts_per_day, preferred_times, series_gap_minutes,
                 created_at, activated_at, banned_at, ban_reason

renders          id, story_id, part_number, variant_key,
                 video_key, audio_key, srt_key, bytes, duration_s,
                 template_id, background_key, voice_id, created_at, purged_at

publications     id, account_id, story_id, render_id, part_number, total_parts,
                 status, scheduled_at, posted_at,
                 tiktok_post_id, youtube_post_id, youtube_error, error,
                 created_at
                 UNIQUE (account_id, story_id, part_number)
```

### `UNIQUE (account_id, story_id, part_number)` é a trava do reuso

É a única forma de garantir que a mesma história nunca saia duas vezes na mesma conta —
sob retry, sob recovery de boot, sob dois planners rodando ao mesmo tempo. Uma checagem em
código não dá essa garantia: o retry de agendamento e o recovery de boot já disputam as
mesmas linhas hoje (ver `worker.py` → `retry_pending_schedules`).

Publicar de novo na mesma conta é o erro mais caro do sistema, porque é visível para o
espectador e é sinal de conta automatizada. Vale uma constraint.

### `renders.purged_at` separa "apagado" de "nunca existiu"

Com ~30 GB/dia, apagar MP4 antigo deixa de ser opcional. Sem essa coluna, a retenção
apagaria a linha e com ela o registro de que a publicação aconteceu. `purged_at` preenchido
significa "o arquivo se foi, o fato permanece" — e é o que permite recontar o histórico de
uma conta depois de ela ter sido limpa.

### `accounts.status = warming`

Conta nova publicando três vezes por dia desde o primeiro dia é o padrão que queima conta.
`warming` é um estado com ritmo próprio (menos posts/dia, sem reuso de roteiro já publicado
em outra conta), e a passagem para `active` é explícita.

Sem esse estado, o único jeito de segurar uma conta nova seria editar `posts_per_day` na
mão e lembrar de desfazer — que é exatamente o tipo de passo manual que não sobrevive a
dezenas de contas.

### `pipeline_runs` vira fila de trabalho

Hoje cada run é uma `BackgroundTask` do FastAPI, que morre com o processo — é por isso que
existe `recover_interrupted_runs()`. Com centenas de contas isso não se sustenta: o trabalho
precisa ser reclamável por mais de um consumidor e sobreviver a restart sem varredura de
reconciliação.

```
jobs             id, kind(refine|tts|render|schedule), story_id, account_id,
                 status, attempts, next_attempt_at, locked_by, locked_at, error
```

Consumida com `SELECT ... FOR UPDATE SKIP LOCKED`. É o que permite mais de um worker de
render sem dois deles pegarem a mesma parte — o pré-requisito da Fase 3.

⚠️ **Isto não pede Celery nem Redis.** A decisão de 14/08/2026 ("fila do `blender_worker`:
semáforo, não Celery") continua valendo pelo mesmo motivo: o estado já vive no Postgres, e
`SKIP LOCKED` resolve a disputa sem infra nova.

---

## Onde as credenciais moram

Centenas de tokens não cabem no `.env`, e o `.env` do servidor já é o lugar onde uma troca
de conta exige parar o orchestrator antes de editar.

**Proposta:** o `tiktok_poster` ganha um banco próprio — pequeno, só
`account_credentials(account_id, buffer_token_enc, buffer_org_id, created_at)`, cifrado com
Fernet e a chave numa env var. O orchestrator manda `account_id` no `POST /schedule` e
**nunca vê token**.

O `BufferClient` já recebe `channel_id` como argumento (foi assim que o YouTube entrou);
falta parametrizar **token e org**, que hoje são leitura direta do settings no `__init__`.

⚠️ **Verificação de canal só vale rodada de onde o token é dono.** Um `channel_id` válido
consultado com o token de outra conta responde `FORBIDDEN`, o que parece credencial errada e
não é. Com N contas isso deixa de ser curiosidade e vira requisito do `GET /health`: a
verificação tem de ser feita por conta, com o token daquela conta.

**Atalho descartado:** credenciais cifradas na tabela `accounts` do orchestrator, com o
poster lendo o mesmo banco. Economiza um banco e acopla dois serviços por schema — o poster
passaria a quebrar em migration do orchestrator. Não vale a economia.

---

## Do pipeline ao planner

Esta é a mudança conceitual maior, maior que o schema.

Hoje o fluxo é **linear e disparado por chegada**: o scout acha uma história e cria um run,
que atravessa refino → TTS → render → agendamento. A capacidade é uma pergunta global
(`count_active_runs` contra `max_pending_runs`).

Com N contas vira um **problema de atribuição**: para cada conta com vaga, escolher a melhor
história ainda não usada por ela, respeitando cooldown entre reusos, distância de nicho e
distância temporal. As consequências:

- **O backpressure do scout passa a ser por conta.** Cada conta tem a própria fila de 10 no
  Buffer; uma fila cheia não diz nada sobre as outras.
- **A seleção deixa de ser "a melhor história do ciclo"** e passa a ser "a melhor história
  *para esta conta*", que é uma pergunta diferente quando as contas têm nichos.
- **O scout deixa de criar runs.** Ele passa a alimentar `stories`; quem cria trabalho é o
  planner, olhando vagas.

---

## Variação por conta: o que já é variável e o que é env var global

O que hoje é global e precisa virar coluna em `accounts`:

| Hoje | Onde está | Vira |
|---|---|---|
| `BLENDER_TEMPLATE_ID` | env var do orchestrator | `accounts.template_id` — a tabela `templates` do `blender_worker` já suporta N |
| voz | `tts_service` decide por `narrator_gender` | `accounts.voice_id`, com o gênero ainda vindo da história |
| biblioteca de fundos | `[template] background_prefix`, global | `accounts.background_set` |
| hashtags obrigatórias | `config.ini [hashtags]` | por conta, junto com o nicho |
| horários e ritmo | `config.ini [posting]` | `accounts.preferred_times`, `posts_per_day`, `timezone` |

`pick_background` já é determinístico por `(run_id, part_number)`; passa a ser por
`(account_id, story_id, part_number)` sobre o conjunto da conta. Duas contas com o mesmo
roteiro caem em fundos diferentes por construção, sem estado de rotação.

⚠️ **O `template.json` que roda é o do bucket, não o do repo** — e o bucket é compartilhado
entre ambientes. Template por conta significa **objetos separados no bucket**, um por
`template_id`, não edições no mesmo objeto.

---

## Fases

### Fase 1 — a conta vira dado (2–5 contas) ✅ implementada em 06/09/2026

Tabela `accounts` no orchestrador, `account_credentials` (cifrada) num banco novo do
`tiktok_poster`, `account_id` em `pipeline_runs`. **Sem** split de
`stories`/`renders`/`publications`: o modelo atual continua, com uma coluna a mais. Detalhes
de implementação em `docs/vision.md` → "Contas de publicação — Fase 1 do multi-account".

**O que ficou de fora de propósito, por decisão deste ciclo:** o `content_scout` ainda não
escolhe conta — toda descoberta automática publica na conta default, e a conta extra só
recebe run por disparo manual (`account_id` no `POST /pipeline`). Round-robin com
backpressure por conta (ver "Do pipeline ao planner", abaixo) é o próximo passo natural,
revisitável quando fizer sentido operar mais de uma conta em produção simultaneamente.

⚠️ **A correção abaixo, sobre o `content_scout` não rodar migration no boot, estava
desatualizada já antes desta Fase 1** — conferido no código em 06/09/2026, o `Dockerfile`
dele roda `alembic upgrade head && uvicorn ...` desde 26/08/2026, igual ao `orchestrator`. O
`tiktok_poster` passou a seguir o mesmo padrão agora que ganhou banco próprio pela primeira
vez.

### Fase 2 — o roteiro deixa de ser do run (10–30 contas)

Split `stories` / `story_parts` / `renders` / `publications`, trava de reuso, variação por
conta, planner no lugar do fluxo linear, `jobs` com `SKIP LOCKED`, notificação agregada no
lugar de evento a evento.

**Pré-requisito medido**: o custo de uma variante realmente distinta (ver "A tensão central").

### Fase 3 — escala (100+)

Mais de um `blender_worker` (ou o caminho barato de variante), retenção obrigatória,
detecção de ban por conta, observabilidade por conta. A essa altura o trabalho dominante é
criar e aquecer contas — humano, com verificação por telefone — e não software.

---

## O que precisa ser medido antes da Fase 2

Nenhum dos dois depende de schema, e os dois juntos definem o teto real do sistema:

1. **Custo de uma variante distinta.** Quanto custa produzir N versões visualmente e
   sonoramente distintas de uma história — via composição ffmpeg, via voz diferente, ou via
   render cheio. Define quantas contas cabem numa máquina.
2. **Oferta sustentada do scout.** Quantas histórias com nota utilizável por dia, em regime,
   com os subs configurados. Define o fator de reuso.

```
fator_de_reuso = (contas × posts_por_dia) ÷ histórias_novas_por_dia
```

O fator de reuso é o número mais importante do sistema. Ele não é escolhido — ele cai da
divisão acima, e é ele que diz quanto risco de fingerprint a operação está correndo.

---

## Decisões em aberto

- **Onde mora a tabela `accounts`** — orchestrator (que planeja) ou serviço novo de
  registro? A proposta acima põe no orchestrator, com só as credenciais no poster.
- **Cooldown entre reusos** — quantos dias entre a mesma história sair em duas contas, e se
  contas do mesmo nicho podem compartilhar história. Sem dado ainda.
- **Refino por conta** — o mesmo roteiro refinado uma vez e reusado, ou um refino por conta
  (persona diferente, gancho diferente)? Refino é barato perto do render, o que pesa a favor
  de por conta. Contra: dobra a chamada de LLM e não muda um pixel do vídeo.
- **Quem detecta ban** — nada no sistema hoje percebe que uma conta parou de publicar. O
  Buffer aceita o agendamento de um canal desconectado? Se aceitar, o silêncio é
  indistinguível do funcionamento normal, e é preciso um check ativo por conta.
- **Notificação em escala** — evento a evento não sobrevive a 20 contas. Resumo por conta
  por dia, ou só exceções? A regra que vale hoje ("um alarme que toca sempre é um alarme que
  ninguém lê") aponta para só exceções.
- **Validar roteiro numa conta pequena antes da principal** — nota de 06/09/2026, ainda não
  avaliada: publicar primeiro numa conta pequena/secundária e só promover para a principal
  (`vozes.do.reddit7`) o que performar. Reduziria o risco de gastar o slot da conta principal
  num roteiro fraco, mas é o oposto do desenho acima (reuso simultâneo com variação por
  conta) — aqui a mesma história sairia em sequência, numa conta e depois na outra, o que
  reabre a pergunta do cooldown/fingerprint entre reusos. Falta decidir se isso é uma
  variante do fluxo de "conta pequena = warming" já proposto, ou algo separado.
