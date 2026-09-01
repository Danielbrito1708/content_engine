# CLAUDE.md — orchestrator

Coordenador central do pipeline de geração de conteúdo. Recebe roteiros, orquestra llm_service → tts_service → blender_worker → tiktok_poster e persiste o estado de cada pipeline run.

## Arquitetura

**Bootstrap**: igual ao blender_worker — `from src.core import settings` em `app.py` dispara `bootstrap._init()` antes de qualquer outro import ler env vars.

**Módulos:**
- `api/routes/pipeline.py` — `POST /pipeline` (cria + inicia background), `GET /pipeline/{id}`, `GET /pipeline` (lista)
- `api/routes/health.py` — `GET /health`: ping no DB
- `db/models.py` — `PipelineRun` + `PipelinePart` + enums `PipelineStatus` / `PartStatus`
- `db/engine.py` — engine async + `AsyncSessionLocal` + `get_session()` dependency
- `schemas/pipeline.py` — `PipelineCreate`, `PipelineResponse`, `PartResponse`
- `clients/llm.py` — `LLMClient.refine(script, metadata) → RefineResult`
- `clients/tts.py` — `TTSClient.generate(text, run_id, part_number=1, rate=None, label=None, narrator_gender=None) → (audio_key, srt_key)`
- `clients/blender.py` — `BlenderClient`: `create_video(...)` (inclui `card_key`/`hook_voice_key`), `create_job(...)`, `render_card(text, template, output_key)`, `get_template_config(...)`, `get_job_status(...)`, `poll_job(...)`
- `clients/tiktok.py` — `TikTokClient.schedule(video_key, classification, part_number, series_id, total_parts=1, follows_at=None, youtube_title=None)` + exceção `BufferQueueFull`
- `clients/http.py` — `request(method, url, *, timeout, attempts)`: política única de retry
- `backgrounds.py` — `pick_background(keys, run_id, part_number)`, puro
- `hook_text.py` — `opens_with_hook(script, hook) -> bool`, puro: se a parte já abre pela frase gancho
- `storage/client.py` — `upload_bytes(...)`, `get_bytes(...)` e `list_keys(bucket, prefix)` via boto3 (MinIO/R2)
- `background_inbox.py` — caixa de entrada de fundos pelo ntfy: `background_inbox_loop()`, `handle_message()`, `handle_url()`
- `worker.py` — `run_pipeline(run_id)` + `recover_interrupted_runs()`, `retry_pending_schedules()`, `maintenance_loop()`
- `api/app.py` — `lifespan`: reconcilia runs órfãos antes de servir, depois sobe o `maintenance_loop` e (se configurada) a caixa de entrada de fundos

## Estado do PipelineRun

```
pending → refining → refined → processing → scheduling → scheduled → posted
                                                     ↘ failed (qualquer etapa)
```

Cada `PipelinePart` tem seu próprio `PartStatus`:
```
pending → tts_running → tts_done → render_pending → render_running → render_done
                                                                    ↘ failed
```

## Comandos

```bash
# Instalar dependências
poetry install

# Rodar local (requer .env e db up)
python main.py

# Testes (requer docker compose up com DB orchestrator disponível)
poetry run pytest

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Variáveis de ambiente

- `ROOT_DIR` — caminho absoluto para a raiz do serviço
- `ENV` — `dev` ou `prod`
- `DEBUG` — `true` ou `false`
- `DATABASE_URL` — asyncpg para o banco `orchestrator`
- `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — credenciais MinIO/R2
- `BLENDER_TEMPLATE_ID` — UUID do template pré-registrado no blender_worker (`POST /templates`)
- `NTFY_BACKGROUND_INBOX_URL` — tópico ntfy **de entrada** para fundos: link de vídeo compartilhado do celular vira clipe no manifesto. Vazio (o padrão) desliga o laço. **Tem que ser outro tópico**, diferente de `NOTIFY_WEBHOOK_URL` e do `NTFY_INBOX_URL` do `content_scout`

URLs dos serviços são configuradas em `config.ini [services]`.
Assets estáticos (background + música) em `config.ini [template]`.

## Features

### Pipeline API (`src/orchestrator/api/routes/pipeline.py`)

- `POST /pipeline` — cria `PipelineRun` e inicia `run_pipeline` em background. Retorna 201 com o estado inicial.
- `GET /pipeline/{id}` — retorna run + todas as parts com status atual.
- `GET /pipeline` — lista runs ordenados por `created_at desc`, com paginação (`limit`, `offset`).

**Request** (`PipelineCreate`): `script` (str), `metadata` (dict, opcional).
**Response** (`PipelineResponse`): `id`, `status`, `parts_count`, `classification`, `error`, `parts[]`, timestamps.

### Worker (`src/orchestrator/worker.py`)

`run_pipeline(run_id)` — executa as fases sequencialmente:

1. **`_refine`**: chama `LLMClient.refine()` → guarda `hook` e `narrator_gender` no run e cria `PipelinePart` para cada parte retornada
2. **`_narration_rate`**: lê `narration.rate` do template uma vez por run, antes do primeiro TTS
3. **`_run_hook_tts`**: narra a frase gancho num arquivo próprio (degradável — ver abaixo)
4. **`_render_card`**: compõe o card de comentário com o gancho (degradável — ver abaixo)
5. **`_process_all_parts`**: para cada part, executa `_run_tts` + `_run_render` sequencialmente
6. **`_schedule`**: chama `TikTokClient.schedule()` para cada part com `video_key` definido

**`_run_tts`**: chama `POST tts_service/generate` → salva `audio_key` e `srt_key` na part. Recebe o `rate` da narração e o repassa; `None` deixa o `tts_service` aplicar seu `TTS_RATE`. O roteiro vai inteiro: quem evita a repetição do gancho é o `hook_muted` do render, não um corte no texto.

### Voz do narrador (`narrator_gender`)

O `llm_service` devolve `narrator_gender` (`male` / `female` / `unknown`) — o gênero de quem conta a história. O orchestrador guarda em `PipelineRun.narrator_gender` (migration `005`, exposto em `PipelineResponse`) e manda em **toda** chamada ao `tts_service`: no gancho e em cada parte.

⚠️ **Requer a migration `005` aplicada** — sem a coluna, todo run morre no `_refine` com `UndefinedColumn`. O `CMD` do Dockerfile roda `alembic upgrade head` no boot, então subir com `--build` basta; fora do Docker é manual. Ver "Passos pendentes de deploy" no `CLAUDE.md` da raiz.

**Sai o gênero, nunca o nome da voz.** Qual voz corresponde a que gênero é decisão do `tts_service`, que conhece os providers; daqui sai um fato sobre o roteiro. Ausente, o campo é **omitido** do payload (não vai `null`), e a narração sai na voz padrão de lá.

**Do run, não da parte** — uma história dividida é a mesma pessoa contando. Mesma razão pela qual o gancho leva o mesmo `narrator_gender` **e** o mesmo `rate` das partes: basta a voz ou a velocidade divergir para o vídeo abrir com dois narradores.

`LLMClient.refine` lê `data.get("narrator_gender")` com fallback `"unknown"`, como faz com o `hook` — um `llm_service` antigo produz run sem gênero, não erro.

- Testes: `tests/test_narrator_voice.py` (10 — persistência no refino, fallback e normalização, o campo chegando ao TTS no gancho e nas partes, a omissão quando não há gênero, a garantia de que nenhum nome de voz sai daqui, o pipeline inteiro com um valor só, e o campo na API).

### Publicação no YouTube (`youtube_title` + `youtube_video_id`)

O mesmo vídeo passou a ser publicado também no YouTube, pelo mesmo Buffer. **O orchestrador não conhece o YouTube**: ele guarda o título que o refino escreveu, manda no `POST /schedule` e lê de volta o que o poster conseguiu fazer. Quem fala com a API é o `tiktok_poster`.

- `PipelineRun.youtube_title` (migration `006`, exposto em `PipelineResponse`) — vindo de `RefineResult.youtube_title`. **É do run, não da parte**: uma história dividida é a mesma história, e o que distingue as partes é o rótulo `(Parte n/N)`, que o poster acrescenta por saber `total_parts`.
- `PipelinePart.youtube_video_id` (migration `006`, exposto em `PartResponse`) — `None` quando o destino está desligado ou quando o agendamento lá falhou. É esta coluna que torna a ausência auditável depois de o aviso ter passado.

`LLMClient.refine` lê `data.get("youtube_title")` com fallback `""`, como faz com o `hook` — um `llm_service` antigo produz run sem título, não erro. `_schedule` **omite** o campo do payload quando vazio (não manda `null`), mesmo contrato do `narrator_gender` no `tts_service`.

⚠️ **Requer a migration `006` aplicada.** O `CMD` do Dockerfile roda `alembic upgrade head` no boot, então subir com `--build` basta; fora do Docker é manual.

⚠️ **Sexta degradação silenciosa.** Falha no YouTube não derruba o run: o TikTok já está agendado, e o run termina `scheduled` publicando nos dois lugares ou em um só — o status não distingue os dois casos. Daí o `warning` em `_schedule`.

⚠️ **O aviso é condicionado a `youtube_enabled`**, que vem na resposta do poster. Destino desligado não é degradação, é configuração: sem essa guarda, toda parte de todo run dispararia um aviso enquanto o canal não estivesse conectado, e um alarme que toca sempre é um alarme que ninguém lê.

- Testes: `tests/test_youtube_destination.py` (12 — o título persistido no refino, a omissão do campo, o mesmo título em toda a série, o ID de volta, o poster antigo sem os campos novos, e o aviso disparando na falha e **não** disparando no destino desligado).

### Velocidade da narração (`_narration_rate`)

A velocidade da narração é definida no `template.json`, no bloco `narration.rate`. Como o TTS roda muito antes do render, o orchestrador precisa ler o template **antes** de chamar o `tts_service`:

1. `GET blender_worker/templates/{BLENDER_TEMPLATE_ID}/config` → `template.json` parseado
2. Extrai `narration.rate` (ex.: `"+30%"`)
3. Repassa como `rate` no `POST tts_service/generate`

Buscado **uma vez por run** em `run_pipeline`, antes do TTS do gancho — o template é o mesmo para todas as partes, e o gancho tem de sair no mesmo rate que elas.

**Degrada em silêncio.** Template sem bloco `narration`, blender_worker fora do ar, config inválido — tudo cai no `TTS_RATE` do `tts_service` com um warning no log, sem derrubar o run. Velocidade de narração é decisão estética; não vale falhar um pipeline por isso. Contrasta com o render, onde qualquer falha aborta.

O orchestrador **não valida o formato** do rate — quem valida é o `tts_service` (`422`). Duplicar a regex em dois serviços só criaria duas fontes de verdade.

**`_run_render`**:
1. Usa `part.srt_key` — legenda word-level já transcrita e subida pelo `tts_service` em `subs/{run_id}/part_{n}.srt`. O orchestrador não gera SRT.
2. `POST blender_worker/videos` com `background_video_key` + `music_key` (do config.ini) + `voice_key` (audio do TTS) + `subtitle_key` + `card_key`/`hook_voice_key` (a intro, nullable)
3. `POST blender_worker/jobs` com `video_id` + `BLENDER_TEMPLATE_ID`
4. Polling via `poll_job()` até `completed` ou `failed`
5. Salva `video_key = output_key` na part

**Pré-requisito de infra**: o template (`.blend` + `template.json`) e os assets estáticos (fundos, trilha) devem estar pré-registrados no blender_worker e no MinIO antes de rodar o pipeline.

⚠️ **`[template] music_key`** apontava para `assets/music.mp3`, que é **35s de silêncio digital** (medido: zero amostras não-nulas) — todo vídeo publicado até aqui saiu sem trilha, sem nenhuma falha. Agora aponta para `assets/music/lofi-goularte.mp3`. O render não valida conteúdo de áudio: um arquivo mudo continua sendo um render bem-sucedido. Ver `docs/vision.md` → "Trilha sonora".

### Áudio da frase gancho (`_run_hook_tts`)

O `llm_service` devolve `hook` — a frase de abertura do roteiro, isolada. O orchestrador guarda em `PipelineRun.hook` e narra essa frase sozinha, chamando o mesmo `POST tts_service/generate` com `label="hook"` → `hook_audio_key` (`audio/{run_id}/hook.mp3`) e `hook_srt_key`.

⚠️ **A etapa é degradável de propósito.** Falha no TTS do gancho vira `log.warning` e o run continua; falha no TTS de uma parte continua derrubando o run. A abertura é uma camada a mais sobre um vídeo que já se sustenta sem ela (a narração da parte 1 abre com essa mesma frase), e perder a camada não pode custar o vídeo. A ausência fica auditável em `hook_audio_key` nulo.

`LLMClient.refine` lê `data.get("hook")`: um `llm_service` antigo produz run sem gancho, não erro. Deploy dos dois serviços não é atômico.

Colunas em `pipeline_runs`: `hook`, `hook_audio_key`, `hook_srt_key` (migration `003`). Expostas em `PipelineResponse`.

### Abertura do vídeo: card + gancho (`_render_card`)

O card de comentário com a frase gancho é composto uma vez por run (`POST blender_worker/images/render` com `template` = `[template] card_template`, `text` = `run.hook`, `output_key` = `cards/{run_id}.png`) e guardado em `PipelineRun.card_key` (migration `004`, exposto em `PipelineResponse`).

`_run_render` manda `card_key` + `hook_audio_key` + `hook_muted` no `POST /videos`, para **todas as partes** — a intro é o que dá a mesma cara à série inteira, não uma abertura só da parte 1.

⚠️ **Degradável como o áudio do gancho**: falha na composição vira `warning`, `card_key` fica nulo e o vídeo sai sem abertura. Sem gancho, o card nem é pedido.

**`_run_hook_tts` agora recebe o `rate`.** Como o gancho é montado na frente da narração, ele tem de ser narrado na mesma velocidade — daí `_narration_rate()` ter subido para antes do primeiro TTS (era lido dentro de `_process_all_parts`, que agora recebe o valor pronto).

**O gancho é dito uma vez só** (`_hook_is_muted` + `hook_text.opens_with_hook`). Numa parte que já abre pela frase — a parte 1, por construção — tocar o `hook.mp3` na frente da narração diria a mesma coisa duas vezes. Então essa parte vai com `hook_muted=True`: o arquivo continua sendo mandado (é ele que mede quanto tempo o card fica na tela) e o blender_worker o monta mudo, deixando a narração dizer a frase.

A condição é o **texto** da parte, não o número dela: se o refino devolver o gancho abrindo a parte 2, ela se muta sozinha. Gancho reescrito pelo modelo dá `False`, e o vídeo volta a abrir com o áudio próprio. `part.script` nunca é reescrito e o TTS recebe o roteiro inteiro.

Regras completas em `docs/vision.md` → "Abertura do vídeo (intro: card + gancho)".

### Legendas

O orchestrador não participa da geração de legenda: o `tts_service` transcreve o próprio áudio (Whisper, timestamp por palavra) e devolve o `srt_key` junto com o `audio_key`. O orchestrador só persiste em `PipelinePart.srt_key` e repassa como `subtitle_key` ao `blender_worker`. Regras em `docs/vision.md` → "Legendas (word-level)".

### Rotação de background (`src/orchestrator/backgrounds.py`)

`pick_background(keys, run_id, part_number, used=None) -> str` — escolhe o clipe de fundo de cada parte entre os objetos sob `[template] background_prefix` (default `assets/backgrounds/`).

- **Rotação de verdade desde 28/08/2026.** `used` mapeia clipe → quantas partes já saíram nele; a escolha vem do subconjunto **menos usado**, então nenhum clipe repete antes de a biblioteca inteira passar. ⚠️ Antes era `sha256(run_id) % len(keys)` — sorteio **com reposição**, que só parecia rotação: em 47 posts sobre 40 clipes reusou um clipe **20 vezes**, a primeira repetição no segundo dia da conta.
- **Empate resolvido por hash**, não por ordem da lista: senão um ciclo novo caminharia a biblioteca alfabeticamente e posts consecutivos dividiriam trechos consecutivos do mesmo arquivo de origem.
- **Determinístico** (`sha256(run_id:part)`, não `hash()`, que é salgado por processo): partes da mesma série caem em clipes diferentes. O que garante o re-render agora é a **persistência**, não o cálculo — a contagem muda com o tempo, a coluna não.
- **`PipelinePart.background_key`** (migration `007`) guarda o clipe escolhido. Gravado **antes** do render, não depois: é o que tira o clipe do bolso dos disponíveis antes que a próxima parte escolha (o render leva minutos) e o que faz um re-render reusar a mesma footage. Nulo nas partes anteriores à migration, que por isso não contam para o ciclo — o primeiro ciclo depois dela passa pela biblioteca inteira.
- **Clipe novo fura a fila**: começa com zero usos, então sai antes do que já está em rotação. Footage nova chega ao canal sem esperar o ciclo fechar. Contagem de clipe que saiu do bucket é ignorada.
- `_background_usage(session)` agrega a contagem; `background_key_for(session, run_id, part_number)` lista o prefixo e cai no `background_video_key` único quando não há clipes.
- Levanta `ValueError` com lista vazia — quem chama decide o fallback.

⚠️ **Requer a migration `007` aplicada.** O `CMD` do Dockerfile roda `alembic upgrade head` no boot, então subir com `--build` basta; fora do Docker é manual.

### Fundo sob demanda (`src/orchestrator/background_source.py`)

A biblioteca deixou de ser "arquivos no bucket" e passou a ser **manifesto + cache**. O manifesto (`[template] background_manifest`, um JSON no bucket) lista ~1.677 segmentos de 2 min cortados de uma playlist de 326 vídeos; o clipe só vira arquivo no render que o sortear.

- **Candidatos = manifesto ∪ prefixo.** Um segmento ainda não materializado concorre em pé de igualdade com um clipe subido à mão. Manifesto ausente ou ilegível devolve `{}` e a rotação volta a sortear só o que está publicado — a camada é opcional.
- `plan_segments(duration, segment_seconds, min_tail)` / `segment_key(prefix, video_id, start)` — puros. A chave é função de `(video_id, start)`, então **regerar o manifesto não invalida o cache**.
- `is_clip(key)` — filtra o que não é vídeo. ⚠️ `list_keys` devolve o prefixo inteiro: por isso o manifesto vive **fora** de `background_prefix`, e o filtro é a segunda linha de defesa. Um `.json` sorteado como fundo faria o render montar um JSON como movie strip.
- `ensure_available(...)` baixa **só a faixa pedida** (`download_ranges`) e recorta. ⚠️ **Nada de `force_keyframes_at_cuts`**: com ele o yt-dlp baixa o vídeo inteiro para cortar com precisão de frame — medido, 188 MB e subindo para um trecho de 2 min, contra 404 MB para o vídeo todo. Precisão de frame não vale nada num fundo.
- ⚠️ **A fonte tem de ser 4K.** O recorte 9:16 de um 3840×2160 dá 1215×2160 e *desce* para 1080×1920; de um 1080p daria 607×1080 e *subiria*, borrado. Daí `source_quality` pedir `height>=2160` antes de aceitar menos.
- `evict(...)` — despejo LRU quando o cache passa de `[backgrounds] cache_max`. **Nunca despeja clipe de parte com `video_key` nulo** (está esperando render, e o blender_worker vai buscar a chave no bucket) nem o recém-materializado (é o clipe do render corrente). Só roda **depois** de um download: varrer o prefixo a cada render custaria uma listagem por vídeo sem nada ter mudado.

⚠️ **Sétima degradação silenciosa.** Falha em materializar não derruba o run — cai para outro clipe já disponível e, em último caso, para o `background_video_key` fixo. O `background_key` é reescrito com o que foi de fato usado, senão um re-render insistiria para sempre na fonte que não baixa. O vídeo sai e o run termina `scheduled`: sem o `notify` aqui, não há aviso em lugar nenhum.

**Dependências novas na imagem:** `yt-dlp` (pyproject) e **`ffmpeg`** (Dockerfile). Sem ffmpeg no PATH o download por faixa cai para o vídeo inteiro, em silêncio.

**Reconstruir o manifesto** (não baixa vídeo nenhum, é `extract_flat`):

```bash
python scripts/build_background_manifest.py <url-da-playlist> [--dry-run]
```

### Caixa de entrada de fundos (`src/orchestrator/background_inbox.py`)

Segundo caminho para alimentar a biblioteca, ao lado do script manual acima: você compartilha o link de **um** vídeo pelo celular e ele vira entradas novas no manifesto, sem terminal. Mesmo padrão ntfy do `content_scout/inbox.py`, adaptado para outro alvo.

**API pública:**
- `background_inbox_loop()` — assina `{NTFY_BACKGROUND_INBOX_URL}/json` e trata cada mensagem. Iniciado no `lifespan`, só se `[background_inbox] enabled` e a env var estiverem setados.
- `handle_message(message) -> list[str]` — extrai as URLs e processa **todas**, sem pausa entre elas (não há API de terceiro com rate limit aqui, só metadado do yt-dlp e upload para o próprio bucket).
- `handle_url(url) -> str` — o desfecho: `submitted` / `duplicate` / `too_short` / `no_duration` / `metadata_failed`.
- `extract_urls(message) -> list[str]` — puro, igual ao do `content_scout`.

**Nenhum vídeo é baixado aqui — só a duração.** `_fetch_metadata` chama `yt-dlp` com `download=False` e `noplaylist=True`: um link de playlist vira só o primeiro vídeo, de propósito — suportar playlist pelo celular replicaria `extract_flat` para um caso que já tem solução manual. O download de fato só acontece quando `ensure_available` materializa o clipe sorteado, no render.

**Dedup é reentrada no manifesto, sem tabela nova.** `segment_key` é função de `(video_id, start)`, então o mesmo vídeo mandado duas vezes gera as mesmas chaves — se todas já estão no manifesto, o desfecho é `duplicate` e nada sobe ao bucket. Se só parte das chaves existir (ex.: manifesto reconstruído com outro `segment_seconds` no meio do caminho), as que faltam são adicionadas.

⚠️ **Vídeo curto demais não produz clipe.** `plan_segments` devolve lista vazia abaixo de `min_tail_seconds`, e o link é recusado com aviso — igual à regra que já valia para a playlist.

⚠️ **O tópico é outro, de novo.** Nem o do `NOTIFY_WEBHOOK_URL`, nem o `NTFY_INBOX_URL` do `content_scout` — os três precisam ser tópicos distintos, ou o serviço lê as próprias mensagens (de saída, ou de outro serviço) como se fossem link.

Config em `config.ini [background_inbox]`: `enabled`, `reconnect_delay_seconds`, `connect_timeout`, `failures_before_alert` — mesmas chaves e mesmo motivo do `[inbox]` do `content_scout`.

- Testes: `tests/test_background_inbox.py` (11 — extração de URL, vídeo novo virando clipes, duplicado não reenvia o manifesto, vídeo curto, sem duração, falha do yt-dlp e múltiplas URLs numa mensagem). Todos puros/mockados: nenhum yt-dlp, nenhum bucket de verdade.

### Resiliência (`src/orchestrator/worker.py`)

- **`BufferQueueFull` não é falha.** `_schedule` devolve `False`, o run fica em `scheduling` com os vídeos intactos, e o scout lê isso como capacidade ocupada (backpressure). `_schedule` é idempotente: parte com `scheduled_at` é pulada.
- **`BufferQueueFull` cobre duas esperas.** `exc.error` diz qual: `buffer_queue_full` (a fila do canal está no teto — abre quando um post publica) ou `buffer_rate_limited` (a **cota da API** do Buffer estourou — abre na virada da janela, com `exc.retry_after` em segundos). O aviso no WhatsApp muda de texto conforme o caso, porque os dois mandam procurar em lugares opostos. ⚠️ **A cota é 250 chamadas/dia**, e a varredura de retry gasta 1–2 por run parado a cada 15 min: um run esperando consome quase a cota do dia só tentando. Ver a tabela em `tiktok_poster/CLAUDE.md`.
- **O teto do Buffer chega por dois caminhos, e os dois param no mesmo lugar.** O poster devolve `429` tanto quando a contagem dele não acha vaga quanto quando o próprio Buffer recusa a criação por teto — nesse segundo caso com a mensagem crua em `detail.rejected_by_buffer`, que a exceção carrega e o aviso mostra. **Antes, o segundo caminho era um 500**: o run virava `failed` definitivo, e a varredura de retry, que só olha `scheduling`, nunca mais o encostava. Dois runs terminaram assim em 15/08/2026, com o vídeo renderizado. Ver `tiktok_poster/CLAUDE.md`.
- **Séries saem encadeadas.** Cada parte manda `follows_at` = horário agendado da anterior, e o poster a coloca `series_gap_minutes` (30) depois. Só a parte 1 vai sem âncora e disputa os horários preferidos. Uma parte **pulada por já ter `scheduled_at` atualiza a âncora** antes do `continue` — senão um run retomado mandaria a parte 2 sem âncora e ela cairia no calendário, quebrando a série exatamente no caso em que o encadeamento importa. Parte sem `video_key` não vira âncora.
- **`total_parts` vem do run, não da classificação.** `_schedule` manda `len(parts)`. O poster lia `classification["parts"]`, chave que o `llm_service` nunca preencheu — o rótulo "(Parte 1/2)" nunca apareceu em post nenhum. Ver `tiktok_poster/CLAUDE.md`.
- **`recover_interrupted_runs()`** — roda no `lifespan` antes da primeira request. Estado ativo no boot é órfão por definição (as `BackgroundTasks` morrem com o processo): run com todas as partes renderizadas é retomado no agendamento, o resto vira `failed` com `"interrompido por restart"`.
- **`retry_pending_schedules()` / `maintenance_loop()`** — reoferece os runs parados a cada `[pipeline] retry_interval_seconds` (**3600s desde 15/08/2026**, era 900s: cada passada gasta chamada da API do Buffer, cujo teto é 250/dia, e a 900s um único run parado consumia 96 delas). ⚠️ **A varredura itera sobre `id`, não sobre instâncias ORM**, e busca cada run dentro do laço. O `rollback` do tratamento de erro expira *todos* os objetos da sessão: segurando os runs seguintes como instâncias, a iteração seguinte tocava um objeto expirado, o SQLAlchemy tentava recarregá-lo de forma síncrona dentro do contexto async, e a passada inteira morria com `greenlet_spawn has not been called` — erro sem relação nenhuma com a falha original. Na prática, **o primeiro run que falhava cancelava todos os pendentes**. Pelo mesmo motivo, o log e o `notify` do `except` usam o `run_id` local, nunca `run.id`.
- **`clients/http.py`** — 3 tentativas com backoff exponencial em erro de transporte e 5xx. **4xx nunca é repetido**, incluindo o `429` do poster.

### Notificação de operação (`src/core/notify.py`)

Avisa no WhatsApp o que o pipeline está fazendo, evento a evento. Arquivo **idêntico** ao de `content_scout/src/core/notify.py` — mesmo padrão de `bootstrap.py`/`logger.py`. Ao editar um, copiar para o outro.

**API pública:**
- `notify(text, *, level="info", icon="•", **fields) -> None` — **síncrono**, só enfileira. Nunca levanta, nunca bloqueia.
- `ping(check, *, fail=False)` — dead-man's switch; destino em `HEALTHCHECK_{CHECK}_URL`, ausente = no-op.
- `sender_loop()` — drena a fila; sobe no `lifespan`, **antes** da reconciliação (é ela que produz o primeiro aviso do boot).
- `format_message(text, *, icon, **fields)`, `short_id(uuid)`, `callmebot_accepted(body)` e `strip_html(body)` — puros.
- `reset()` — descarta a fila; só para testes.

⚠️ **`notify()` é síncrono de propósito.** Sem `await` no ponto de chamada, um enganche não vira ponto de suspensão no meio de uma transação, e ninguém espera pela rede dentro do pipeline. Um envio leva ~1s e um run emite ~14 eventos.

⚠️ **O CallMeBot responde `200` mesmo recusando** (cota esgotada, apikey inválida): o motivo vem só no corpo. `_deliver` lê o corpo e loga `notify_rejected` com o motivo — sem isso a recusa passa como entrega, que é como o monitoramento morreu em silêncio por dias em 16/08/2026. A checagem é pelo marcador de sucesso (`callmebot_accepted`), não por lista de erros conhecidos, para que um modo de recusa novo também vire aviso. Continua sendo `log.warning`, nunca `notify()` — avisar pelo canal que falhou seria circular.

⚠️ **`[monitoring] webhook_format`** decide o corpo do POST: `json` manda `{"text": ...}`, `text` manda a mensagem crua (o ntfy mostra o corpo como veio; em `json` o celular receberia o literal com chaves e aspas). **O default do código é `json`** — só o `config.ini` está em `text`.

⚠️ **Sem destino configurado, é no-op** — nem enfileira. Fila que ninguém drena encheria em dev e na suíte. `_enabled()` exige `CALLMEBOT_PHONE`+`CALLMEBOT_APIKEY` **ou** `NOTIFY_WEBHOOK_URL`.

**Onde estão os enganches** (`worker.py`, salvo indicado):

| Evento | Nível | Local |
|---|---|---|
| 📥 Roteiro recebido | info | `routes/pipeline.py` `create_pipeline` |
| 🟢 orchestrator no ar | info | `api/app.py` `lifespan` |
| 🧠 Refinando / ✂️ Roteiro refinado | debug / info | `_refine` |
| 🎙️ Gancho narrado | debug | `_run_hook_tts` |
| 🖼️ Card pronto | debug | `_render_card` |
| 🔊 Narração pronta | debug | `_run_tts` |
| 🎞️ Render iniciado / 🎬 Vídeo renderizado | debug / info | `_run_render` |
| 📋 Agendando / 📅 Publicação agendada / 🚀 Run concluído | debug / info / info | `_schedule` |
| ⏸️ Fila do Buffer cheia | info | `_schedule` (`BufferQueueFull`) |
| ⚠️ Parte não agendada no YouTube | warning | `_schedule` (só com o destino ligado) |
| ▶️ Runs destravados | info | `maintenance_loop` |
| 🔧 Runs órfãos reconciliados | warning | `recover_interrupted_runs` |
| ⚠️ Degradações silenciosas | warning | ver abaixo |
| ❌ Falhas | error | `run_pipeline`, `_schedule_in_background`, `retry_pending_schedules`, `maintenance_loop` |

**As seis degradações `warning` são o motivo principal disto existir**: gancho não narrado (`_run_hook_tts`), card não composto (`_render_card`), template ilegível (`_narration_rate`), biblioteca de fundos vazia (`background_key_for`), parte sem `video_key` (`_schedule`) e parte não agendada no YouTube (`_schedule`, só quando o destino estava ligado). Em todas o vídeo publica e o run termina `scheduled` — o status não distingue vídeo íntegro de vídeo capado, então sem o aviso aqui não há aviso em lugar nenhum.

⚠️ **O estágio da falha é lido antes de `run.status = failed`.** Depois da atribuição todo run falha "em `failed`", e o estágio é a única pista da mensagem sobre onde procurar.

**Dead-man's switches:** `alive` (a cada ciclo do `maintenance_loop`, antes do trabalho — varredura lenta não pode ser lida como morte) e `produced` (**só** no fim de `_schedule` bem-sucedido). ⚠️ **`produced` não é pingado quando o Buffer está cheio**: um run esperando vaga não produziu nada, e um switch que mente compra silêncio. Coberto por teste.

**`_when(datetime) -> str | None`** formata o horário agendado com `astimezone()`. Exige `TZ` no compose; sem ele o container roda em UTC e o horário sai 3h adiantado.

**Config** em `config.ini [monitoring]`: `enabled`, `level` (`debug` por default — tudo), `min_interval_seconds` (3; o CallMeBot recusa rajadas e um run é uma rajada). **Credenciais só por env var** — ver `.env.example`.

- Testes: `tests/test_notify.py` (40 — formatação, níveis, fila cheia, a garantia de nunca levantar, entrega nos dois destinos, retry, os pings, o `sender_loop`, os dois formatos de corpo do webhook, a recusa com `200`, e os enganches: estágio da falha, `produced` no sucesso e a ausência dele com o Buffer cheio).
- ⚠️ O fixture autouse `notify_off` (`conftest.py`) apaga as vars de destino: `bootstrap` chama `load_dotenv()`, então sem ele a suíte inteira dispararia WhatsApp de verdade.

### Storage (`src/orchestrator/storage/client.py`)

`upload_bytes(bucket, key, data, content_type) -> None` — upload via boto3 (MinIO/R2). Usa `run_in_executor` para não bloquear o event loop.

`list_keys(bucket, prefix) -> list[str]` — lista paginada e **ordenada** (a rotação de background indexa nela, então a ordem tem que ser estável).

## Testing rules

Integration tests — requerem DB rodando. MinIO e serviços externos são mockados com `respx` e `monkeypatch`.

⚠️ **`clean_db` é autouse e apaga `pipeline_runs`/`pipeline_parts` sem filtro.** Apontar `DATABASE_URL` para o banco `orchestrator` do compose destrói o estado vivo. Rodar contra um descartável:

```bash
docker exec content_engine-db-1 psql -U postgres -c "CREATE DATABASE orchestrator_test;"
# no container, com DATABASE_URL=...@db:5432/orchestrator_test
alembic upgrade head && python -m pytest -q
```

195 testes em 14 arquivos: `test_pipeline.py` (6, API layer), `test_youtube_destination.py` (12 — o segundo destino de publicação), `test_worker.py` (10, stages individuais + end-to-end), `test_series_scheduling.py` (6 — o encadeamento das partes: âncora, retomada, `total_parts`), `test_narrator_voice.py` (10 — o gênero do narrador do refino até o `tts_service`), `test_narration_rate.py` (13 — leitura do `narration.rate` do template, os fallbacks silenciosos e o repasse do `rate` ao `tts_service`), `test_hook_audio.py` (7 — gancho: persistência, key própria, skip sem gancho e falha degradável), `test_card_intro.py` (13 — card composto com o gancho, rate do gancho, o mute na parte que abre com ele e as keys chegando ao render), `test_hook_text.py` (12 — o predicado puro: prefixo, espaçamento, acentuação, e os casos em que não é abertura), `test_resilience.py` (13 — inclui a rotação consultando o banco: clipe já usado não volta, e parte anterior à migration não conta; inclui também a regressão do `greenlet_spawn`: um run que falha não pode abortar os seguintes da mesma varredura), `test_backgrounds.py` (23 — o ciclo que esgota a biblioteca antes de repetir, o planejamento de segmentos e o filtro de extensão), `test_background_source.py` (11 — união dos candidatos, a queda quando o download falha e as três coisas que o despejo nunca apaga), `test_background_inbox.py` (11 — extração de URL, vídeo novo virando clipes, duplicado, curto demais, sem duração e falha do yt-dlp) e `test_http.py` (5).

⚠️ **`test_resilience.py::test_background_*` exigem `_load_background_manifest()` caindo em `{}`** — ou seja, `MINIO_ENDPOINT` sem acesso de verdade ao bucket de produção. Rodando com um `.env` que aponta para o R2 real e tem credenciais válidas, essas quatro checagens passam a ver o manifesto de ~1.677 clipes de verdade em vez do fixture sintético, e falham por picar um clipe real em vez de `bg_000.mp4`. Não é regressão: aponte `MINIO_ENDPOINT` para algo inalcançável (ex.: `http://127.0.0.1:1`) ao rodar a suíte fora de um `.env` de teste dedicado.
