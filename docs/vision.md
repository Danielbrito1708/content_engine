# Vision — content_engine

## Visão Geral

`content_engine` é um pipeline de automação de conteúdo para TikTok. Recebe um roteiro em texto, refina e classifica o conteúdo com LLM, gera o áudio com TTS, monta e renderiza o vídeo no Blender, e agenda a publicação no TikTok. O sistema é gerenciado por um orchestrador central que coordena todos os serviços e mantém o estado de cada pipeline run.

---

## Serviços

| Serviço | Porta interna | Responsabilidade |
|---|---|---|
| `orchestrator` | 8000 | Coordenação do pipeline, API principal, DB de estado |
| `blender_worker` | 8001 | Montagem VSE e renderização de vídeo |
| `llm_service` | 8002 | Refinamento, classificação e divisão de roteiros |
| `tts_service` | 8003 | Geração de áudio a partir de texto |
| `tiktok_poster` | 8004 | Publicação, agendamento, hashtags e analytics |
| `content_scout` | 8005 | Descoberta automática de roteiros na internet |
| `dashboard` | 3000 | Interface web simples para submissão e monitoramento |
| `db` (infra) | 5433 | PostgreSQL compartilhado entre serviços |
| `minio` (infra) | 9000 | Armazenamento de arquivos (áudio, vídeo, assets) |

---

## Pipeline Completo

```
content_scout (periódico)  ─┐
Usuário → dashboard / API ─┴─ POST /pipeline  { script: "...", metadata: {...} }
       │
       ▼
  orchestrator
  ├─ cria pipeline_run no DB (status: pending)
  │
  ├─ 1. REFINAR → llm_service
  │     └─ melhora ganchos e fluxo narrativo (CTA fica fora do texto narrado)
  │     └─ classifica: público-alvo, tom, tipo de conteúdo
  │     └─ devolve a história inteira; só divide acima de 30 min de fala
  │     └─ gera resumo das partes anteriores (para parte 2+)
  │     └─ devolve a frase gancho isolada (campo `hook`)
  │
  ├─ 1.5. TTS DO GANCHO → tts_service   (degradável: falha não derruba o run)
  │     └─ narra só a frase gancho → audio/{run_id}/hook.mp3
  │
  ├─ 1.6. CARD DO GANCHO → blender_worker   (degradável)
  │     └─ compõe o card de comentário → cards/{run_id}.png
  │     └─ os dois abrem o vídeo de todas as partes (ver "Abertura do vídeo")
  │
  ├─ [para cada parte do roteiro:]
  │   ├─ 2. TTS → tts_service
  │   │     └─ gera audio.mp3, salva no MinIO
  │   │
  │   └─ 3. RENDER → blender_worker
  │         └─ monta VSE (vídeo + áudio + legendas)
  │         └─ renderiza para output.mp4, salva no MinIO
  │
  └─ 4. AGENDAR → tiktok_poster
        └─ agenda 3 posts por dia; partes de uma série, 30 min uma da outra
        └─ otimiza hashtags com base na classificação + histórico
        └─ publica e coleta métricas
```

---

## Estado do Pipeline

Cada `pipeline_run` no DB do orchestrador segue este ciclo:

```
pending
  → refining        (llm_service processando)
  → refined         (roteiro melhorado, classificação pronta)
  → [splitting]     (se dividido em partes)
  → tts_pending     (aguardando TTS — por parte)
  → tts_running
  → tts_done
  → render_pending  (aguardando blender_worker — por parte)
  → render_running
  → render_done
  → scheduling      (tiktok_poster agendando)
  → scheduled       (agendado, aguardando hora do post)
  → posted          (publicado)
  → failed          (erro em qualquer etapa, com mensagem)
```

Cada parte de uma série tem seu próprio sub-estado. O orchestrador só avança para o agendamento quando todas as partes estão `render_done`.

### `scheduling` é um estado de espera, não de erro

Quando o `tiktok_poster` responde `429 buffer_queue_full`, o run **permanece** em `scheduling` com os vídeos renderizados intactos. Não é falha: nada está errado com o run, só não há vaga na fila naquele minuto. Antes o 429 virava exceção e queimava o run inteiro — descartando LLM, TTS, Whisper e render já pagos por causa de uma condição temporária.

Isso fecha o elo de backpressure que faltava. O scout conta `scheduling` como capacidade ocupada, então a ingestão se segura sozinha enquanto a fila do Buffer está cheia. Sem isso as taxas não fechavam: o scout ingere até 2 runs/hora (48/dia) e o Buffer publica 3 posts/dia — a diferença virava run falho depois do render.

`_schedule()` é idempotente: parte com `scheduled_at` preenchido é pulada, então re-oferecer um run nunca republica o que já tem vaga. `retry_pending_schedules()`, no `maintenance_loop`, drena os runs parados a cada `[pipeline] retry_interval_seconds` (3600s desde 15/08/2026 — ver a cota logo abaixo).

**O teto do Buffer também aparece tarde demais para a contagem enxergar.** O poster pergunta quantos posts há na fila antes de tentar, mas essa pergunta filtra `status: [scheduled]` de um canal, e o teto do Buffer não é obrigado a contar do mesmo jeito. Quando a recusa vem só na criação do post, o desfecho é o mesmo — não há vaga —, e por isso ela é traduzida no **mesmo `429`**, com a mensagem crua do Buffer em `detail.rejected_by_buffer`.

A regra que separa os dois casos: vira espera se a mensagem citar teto **ou** se uma segunda contagem, feita já no caminho de erro, mostrar a fila no limite. Qualquer outra recusa continua sendo erro de verdade. A assimetria é deliberada e vem do custo de cada engano: tratar um erro real como espera cria um run reoferecido a cada 15 minutos para sempre, que é ruim; tratar uma espera como erro **perde o vídeo**, porque `failed` é definitivo e a varredura de retry só olha `scheduling`. Errar para o lado da espera custa tempo; para o outro lado, custa o trabalho inteiro.

Não é hipótese: em 15/08/2026 dois runs morreram exatamente assim, com o vídeo renderizado no bucket e nenhum caminho automático de volta.

### A cota da API do Buffer é um recurso escasso, e a espera consome ela

O plano dá **250 chamadas por dia** (e 100 a cada 15 minutos). Estourado o teto, a API responde `429` a tudo — e isso não é fila cheia: é a mesma chamada que passaria daqui a algumas horas. Por isso vira o mesmo `429 buffer_rate_limited` para o orchestrador, que já lê `429` como espera. Antes subia como erro HTTP genérico e virava 500, ou seja, run perdido por uma condição que se resolve sozinha.

O que torna isso estrutural, e não um detalhe de plano: **esperar custa cota**. Um run parado é reoferecido de tempos em tempos, e cada tentativa gasta chamada. A 900s eram 96 por dia por run, ou 192 sem `BUFFER_ORG_ID` — quase a cota inteira gasta por um único run perguntando se já pode. O backpressure, que existe para não desperdiçar trabalho, gastava o recurso de que precisa para sair da espera.

Duas medidas aplicadas em 15/08/2026, as duas baratas:

- **`BUFFER_ORG_ID` preenchido no `.env`.** Sem ele o cliente descobre a organização a cada request — uma chamada a mais em *todo* request, inclusive nas varreduras que não vão a lugar nenhum.
- **`[pipeline] retry_interval_seconds` de 900 para 3600.** A varredura passa a andar no ritmo do que ela espera: a fila abre 3 vezes por dia, então de hora em hora nunca se atrasa mais de uma hora e custa 24 chamadas por run parado.

Fica em aberto a que resolve de verdade: **respeitar o `retry_after`** que a API já manda na resposta, pulando as varreduras enquanto a janela está fechada. É a única que não gasta nada quando não há chance nenhuma de sucesso — hoje o `retry_after` chega até o aviso no WhatsApp, mas não muda a cadência da varredura.

⚠️ **`GET /health` do poster também custa uma chamada**, porque verifica a conexão com o Buffer de verdade. Um monitor externo batendo a cada 60s são 1440 chamadas por dia contra um teto de 250 — o monitoramento derrubaria a publicação. Ver `deploy.md` → "Monitoramento" antes de apontar o Uptime Kuma para ele.

### Recuperação de runs órfãos no boot

O pipeline roda em `BackgroundTasks` do FastAPI, que morre com o processo. Um run interrompido no meio ficava `processing` para sempre, e como o scout lê esse estado como capacidade ocupada, **cinco runs órfãos paravam a ingestão em definitivo** — em silêncio.

`recover_interrupted_runs()` roda no `lifespan`, **antes** de aceitar a primeira request: qualquer estado ativo no boot é, por definição, sem dono, porque nenhuma task sobrevive a um restart.

- Todas as partes com `video_key` → o run só deve uma chamada de agendamento: é retomado.
- Qualquer outro caso → `failed` com `"interrompido por restart do orchestrator"`.

Re-executar o pipeline do topo seria a alternativa, e foi descartada: duplicaria as parts e pagaria LLM e narração de novo.

### Retry de chamadas entre serviços

`clients/http.py` centraliza a política: 3 tentativas, backoff exponencial (2s, 4s), apenas para erros de transporte e 5xx. **4xx nunca é repetido** — é resposta determinística, e isso inclui o `429` do poster, que é decisão de agendamento tratada acima, não erro para insistir.

A razão é econômica: quando o render começa, o run já pagou LLM, síntese de voz e transcrição. Uma conexão caída ou um container ainda subindo não pode custar tudo isso.

---

## Classificação de Conteúdo

O `llm_service` retorna um objeto de classificação junto com o roteiro refinado. O schema é flexível — cada tipo de conteúdo pode ter campos diferentes.

```json
{
  "content_type": "drama",
  "tone": "suspenseful",
  "target_audience": {
    "age_range": [15, 25],
    "gender": "female",
    "interests": ["relationships", "drama"]
  },
  "parts": 2,
  "split_rationale": "Cliffhanger no momento em que ela descobre as mensagens.",
  "cta_per_part": [
    "Comenta o que você acha que vai acontecer 👇",
    "Segue pra não perder o final 🔥"
  ],
  "hashtag_hints": ["#traição", "#relacionamento", "#dramadotiktok"]
}
```

O schema é armazenado como JSONB no DB do orchestrador. Novos campos são adicionados sem migração.

**`cta_per_part` era legenda, não roteiro — descrição histórica.** O prompt de refino pedia que *cada parte terminasse com um CTA*, e as partes são narradas literalmente (`text=part.script`) — o CTA era falado no vídeo, depois do desfecho da história. Eram dois artefatos diferentes com o mesmo nome: o campo, que o `tiktok_poster` usava em `compose_caption()`, e uma frase escrita dentro do texto narrado. O segundo saiu do prompt em 27/08/2026, junto com qualquer outra forma de finalização (despedida, moral, "e é isso"); o primeiro saiu da legenda em 31/08/2026, quando `cta_per_part` passou a ser a pergunta que fecha a *narração* (ver "Formato: uma história completa em 10 a 40 segundos" abaixo) — repeti-la na legenda entregaria o desfecho antes da história. Hoje `cta_per_part` não aparece em lugar nenhum do post; quem entra na legenda, se houver, é `classification.binary_cta` (ver "CTA de votação binária na legenda").

O corte com cliffhanger continua valendo: um corte no meio da tensão é parte da história, não uma finalização colada nela.

---

## Formato: uma história completa em 10 a 40 segundos

**Um vídeo por história, sempre.** O LLM devolve `parts` com exatamente um elemento e
`split_rationale` nulo. Não existe mais divisão em partes: a história é **recontada
condensada** para caber na janela, e quem assiste entende a situação, o conflito e o que
está em jogo sem precisar de nenhum outro vídeo.

A banda vive em `llm_service/prompts/refine.py`:

```
TARGET_MIN_SECONDS = 10   TARGET_MAX_SECONDS = 40
NARRATION_WPM      = 225  (150 wpm da voz neural × o +50% do template)
TARGET_MIN_WORDS   = 37   TARGET_MAX_WORDS   = 150
```

**Declarada em segundos e traduzida para palavras.** O prompt fala em palavras porque é o
que o modelo consegue contar; segundos é o que a regra significa. ⚠️ `NARRATION_WPM` embute
o `narration.rate` do template **publicado no bucket** — mexer num sem o outro desloca a
banda inteira sem erro nenhum, e o desvio só aparece no vídeo pronto.

**O prompt manda condensar, e isso é a inversão de uma regra anterior.** Até 31/08/2026 ele
dizia o oposto — *"Não resuma, não encurte e não corte trechos para o roteiro caber em menos
tempo"* — porque o problema de então era um modelo que resumia em vez de dividir. Com a
divisão eliminada, resumir deixou de ser o modo de falha e passou a ser o trabalho. O que
substituiu a regra não é "resuma", é uma instrução sobre **o que** cortar: preservar quem é
quem, o conflito, o detalhe concreto que dá raiva e a virada; cortar personagem secundário,
contexto que não muda o julgamento e diálogo repetido. E cortar com cena e número ("gastou
nossa reserva de 40 mil no cassino"), não com abstração ("foi irresponsável").

**Agora há guarda determinística, e ela junta em vez de descartar.**
`RefineResponse._collapse_to_single_part` transforma qualquer `parts` de tamanho N>1 num
elemento só. A versão anterior não tinha guarda porque reunir partes exigiria remover os "Na
parte anterior..." e os CTAs de meio de história — reescrita, não validação. Sem a divisão,
esses enxertos não existem e a junção é concatenação simples.

⚠️ **Junta, nunca descarta o excedente.** Os dois modos de falha não se equivalem: um vídeo
longo demais é um vídeo ruim, visível na hora e auditável em `split_rationale`; um vídeo sem
o fim da história é um produto quebrado, e ninguém repara até alguém assistir. O corte de
tamanho continua sendo responsabilidade do prompt — no validator não há como cortar sem
partir frase ao meio.

**A mecânica de série continua existindo, dormente.** `PipelinePart`, o rótulo "(Parte n/N)"
e o encadeamento de slots (`follows_at` / `continuation_slot`) não foram removidos: com
`parts` sempre de tamanho 1 eles simplesmente não disparam. Remover custaria migration,
schema e três serviços para apagar um caminho que não incomoda parado — e que é o que
sustenta a guarda acima quando o modelo desobedece.

**Por que 40s e não os 6 minutos anteriores.** O formato longo foi adotado em 27/08/2026
contra o de 600 palavras, e o argumento era retenção: vídeo longo com história inteira retém
melhor que seis fragmentos, porque não pede nenhuma ação para continuar. Esse argumento
sobrevive intacto — o que muda é que a história inteira passou a caber em 40 segundos.
⚠️ **Isto não é correção de alcance.** A queda de agosto foi medida e a duração **não** era
o discriminador (a conta entregava bem com vídeos de 0:56 a 6:10 no mesmo período); ver
"Queda de alcance". É decisão de formato, e deve ser avaliada como tal.

**De brinde, o teto do YouTube deixa de morder.** O Buffer recusa vídeo acima de ~3 min no
canal (é do tipo Shorts): nos dados, tudo até 2:25 tem `youtube_video_id` e tudo de 4:00 para
cima está nulo. Em 40 segundos, 100% dos vídeos passam nos dois destinos.

---

### O texto narrado termina numa pergunta em primeira pessoa

A última frase da narração é a decisão em aberto da história, feita pelo narrador ao
espectador: *"devo me separar?"*, *"devo processar meu ex-marido?"*. O modelo copia essa
mesma frase, literal, em `classification.cta_per_part` — mesmo padrão do `hook`, que também
é uma cópia identificada de um trecho que continua dentro do roteiro.

**Isto reverte a decisão de 27/08/2026**, que tirou o CTA do texto narrado. A regra de então
existia por um motivo específico: o CTA era genérico ("Comenta o que você faria 👇") e vinha
*depois* do desfecho, colado numa história já encerrada. A pergunta agora não fecha o vídeo —
ela **é** o ponto em que a história para. Por isso o prompt exige que a condensação pare na
decisão: perguntar "devo me separar?" numa história que já terminou na separação é
incoerente, e essa incoerência é o modo de falha a vigiar.

**O que foi removido em 27/08 continua removido**: despedida, moral, "e é isso", pedido de
like/follow/inscrição. A única finalização permitida é a pergunta.

**A pergunta sai da história, não de um molde.** O prompt proíbe explicitamente a frase que
serviria para qualquer vídeo ("comenta o que você faria") e exige a que cita o que aconteceu
ali ("depois disso tudo, devo aceitar ele de volta?").

⚠️ **E ela não vai na legenda.** `compose_caption` deixou de escrever o `cta`: repeti-la no
post faria o espectador **ler agora o que vai ouvir em 30 segundos**, entregando o desfecho
antes da história. A legenda passou a ser só as hashtags (mais o rótulo de parte, que não
dispara). O parâmetro continua na assinatura da função porque `_schedule_youtube` ainda usa
`cta` como fallback de título quando um orchestrador antigo não manda `youtube_title` —
tirá-lo mudaria a chamada em dois destinos para não mudar nada no resultado.

---

## Agendamento de séries

Quando há mais de uma parte, elas saem **encadeadas**: a parte N é agendada `series_gap_minutes` (30) depois do horário já agendado da parte N-1. Só a parte 1 disputa `preferred_times` / `posts_per_day`.

```
_schedule (orchestrator)          POST /schedule (tiktok_poster)
  parte 1 → follows_at ausente  → next_available_slot()   → 22:00 UTC (19:00 BRT)
  parte 2 → follows_at = 22:00  → continuation_slot()     → 22:30 UTC (19:30 BRT)
  parte 3 → follows_at = 22:30  → continuation_slot()     → 23:00 UTC (20:00 BRT)
```

**Por que a continuação ignora os horários preferidos.** `preferred_times` e `posts_per_day` existem para espaçar histórias independentes ao longo do dia. Uma história dividida não são N posts: é uma história continuada, e submetê-la a esse ritmo jogava a parte 2 para o dia seguinte — que era exatamente o comportamento anterior ("cada parte em um dia consecutivo"). ⚠️ **Desde 31/08/2026 esse caminho é dormente**: `parts` tem sempre um elemento (ver "Formato"), então nenhum run manda `follows_at`. A mecânica ficou porque é ela que sustenta a guarda de junção quando o modelo desobedece.

**O `queue_limit` continua valendo.** É o teto da fila do Buffer, não uma escolha de ritmo — furá-lo falharia na API em vez de ali. Uma continuação que esbarra nele devolve `429` e cai no mesmo caminho de backpressure de sempre: o run fica em `scheduling` e o `retry_pending_schedules()` reoferece.

**O intervalo é espaçamento mínimo, não deslocamento fixo.** `continuation_slot` devolve `max(follows_at + gap, now + gap)`. Um run retomado muito depois de um restart tem a parte anterior no passado; ancorar nela pediria ao Buffer um horário já vencido. A história volta a andar um intervalo a partir de agora.

**A âncora sobrevive ao restart.** `_schedule` é idempotente — parte com `scheduled_at` é pulada —, mas a parte pulada **atualiza a âncora** antes do `continue`. Sem isso, um run retomado com a parte 1 já agendada mandaria a parte 2 sem `follows_at` e ela cairia no calendário, quebrando a série justamente no caso em que o encadeamento importa. Parte sem `video_key` não vira âncora: ela não foi agendada, então não há horário a herdar.

**`total_parts` passou a vir do orchestrador.** O poster lia `classification["parts"]`, chave que o `llm_service` nunca preencheu — o `Classification` não tem esse campo. Consequência: `compose_caption` recebia `total_parts=1` sempre e o rótulo "(Parte 1/2)" **nunca apareceu em post nenhum**, nem nas séries. Quem sabe quantas partes existem é quem as criou, então o número agora é `len(parts)` do run, mandado explicitamente no `ScheduleRequest`. A chave antiga é ignorada.

**Compatibilidade.** `total_parts` (default 1) e `follows_at` (default `None`) são opcionais no `ScheduleRequest`: um poster novo aceita requests de um orchestrador velho, que simplesmente não encadeia. Deploy dos dois serviços não é atômico.

---

## Frase gancho (`hook`)

O gancho sempre foi **regra de escrita** no prompt de refino ("a primeira frase deve prender em 2 segundos"), nunca um dado. Passou a ser campo (`RefineResponse.hook`) porque o pipeline precisa narrá-lo isolado, e para isso é preciso saber onde a frase termina — coisa que texto corrido não informa.

**O campo é sempre preenchido.** O prompt pede o `hook` copiado literalmente da primeira frase da parte 1; se o modelo omitir, um validador em `RefineResponse` deriva a frase de `parts[0]` (`derive_hook`). O contrato do endpoint não pode depender da obediência do modelo — quem consome trata `hook` vazio como "não há gancho", e isso só deve acontecer quando não há roteiro.

**Regra de derivação.** Fim de frase = pontuação terminal (`. ! ? …`, mais aspas/parênteses de fechamento) **seguida de espaço** — exigir o espaço é o que impede `R$ 3.5 mil` de virar fim de frase. Sem pontuação terminal, a parte inteira seria o "gancho", então há um teto de 200 caracteres com corte na última palavra inteira. O teto existe só para a derivação; gancho vindo do modelo é usado como veio.

**O gancho não é removido de lugar nenhum.** `parts[0]` continua abrindo com ele, `PipelinePart.script` guarda o roteiro inteiro e a narração da parte é gerada com o texto completo — o campo `hook` é uma *cópia identificada*, não um recorte.

**Quem não é montado é o áudio.** Na parte que já abre pela frase (a parte 1, por construção), tocar o `hook.mp3` na frente da narração faria o vídeo dizer a mesma coisa duas vezes seguidas, nos segundos em que a retenção se decide. Então nessa parte o arquivo entra **mudo**, só medindo por quanto tempo o card fica na tela, e quem diz a frase é a narração inteira. Ver "Abertura do vídeo" → "O gancho é dito uma vez só".

### Áudio do gancho

O orchestrador narra o `hook` numa etapa própria (`_run_hook_tts`), entre o refino e o processamento das partes, e guarda `hook_audio_key` / `hook_srt_key` no `pipeline_run`.

**Key própria via `label`.** O `tts_service` montava a key sempre como `part_{n}`; o gancho sobrescreveria `part_1.mp3`, que é a narração da parte inteira. O `GenerateRequest` ganhou `label` opcional (`hook` → `audio/{run_id}/hook.mp3`, `subs/{run_id}/hook.srt`). O pattern `^[a-z0-9][a-z0-9_-]{0,63}$` não é cosmético: a key é montada por interpolação e um label com `/` ou `..` escreveria fora do run.

**A etapa é degradável.** Falha no TTS do gancho vira `warning` e o run segue; falha no TTS de uma parte continua derrubando o run. A assimetria é intencional — o gancho já está narrado dentro da parte 1, então esse arquivo é um extra, e perder um extra não pode custar o vídeo, que é o que o pipeline existe para entregar. A ausência fica auditável em `hook_audio_key` nulo.

**Compatibilidade.** `LLMClient.refine` lê `data.get("hook")`, então um `llm_service` antigo (sem o campo) apenas resulta em run sem gancho, não em erro — o deploy dos dois serviços não é atômico. Mesma lógica no `tts_service`: sem `label`, as keys são exatamente as de antes.

---

## Velocidade da Narração

Definida no `template.json`, no bloco `narration.rate` (padrão `+50%` desde 31/08/2026, era `+30%`), e aplicada pelo `tts_service`. No provider `edge` vai para `edge_tts.Communicate(..., rate=...)`; no `azure`, para o `<prosody rate='...'>` do SSML. É o mesmo parâmetro nos dois casos, então trocar de provider não muda o ritmo da narração.

**Por que no template, e não só em env var.** Velocidade de fala é decisão de design do formato, igual à tipografia da legenda e ao timing da edição — que já moram no `template.json`. Um template de drama quer narração pausada; um de curiosidades quer ritmo acelerado. Com env var, trocar de formato exigiria redeploy do `tts_service` e o valor seria global para todos os templates ao mesmo tempo.

**Como o rate chega ao TTS.** O `template.json` vive no MinIO e hoje só era lido pelo `blender_worker` **na hora do render** — tarde demais, já que o TTS roda antes. A cadeia:

```
template.json (narration.rate)
  └→ blender_worker: GET /templates/{id}/config   (baixa do MinIO, devolve o JSON parseado)
      └→ orchestrator: _narration_rate()          (1× por run, em _process_all_parts)
          └→ tts_service: POST /generate {rate}   (override do TTS_RATE)
              └→ provider ativo (edge: Communicate(rate=...) | azure: <prosody rate>)
```

Decisão: o orchestrador **não lê o MinIO nem parseia `template.json`**. O `blender_worker` é dono dos templates, então serve o config por HTTP. Isso evita duplicar o parsing e o conhecimento de bucket/key em dois serviços. O endpoint expõe o `template.json` inteiro, não só `narration` — outros campos vão precisar do mesmo caminho.

**Precedência:** `narration.rate` do template → `TTS_RATE` do `tts_service` → `+50%`. O env var deixa de ser a fonte primária e vira fallback: cobre templates sem o bloco `narration` (compatibilidade) e chamadas diretas ao `tts_service` fora do pipeline. Os dois são mantidos **no mesmo valor** — divergi-los faria o caminho de fallback narrar num ritmo diferente do resto do canal, e a diferença só apareceria no vídeo pronto.

**Degradação.** Falha ao ler o config — template sem bloco, blender_worker fora do ar, JSON inválido — cai no `TTS_RATE` com warning, sem derrubar o run. Narração é estética; o render, não. Mesmo critério da remoção de silêncio (degrada) versus a transcrição (derruba).

**Validação em um lugar só.** A regex `^[+-]\d+%$` mora no `tts_service` (`validate_rate`), usada tanto no boot (env var) quanto no campo da request (`422`). O orchestrador repassa o valor sem validar — duplicar a regra criaria duas fontes de verdade que divergem com o tempo.

**Por que no motor de voz, e não em pós-processamento.** Acelerar o MP3 depois de pronto (resample no `pydub`/ffmpeg) sobe o pitch junto e a voz vira "esquilo"; corrigir isso exige time-stretch, que introduz artefato. O `rate` do edge-tts é `prosody rate` do SSML — a Microsoft sintetiza já no ritmo pedido, com o pitch intacto e sem perda de qualidade. Custo zero: não há etapa de áudio extra no pipeline.

**Formato.** Percentual com sinal obrigatório (`+15%`, `-10%`, `+0%` desliga). O edge-tts só rejeitaria o formato na hora de sintetizar, o que transformaria um erro de config em falha de request no meio do pipeline — daí a validação antecipada nos dois pontos de entrada.

**Ordem no pipeline.** O `rate` age na síntese, antes de tudo. Logo a remoção de silêncio e a transcrição já operam sobre o áudio acelerado, e o SRT sai com o timing certo sem nenhum ajuste — mesma razão pela qual a transcrição roda depois do corte de silêncio (ver "Legendas"). Nada no `blender_worker` muda: ele consome o par MP3+SRT como sempre.

**Efeito no tamanho do roteiro.** A banda de 10–40s é de fala, não de texto, e narração mais rápida faz caber mais história no mesmo tempo. O LLM escreve o roteiro sem conhecer o `rate`: `NARRATION_WPM` é a ponte, e já embute o `+50%` do template publicado. ⚠️ **As duas andam juntas** — a subida de `+15%` para `+30%` levou a constante de 170 para 195, e a de `+30%` para `+50%` a levou para 225. Mexer no rate sem mexer na constante desloca a banda inteira: a 225 wpm declarados contra um template ainda em `+30%`, as 150 palavras do teto viram 46 segundos em vez de 40. Sem erro em lugar nenhum — só o vídeo saindo mais longo do que o formato diz.

**Ferramenta de edição do template (fora deste caminho).** `blender_worker` também expõe
`POST /timelines/validate` e `GET /timelines/schema` — validação de um template **VSEL**
(`docs/edicao_declarativa.md`) sem precisar renderizar — mais
`POST /timelines/preview/frame` e `POST /timelines/preview/clip`, que já rodam o Blender de
verdade contra um `video_id` real pra devolver um frame ou um clipe curto de preview. São
rotas de dev/operador, não parte de `POST /jobs` nem deste fluxo de `rate`: nenhum job real
aponta pra esse formato ainda. Ver `blender_worker/CLAUDE.md` § "Fase 3, níveis 2/3" para a
API completa.

---

## Voz da Narração

As histórias do acervo são contadas em **primeira pessoa**, então o narrador tem gênero e a voz precisa concordar com ele. Até aqui toda narração saía em `TTS_VOICE` — uma voz feminina —, inclusive a de narrador homem. Isso não é detalhe de acabamento: é a primeira coisa que o espectador percebe, acontece nos segundos em que a retenção se decide, e nenhum acerto de ritmo ou de loudness compensa.

**Quem decide é quem lê o roteiro inteiro.** O gênero do narrador só existe no texto, e o único ponto do pipeline que vê o texto completo antes do TTS é o refino. Daí o campo nascer lá:

```
llm_service: RefineResponse.narrator_gender   (male | female | unknown)
  └→ orchestrator: PipelineRun.narrator_gender  (migration 005)
      └→ tts_service: POST /generate {narrator_gender}
          └→ resolve_voice() → TTS_VOICE_MALE | TTS_VOICE_FEMALE | TTS_VOICE
              └→ provider ativo (edge: Communicate(voice) | azure: <voice name=...>)
```

**Trafega o gênero, nunca o nome da voz.** O que o orchestrador manda é um fato sobre o roteiro; que voz corresponde a ele é decisão do `tts_service`, que conhece os providers. Como `edge` e `azure` servem as mesmas vozes neurais, `male` é uma string só para os dois, e um provider novo mexe num arquivo só (`tts/voices.py`). Um provider novo precisa aceitar `voice` no construtor — senão a voz do narrador é ignorada em silêncio ao trocar de provider, exatamente a armadilha já documentada para o `rate`.

**Padrões:** `TTS_VOICE_MALE` = `pt-BR-AntonioNeural`, `TTS_VOICE_FEMALE` = `pt-BR-FranciscaNeural`. O par foi escolhido para soar como duas pessoas da mesma idade e do mesmo registro: o que muda entre eles é o gênero, não o personagem. `TTS_VOICE` (`pt-BR-ThalitaNeural`) deixa de ser a voz de todo vídeo e vira o fallback de `unknown`.

**`unknown` não é falha.** História sem narrador identificável — ou narrada em terceira pessoa — é resultado normal, e mantém a voz padrão. O prompt manda explicitamente **preferir `unknown` a chutar**, e proíbe deduzir pelo assunto ou pelo público: errar o gênero custa mais que não escolher.

**Não confundir com `classification.target_audience.gender`.** Um é quem narra, o outro é para quem se narra, e os dois divergem o tempo todo — história de homem com público majoritariamente feminino é o caso comum do corpus. São campos separados, com regras separadas no prompt, e há teste nos dois serviços fixando a distinção.

**Normaliza em vez de rejeitar, nas duas pontas.** O valor nasce numa classificação de LLM: o `llm_service` reduz qualquer coisa fora de `male`/`female` a `unknown`, e o `tts_service` faz o mesmo com o que chega na request — sem `422`. O pior caso de errar é a voz que todo vídeo usava antes disso existir; um erro de validação custaria o run inteiro por um campo cosmético. O gênero recebido e a voz resolvida vão no log da rota, então um modelo que comece a responder `"masculino"` é visível sem ser fatal. Contrasta com o `rate`, cuja validação é estrita: formato errado ali é erro de configuração, não de julgamento.

**O gancho vai na mesma voz.** `_run_hook_tts` manda o mesmo `narrator_gender` das partes, pela mesma razão pela qual manda o mesmo `rate`: o gancho é montado na frente da narração, e basta a voz ou a velocidade divergir para o vídeo abrir com dois narradores. O valor é do **run**, não da parte — uma história dividida é a mesma pessoa contando.

**Compatibilidade.** `LLMClient.refine` lê `data.get("narrator_gender")` com fallback `unknown`, e o `TTSClient` **omite** o campo quando não há gênero (não manda `null`), então um `llm_service` antigo ou uma chamada direta ao `tts_service` produzem exatamente o comportamento anterior. O deploy dos três serviços não é atômico.

---

## Qualidade do Áudio da Narração

**O teto era o provider.** O `edge-tts` tem o formato de saída hardcoded em `audio-24khz-48kbitrate-mono-mp3` (`edge_tts/communicate.py`) — não é parâmetro, é constante, porque o endpoint gratuito do Edge só serve esse formato. A 24 kHz de sample rate, nada acima de ~12 kHz existe no sinal: é matemática, não compressão. Era essa a causa da narração soar abafada, e nenhum pós-processamento recupera banda que nunca foi sintetizada.

**Provider `azure`.** Azure Speech (Cognitive Services) expõe as **mesmas vozes neurais** do edge (`pt-BR-ThalitaNeural` etc.) via REST, com o formato de saída escolhido pelo cliente. Default `audio-48khz-192kbitrate-mono-mp3`. Decisão: é a menor mudança possível que resolve o problema — mesma voz, mesmo `TTS_RATE`, mesma interface `BaseTTSClient.generate(text) -> bytes`, mesma key MinIO. Só o transporte muda. O ElevenLabs resolveria também, mas trocaria a voz do canal e custa por caractere; o Azure tem free tier de 500k caracteres/mês.

O `edge` continua registrado como fallback sem-configuração — útil em dev e quando não há key.

⚠️ **Decisão (14/08/2026): produção fica no `edge`.** O `azure` está implementado e testado, e o parágrafo acima continua descrevendo por que ele é melhor — mas ele exige uma conta e uma key para manter, e a diferença de nitidez não bloqueia publicação. O default do código (`TTS_PROVIDER`, `config.py`) é `edge` e permanece assim; a narração em produção sai a 24 kHz / 48 kbps, com o abafamento descrito acima. Reverter é preencher `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION` e apontar `TTS_PROVIDER=azure` — sem migration, sem mudança de contrato, e com a validação de boot abaixo garantindo que a troca falhe visível se a key faltar.

**Falha no boot, não na request.** `AZURE_SPEECH_KEY` e `AZURE_SPEECH_REGION` são validados em `TTSEnvSettings._check_provider_key` quando `TTS_PROVIDER=azure`. Mesma razão do `TTS_RATE`: config errada tem que derrubar o start, não virar `502` no meio de um pipeline run que já pagou LLM.

**SSML é escapado.** O corpo da request é SSML, então um roteiro com `&` ou `<` produziria XML malformado e um `400` do Azure. `build_ssml` passa o texto por `xml.sax.saxutils.escape`. Roteiros vêm de LLM e do Reddit — assumir que não têm caractere especial é assumir errado.

**Gerações lossy.** Cada round-trip MP3→MP3 é uma geração lossy nova. A cadeia tinha três (síntese → `remove_silence` → AAC do Blender), e a do meio era gratuita: o ffmpeg rodava sem `-b:a`, herdando um default do `libmp3lame` derivado do sample rate — medido em **64 kbps** para 48 kHz mono e **32 kbps** para 24 kHz mono. Um source de 192 kbps do Azure seria esmagado a um terço do bitrate a cada passada. Agora `process_audio` encoda uma vez só, com bitrate e sample rate explícitos, e **devolve os bytes intactos quando não há filtro a aplicar** (`REMOVE_SILENCE=false` + `NORMALIZE_AUDIO=false`), em vez de re-encodar por nada.

---

## Normalização de Loudness

`process_audio` (`audio/postprocess.py`) substituiu `audio/silence.py`. Faz corte de silêncio e normalização de volume **na mesma passada de ffmpeg**, pela razão acima: duas passadas seriam duas gerações lossy.

**Alvo −16 LUFS / −1.5 dBTP** (`LOUDNESS_TARGET_LUFS`, `NORMALIZE_AUDIO`). −16 LUFS é a referência das plataformas de vídeo: entregar no alvo evita que o normalizador do TikTok mexa no vídeo depois de publicado. Sem isso o nível era o que o motor de voz decidisse entregar — parte 2 mais baixa que parte 1 do mesmo vídeo, e a narração ora sumindo sob a trilha ora estourando acima dela.

**Ordem: trim antes de loudnorm.** O `loudnorm` mede o stream inteiro; silêncio de cabeça e cauda puxa a loudness medida para baixo e o filtro compensa deixando a voz mais alta que o alvo. Cortar primeiro faz a medição ser só de fala. Garantido por teste (`test_chain_trims_before_normalizing`).

**`aresample` obrigatório depois do `loudnorm`.** Em single-pass o `loudnorm` emite 192 kHz independentemente da entrada; sem o resample explícito o encoder herdaria essa taxa e o arquivo ficaria absurdamente maior sem ganho nenhum. É uma pegadinha do filtro, não uma escolha — também coberta por teste.

**Highpass em 80 Hz.** Voz não tem conteúdo útil abaixo disso — só rumble e thump de plosiva, que consomem headroom que o `loudnorm` daria à voz. Na prática o efeito é marginal em TTS: a saída do `edge` já entra com −40 dB nessa banda, não há rumble a remover. É apólice de seguro para fontes que tenham, não o que faz o áudio melhorar.

**Bitrate e sample rate casam com a fonte por padrão.** `AUDIO_BITRATE` e `AUDIO_SAMPLE_RATE` vazios fazem `process_audio` consultar o `ffprobe` e reproduzir o que a entrada já era. Decisão tomada depois de medir: com o provider `edge` (24 kHz / 48 kbps), forçar 48 kHz / 192 kbps gerou um arquivo **4× maior** — 270 KB contra 68 KB — com loudness idêntica (−16,5 vs −16,6 LUFS) e o mesmo espectro vazio acima de 13 kHz. Reamostrar para cima não inventa banda; só infla storage. O override explícito continua disponível para quando fizer sentido, e o `/health` reporta `"source"` quando não há um.

**Degrada em silêncio.** Falha no pós-processamento é logada como warning e o áudio original segue para o MinIO. Diferente da transcrição, que derruba a request com `502`: um áudio sem normalizar ainda produz vídeo; um SRT ausente, não.

---

## Corte de silêncio: `max_pause_ms` é um teto, não um gatilho

O parâmetro foi lido errado desde o início. `MIN_SILENCE_MS=500` parecia significar "só remove silêncios acima de 500ms", mas o `silenceremove` do ffmpeg **copia o áudio até que `stop_duration` de silêncio já tenha passado**, e só então para de copiar. O número é ao mesmo tempo o limiar de detecção **e a quantidade de silêncio que fica para trás**.

Consequência: pausa de 1,5s e pausa de 3,0s saíam **as duas com 0,52s**. A narração continuava soando esburacada não apesar do corte, mas por causa dele — todo intervalo longo era normalizado para meio segundo de nada. Medido num clipe de 7,5s com as duas pausas.

Por isso o parâmetro foi renomeado para `max_pause_ms` e o default caiu de 500 para **200**: uma pausa menor que o teto passa intacta, toda pausa maior sai em exatamente `max_pause_ms`. `MIN_SILENCE_MS` continua sendo aceito como alias depreciado — sempre foi o mesmo número, e ignorá-lo mudaria em silêncio um `.env` já calibrado.

**`stop_silence` não é usado de propósito.** Ele *soma* ao que é preservado (medido: `0.1` deixou 0,62s de pausa residual), então só consegue alongar pausas — sob um teto baixo, esticaria uma pausa para além do comprimento original.

Seis testes fixam esse comportamento em `test_postprocess.py`, incluindo o caso "o teto nunca alonga uma pausa".

### CLI de calibragem (`tts_service/scripts/cut_silence.py`)

Limiar de silêncio é escolha empírica: `-40dB` corta bem uma voz e come o começo das palavras de outra. Sem uma forma de rodar avulso, testar um valor exigia editar env var, subir o serviço, disparar um pipeline run e esperar TTS + Whisper + render — para então ouvir o resultado. O CLI reduz o ciclo a um comando sobre um arquivo local, e reporta duração antes/depois e percentual cortado, que é o número que diz se o ajuste foi longe demais.

**Um único ponto de verdade.** O CLI chama o mesmo `process_audio` da rota `/generate` — não reimplementa a cadeia de filtros. Um limiar calibrado no terminal descreve exatamente o que a produção vai fazer; se fossem dois códigos, a calibragem mediria a ferramenta em vez do pipeline. Pela mesma razão a normalização de loudness fica **ligada por padrão**, como em produção; `--no-normalize` existe para isolar o efeito do corte quando se quer ouvir só ele.

Decisões do CLI:

- **Nunca escreve por cima sem `--force`.** A saída padrão é `<nome>.trimmed.mp3` ao lado da entrada, e uma segunda rodada com os mesmos parâmetros aborta em vez de sobrescrever. Calibrar é rodar o mesmo arquivo várias vezes; perder silenciosamente o resultado anterior estragaria justamente a comparação.
- **Saída igual à entrada é recusada.** O original é o insumo da próxima tentativa — cortar em cima dele acumularia cortes de rodadas anteriores e o número reportado deixaria de significar o que diz.
- **`--dry-run` processa de verdade.** Roda o ffmpeg e mede, só não grava. Um dry-run que apenas estimasse o corte não responderia à pergunta que se está fazendo.
- **Falha de um arquivo não derruba o lote.** Vários arquivos por invocação, erro reportado por arquivo e código de saída diferente de zero no fim. Exceção: ffmpeg ausente do PATH aborta na hora — repetir o mesmo erro por arquivo não informa nada.
- **Relatório em ASCII.** O repo é desenvolvido no Windows, onde o console é cp1252 e um `→` no `print` levanta `UnicodeEncodeError` no meio da execução. Verificado ao vivo: a versão com seta Unicode passava nos testes (o capsys captura em UTF-8) e quebrava no terminal real.

---

## Legendas (word-level)

**Origem.** A legenda é derivada do áudio, não do roteiro. O `tts_service` transcreve o MP3 já gerado (e já com silêncios removidos) via `faster-whisper` com `word_timestamps=True`, e emite um SRT com **uma entrada por palavra**. Decisão: o roteiro e a narração divergem (o TTS abrevia, o corte de silêncio desloca o tempo), então estimar timing por WPM sempre dessincroniza. Transcrever o artefato final é a única fonte de verdade.

O SRT vai para `subs/{run_id}/part_{n}.srt` e o orchestrador só repassa a key — ele não gera mais SRT.

**Pré-requisito de timing — `fps_base`.** O FPS efetivo do Blender é `render.fps / render.fps_base`, e o `fps_base` é gravado no `.blend`. O `edit_video.py` define os dois (`fps = frame_rate`, `fps_base = 1.0`) para que "frame" no `template.json` e no SRT signifique a mesma coisa que na cena renderizada. Com o `fps_base` do template atual (`0.1`) e só o `fps` sobrescrito, a cena rodaria a 10× a velocidade pretendida e nenhum timing em frames bateria com o áudio.

**Renderização (`blender_worker`).** Cada entrada do SRT vira uma text strip no canal de legendas do VSE. Regras, todas em `build_subtitle_timeline`:

- **Offset de sincronia** — os timestamps do SRT são relativos ao início da narração, mas a voice strip começa em `speech_start + 1`. Toda entrada é deslocada por esse mesmo offset. Sem isso a legenda adianta pelo tamanho do intro.
- **Hold até a próxima palavra** — o fim de cada strip é estendido até o início da seguinte. Whisper deixa micro-vãos entre palavras; respeitá-los literalmente produz flicker.
- **Cap do hold (`max_hold_seconds`, padrão 0.4s)** — o hold não passa disso. Numa pausa real da narração a legenda sai da tela em vez de deixar a última palavra pendurada.
- **Sem overlap** — o fim é sempre clampado ao início da próxima. Duas strips sobrepostas no mesmo canal fazem o Blender realocar uma delas para outro canal, quebrando a composição.
- **Duração mínima de 1 frame** — palavras mais curtas que um frame existem; strip de duração zero é inválida.
- **Merge, nunca descarte** — duas palavras que caem no mesmo frame são concatenadas numa strip só. Nenhuma palavra é perdida silenciosamente.
- **Fade só na borda de vão** — `fade_frames` (padrão 3) se aplica apenas quando há vão real antes/depois, e na primeira e última strip. Entre palavras adjacentes a troca é corte seco: fade em cada palavra é justamente o que lê como piscada.
- **Fade nunca maior que ⅓ da strip** — em palavras curtas um fade fixo inverteria a ordem dos keyframes de `blend_alpha` (fade-out antes do fade-in), fazendo a palavra piscar ou nunca atingir opacidade cheia.
- **Rise em toda palavra** — cada palavra nasce `rise_offset` (padrão 0.025 da altura) abaixo da posição de repouso `SUBTITLE_Y` (0.05) e sobe até ela em `rise_frames` (padrão 4), com `SINE`/`EASE_OUT`. Decisão: o "pop" por palavra é o que dá ritmo à legenda, mas fazê-lo com opacidade pisca. Movendo a posição em vez da transparência, o efeito pode valer para **todas** as palavras — inclusive as coladas — sem o artefato visual. Fade e rise são ortogonais de propósito: o fade marca fronteira de silêncio, o rise marca troca de palavra.
- **Rise nunca maior que `duração - 1`** — a palavra precisa chegar à posição de repouso antes da strip acabar.
- **Interpolação fixada explicitamente** — keyframes novos herdam a preferência do Blender de quem executa (`keyframe_new_interpolation_type`); um dev com `CONSTANT` configurado veria um pulo em vez da subida. `_set_easing` grava `SINE`/`EASE_OUT` nos pontos após inserir.

**Tipografia.** A fonte, a cor e o contorno são resolvidos uma vez por job em `resolve_subtitle_style()` (função pura, sem `bpy`) e aplicados a cada strip por `apply_text_style()`.

**Posição vertical.** `y_position` (padrão `0.5`, centro exato) é fração da altura do frame, com as strips em `align_y = "CENTER"`. Ancorar o meio do próprio texto — e não a baseline — é o que faz o mesmo número significar o mesmo lugar para uma palavra alta e uma baixa. Valor clampado a 0..1: fora do frame a legenda simplesmente não aparece, o que se leria como legenda ausente e não como erro de configuração.

O template publicado usa **0.474** — 50px abaixo do centro exato numa altura de 1920 (`50/1920 = 0.026`). Verificado renderizando a mesma palavra no mesmo corpo mudando só `y_position`: o centro do glifo andou exatos 50,0px. Mudança de posição se mede assim, com o corpo fixo — comparar frames que diferem em tamanho **e** posição acusa ~3px a menos, porque a caixa de altura-x de um corpo menor se assenta de outro jeito em relação à âncora.

**Auto-ajuste por palavra.** `font_size` é **teto, não valor fixo**. Legenda word-level é centralizada e de palavra única — o Blender não quebra uma palavra só em duas linhas e não avisa quando ela ultrapassa o frame; ela simplesmente sai pelas duas bordas. Medido a 1080px: `"procedimento,"` já ocupava 1037px no tamanho 140, e a partir de 170 era cortada.

Por isso cada palavra é medida antes de ser aplicada, e só as que não cabem encolhem. Numa narração real de 178 palavras, o tamanho 190 exigiu ajuste em 13 (7%), a mais longa caindo para 133. A medição usa `blf` — o mesmo rasterizador que a text strip do VSE —, então não há divergência entre o que se mede e o que se renderiza. Se a fonte não carregar no `blf`, o auto-ajuste é pulado em vez de medir com um tipo diferente do que será desenhado.

⚠️ **`template.json` mora no bucket, não no repo.** O `render_job` baixa `templates/template.json` do MinIO/R2; editar a cópia versionada não muda render nenhum enquanto o arquivo não for enviado. Os dois divergiram: o repo declarava `font_size: 140` enquanto o template publicado não tinha bloco de tipografia algum, e todo render saiu com os 60 embutidos do Blender. `font_size` é a única propriedade sem default no código — exatamente por isso foi a que regrediu em silêncio, já que fonte, cor e contorno continuaram vindo dos `DEFAULT_*` e nada parecia quebrado.

- **Futura Bold como padrão, com fallback em cadeia** — `resolve_font_path()` testa, em ordem: `subtitles.font_path` do template → `assets/fonts/Futura-Bold.ttf` → `.otf` → `DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`, já na imagem). Se nada existir, retorna `None` e a strip fica com a fonte embutida do Blender. Decisão: fonte ausente é problema de estilo, não motivo para falhar um render que já consumiu LLM, TTS e transcrição — degrada o visual, nunca o job.
- **A fonte mora em `blender_worker/assets/fonts/`, não na raiz do monorepo** — o compose usa `build: ./blender_worker`, então o build context da imagem é o diretório do serviço. Um `.ttf` na raiz do monorepo é invisível para o `COPY . .` do Dockerfile e o container renderizaria em DejaVu sem nenhum erro visível. O nome do arquivo é case-sensitive no Linux (`Futura-Bold.ttf`).
- **Binários marcados no `.gitattributes`** — o repo é desenvolvido no Windows com `core.autocrlf=true`. Sem regra explícita, o git decide por heurística de conteúdo se converte newlines; um `.ttf` ou `.blend` convertido quebra em tempo de render, não em tempo de commit.
- **Datablock carregado uma vez** — `bpy.data.fonts.load(..., check_existing=True)` fora do loop. Um vídeo tem centenas de strips word-level; carregar por strip criaria centenas de datablocks duplicados no `.blend`.
- **Branco com contorno preto** — o fundo é vídeo em movimento, então não há cor de texto que funcione sozinha: texto branco desaparece em cena clara. O contorno resolve isso sem tarja/caixa atrás do texto, que roubaria área da tela num formato vertical.
- **`outline_width` padrão 0.24, não 0.05** — 0.05 é o padrão do Blender e renderiza como um fio de cabelo que some sobre fundo claro. 0.24 é o limite superior que ainda preserva as formas das letras: acima de ~0.30 os contornos se fundem entre glifos vizinhos e as contraformas das letras redondas começam a fechar, o que custa legibilidade na velocidade de uma palavra por vez. O valor é clampado em 0..1 na leitura do template (o Blender clampa em silêncio; clampar aqui evita que um valor errado renderize como outra coisa).
- **`font_size` não tem padrão no código** — quando ausente, o tamanho que o Blender deu à strip (60) é preservado, e 60 é pequeno demais para 1080×1920. O `template.json` define 160, que ocupa a largura útil sem encostar nas bordas (a 160 o auto-ajuste toca só 2 das 178 palavras de uma narração real). A decisão fica no template e não no código porque corpo é escolha de design por template, não invariante do pipeline.
- **Requer Blender 4.2+** — `use_outline`/`outline_color`/`outline_width` só existem a partir do 4.2 (versão fixada no Dockerfile). Em build anterior o script levanta `AttributeError` em vez de descartar o contorno silenciosamente: legenda sem contorno é ilegível, então falhar alto é o comportamento correto.
- **View transform importa** — o `template.blend` usa `Standard`, então branco 1.0 sai branco 1.0. Sob `AgX` (padrão de fábrica do Blender) o mesmo branco renderiza em ~0.78 e o contorno perde contraste. Um template novo precisa manter `Standard`.

**Configuração** — bloco opcional `subtitles` no `template.json`:

```json
"subtitles": {
  "fade_frames": 3,
  "max_hold_seconds": 0.4,
  "rise_frames": 4,
  "rise_offset": 0.025,
  "font_path": "assets/fonts/Futura-Bold.ttf",
  "font_size": 90,
  "color": [1.0, 1.0, 1.0, 1.0],
  "use_outline": true,
  "outline_color": [0.0, 0.0, 0.0, 1.0],
  "outline_width": 0.12
}
```

Cores aceitam `[r, g, b]` ou `[r, g, b, a]` (alfa assume 1.0); qualquer outro número de canais levanta `ValueError` na leitura do template, não no meio do render.

`fade_frames: 0` desliga o fade (corte seco); `rise_frames: 0` desliga a subida.

---

## Integridade dos assets e duração do render

### Fundo sem imagem decodificável

Um arquivo sem faixa de vídeo utilizável **ainda carrega** como movie strip: o Blender devolve uma strip com um frame de placeholder em vez de levantar erro. Sem checagem, o render conclui com sucesso e produz fundo preto pela duração inteira — o job reporta `completed`, o MP4 tem tamanho e duração plausíveis, e nada a jusante distingue um asset quebrado de um deliberadamente escuro. Foi exatamente o que aconteceu com o `assets/background.mp4` de 1 KB versionado no bucket.

`check_movie_strip()` roda logo após a strip ser adicionada e derruba o job se `frame_duration < 2`, nomeando o arquivo na mensagem.

**Por que 2 frames.** Medido contra a RNA real: o stub de 1 KB reporta `frame_duration=1`; um clipe válido de 5s a 30fps reporta `150`. Dois frames é o piso que separa os dois casos. Um fundo genuinamente de 1 frame é imagem estática e pertence a uma image strip, não aqui.

**Falhar é melhor que degradar.** Diferente da fonte de legenda — cuja ausência degrada a estética e nunca derruba o render — um fundo inexistente não degrada nada: destrói o vídeo. Não há resultado parcial útil a preservar, então a falha é dura e imediata.

### Beds não definem a duração

`content_end_frame()` calcula `scene.frame_end` ignorando os canais de **música e vídeo de fundo**. Ambos são *beds*: cada um tem o tamanho que o asset por acaso tem, e nenhum diz nada sobre onde a história termina — só a narração e sua legenda dizem.

Medido: um fundo de 90s sob narração de 68s renderizava 22s de ar morto depois da última palavra sair da tela. A regra anterior excluía apenas a música; a falha passou despercebida porque o fundo placeholder tinha um único frame e nunca era o mais longo.

**O caso espelhado é resolvido esticando o bed, não encurtando a timeline.** Um bed *mais curto* que a narração deixava o final preto — medido: clipe de 45s sob narração de 71s, 26s de preto com legenda por cima e nenhum erro. Encurtar a timeline até o fundo cortaria narração no meio da frase, então quem cede é o bed: `background_repeats()` calcula os frames de início das cópias necessárias e `extend_background()` as deposita encostadas, sem sobreposição (strips sobrepostas o Blender realoca de canal) e sem vão (um vão é um frame preto).

Isso *era* documentado como problema de asset, e era, enquanto todo render compartilhava um único arquivo longo escolhido a dedo. Com o fundo vindo de uma biblioteca de clipes curtos, um clipe menor que a narração passou a ser o caso normal — a regra mudou porque o desenho mudou.

`MAX_BACKGROUND_REPEATS` (60) limita o caso degenerado: um arquivo quase vazio pediria milhares de strips. Passando disso o final volta a ficar preto, que é o comportamento antigo.

**Coerção de tipo na fronteira do RNA.** `background_repeats()` recebe `strip.frame_start` e `strip.frame_duration`, que o Blender devolve como **float**, e `sequences.new_movie()` aceita só `int` — a repetição morria com `TypeError` no meio da montagem. Nunca apareceu em produção porque um clipe mais longo que a narração não pede repetição nenhuma, e o float então nunca chega à API; apareceu no primeiro render de validação com clipe curto. Os números são convertidos dentro da função pura, não no chamador, para que a regra e a coerção sejam testadas juntas.

### O vídeo não tem finalização

O último frame do vídeo é a última palavra da narração. Não existe cartela de encerramento, bloco de outro nem batida final — e as chaves `timing.outro_start` / `timing.outro_end` do `template.json`, herdadas do rascunho, nunca foram lidas por código algum. Saíram do template versionado, porque uma configuração que descreve uma etapa inexistente é pior que nenhuma: a próxima sessão a implementa.

A única marca de encerramento é a **trilha sumindo por baixo da última frase**: `music_fade_start()` conta o fade de trás para frente, `last_frame - fade_frames`, com `music.fade_out_seconds` (1,5s por padrão) no template.

**Por que contar do fim e não de um frame fixo.** `timing.music_fade_out` era o frame 840 contra um `frame_end` de 900 — 2 segundos de fade no rascunho de 30s para o qual foi escrito. Com `frame_end` passando a ser decidido pela narração, esse número virou outra coisa: quando o vídeo é **mais curto** que 840 frames, os keyframes entram fora de ordem (1, 840, 701) e o Blender os reordena por frame, deixando `0.2 → 0.0 → 0.2`. O resultado é o vídeo inteiro em declínio.

Medido no mesmo timeline de validação — 23,37s / 701 frames, trilha isolada (voz mutada no mixdown, senão as últimas palavras dominam a janela justamente onde o fade acontece):

- **Antigo** (keyframes 1/840/701, janelas de 2s): -26,1 dBFS em 0s → -33,6 em 12s → -55,7 em 20s → **-73,4 no fim**. Queda contínua do primeiro ao último segundo.
- **Agora** (keyframes 1/656/701, janelas de 0,5s): -26,0 dBFS em 0s, ainda -26,0 em 21,0s, -26,0 em 21,5s, então -27,6 em 22,0s, -33,9 em 22,5s e -49,0 na última janela. O volume só se move nos 1,5s finais.

Era este o defeito por trás da impressão de "trilha que não está lá": ela estava no arquivo, no canal certo, no volume certo — e em queda desde o primeiro segundo.

**Segundos, não frames, na configuração.** Um fade é uma duração musical: o mesmo número tem que significar o mesmo encerramento a 30 ou a 60fps. `music_fade_frames()` faz a conversão; `0` desliga o fade (é como um template diz "sem fade"), e um timeline sem espaço para o fade não recebe keyframe nenhum em vez de recebê-los antes do próprio início da trilha.

### Meio segundo de respiro depois da última palavra

`scene.frame_end` é `content_end_frame(...) + end_padding_frames(...)`. A narração continua decidindo onde o vídeo acaba — mas não no frame exato em que ela para.

**Por que o corte rente não funciona.** Duas coisas se somam: o fim do strip de voz já é arredondado para o frame, e o último pacote de áudio do MP4 cai justamente sobre o corte. A consoante final morre, e o vídeo lê como se tivesse acabado no meio da palavra — o oposto do que "termina na última palavra" deveria significar.

`DEFAULT_END_PADDING_SECONDS` é **0,5s**: tempo de a palavra terminar de ser dita e de o fade da trilha completar, curto o bastante para não virar ar morto (o defeito que a regra dos beds foi criada para eliminar). Configurável no bloco `narration` do template (`tail_seconds`), o mesmo bloco de onde já sai o `rate`; `0` volta ao corte rente. Valor negativo é chão em 0 — um template não pode terminar o render **antes** da narração, o que trocaria uma sílaba cortada por uma palavra inteira perdida.

**Segundos, não frames**, pela mesma razão do fade da música: é uma duração de escuta e tem que significar a mesma pausa a 30 ou a 60fps.

**A ordem em `main()` importa.** O respiro entra **antes** de `extend_background()` e de `music_fade_start()`: sem isso o fundo não cobriria os frames extras (final preto, o defeito já conhecido) e o fade terminaria meio segundo antes do último frame.

Default no código porque o `template.json` vive no bucket — o template publicado não tem `tail_seconds`, e precisa ganhar o respiro sem republicação.

### Rotação de fundo (`orchestrator/backgrounds.py`)

`pick_background(keys, run_id, part_number, used)` escolhe o clipe de cada parte entre os objetos publicados sob `[template] background_prefix`.

**Determinístico, não aleatório.** Duas razões: uma parte re-renderizada depois de um restart tem que voltar com a mesma imagem, senão o retry produz silenciosamente um vídeo diferente do que já foi revisado; e a semente inclui o número da parte, então as partes de uma mesma série — publicadas em sequência, onde a repetição seria mais visível — caem em clipes diferentes. A semente é `sha256(run_id:part)`, não `hash()`, que é salgado por processo e mudaria a cada restart.

**Fallback preservado.** Prefixo vazio ou sem objetos cai no `background_video_key` único de antes. Uma biblioteca não preenchida degrada para o comportamento antigo em vez de falhar na última etapa.

#### Era um sorteio, não uma rotação (28/08/2026)

O nome do módulo e a sua própria docstring diziam "rotação", mas a regra era `sha256(run_id) % len(keys)`: um **sorteio com reposição**, sem memória do que já tinha saído. Sorteio uniforme não é rotação — pelo problema do aniversário, 47 tiragens sobre 40 clipes esgotam em média só ~26 deles. Foi o que aconteceu: **20 dos 47 vídeos publicados reusaram um clipe já usado, e a primeira repetição caiu no segundo dia do canal**, quando 39 clipes seguiam intocados.

Isso importa porque o fundo é a maior superfície do quadro. Um canal cujos vídeos abrem no mesmo card, na mesma posição, com a mesma voz sintética e sobre footage que já apareceu é um canal emitindo assinatura de produção em massa — a mesma linha de raciocínio que tirou o fade-in do card. A rotação existia justamente para não emitir essa assinatura, e não estava rotacionando.

**A regra agora é "o menos usado".** `used` mapeia clipe → quantas partes já saíram nele; a escolha vem do subconjunto com a menor contagem, o que garante que a biblioteca inteira passe antes de qualquer clipe voltar. Consequências deliberadas:

- **Empate resolvido por hash, não por ordem da lista.** No começo de um ciclo todos empatam; quebrar pela ordem faria o canal caminhar a biblioteca alfabeticamente, e como os clipes vêm de cortes do mesmo arquivo de origem, posts consecutivos sairiam sobre trechos consecutivos da mesma cena — repetição pior que a que se quer evitar.
- **Clipe novo fura a fila.** Zero usos é a menor contagem possível, então footage adicionada ao bucket sai antes do que já está em rotação, sem esperar o ciclo fechar. É o comportamento desejado: material novo é justamente o que quebra a assinatura.
- **A contagem é acumulada, não uma janela.** O objetivo é "nenhum clipe repete antes de a biblioteca acabar", que é uma propriedade acumulada. Uma janela deslizante permitiria um clipe voltar cedo por ter saído da janela.

**A escolha é persistida, e é ela que dá determinismo agora.** `PipelinePart.background_key` (migration `007`) guarda o clipe. Antes o determinismo vinha do cálculo ser puro; com a contagem entrando na conta, o mesmo cálculo em dois momentos dá respostas diferentes — o que segura o re-render é a coluna. Ela é gravada **antes** do render, não depois: o render leva minutos, e nesse intervalo a escolha já precisa estar contabilizada, ou duas partes em voo escolheriam o mesmo clipe.

Partes anteriores à migration ficam com `background_key` nulo e **não entram na contagem**, de propósito: o primeiro ciclo depois da mudança passa pela biblioteca inteira em vez de herdar um estado enviesado pelos 20 reusos.

#### A biblioteca deixou de caber no disco (29/08/2026)

A fonte de fundo passou a ser uma playlist de **326 vídeos** de gameplay sem direitos autorais — 55 horas, mediana de 9,2 min por vídeo. Cortada em segmentos de 2 min, dá **1.677 clipes**: a três posts por dia, mais de um ano antes de a regra "não repete até esgotar" precisar entrar em ação uma segunda vez.

Baixar tudo custaria ~100 GB medidos (0,39 a 0,73 MB por segundo de vídeo) num servidor com 175 GB livres, para consumir três clipes por dia. Então a biblioteca virou **manifesto + cache**, e não arquivos.

**O manifesto é uma lista, não um diretório.** Um JSON no bucket com `{key, video_id, start}` por segmento. A rotação sorteia sobre a **união** do manifesto com o que já está publicado sob o prefixo — unir, e não escolher uma das duas fontes, é o que deixa os 11 clipes ASMR subidos à mão conviverem com os 1.677 segmentos sem que uma biblioteca esconda a outra. Manifesto ausente ou ilegível degrada para o comportamento anterior: a camada é opcional, não pré-requisito.

**A chave é função de `(video_id, start)`**, não um índice sequencial. Isso é o que faz reconstruir o manifesto ser barato: um manifesto novo continua citando os clipes que já estão materializados, e eles não precisam ser rebaixados. O preço é que mudar `segment_seconds` gera chaves novas e deixa as antigas órfãs no bucket — o despejo só considera o que o manifesto corrente cita.

**Duas medidas é que tornam o download barato:**

- **Só a faixa pedida.** `download_ranges` do yt-dlp busca o trecho, não o arquivo. ⚠️ Isso vale *enquanto* `force_keyframes_at_cuts` ficar desligado: com ele o yt-dlp baixa o vídeo inteiro para cortar com precisão de frame — medido, 188 MB e subindo para um trecho de 2 min. Precisão de frame não significa nada num fundo, e o custo dela é o download inteiro.
- **A fonte tem de ser 4K.** O recorte 9:16 de um 3840×2160 dá 1215×2160 e *desce* para 1080×1920. De um 1080p daria 607×1080 e *subiria* — o mesmo pixel esticado 1,78×. Pedir qualidade alta aqui **reduz** o borrão em vez de aumentar o custo final, porque o que sobe para o bucket é o recorte, não a fonte.

**O enquadramento é corte central, e foi medido.** A alternativa — encaixar o 16:9 no meio da tela vertical com as bordas preenchidas por uma versão borrada do próprio quadro — foi comparada em três frames no dia 29/08/2026 e perdeu: o gameplay fica no terço central e sobram duas faixas mortas. O corte central funciona porque a câmera de terceira pessoa mantém o veículo no centro por construção. De quebra, o céu liso em cima e a rampa embaixo são justamente onde o card e a legenda entram, então o enquadramento ajuda a legibilidade em vez de brigar com ela. O filtro está em `config.ini`, não em código: trocar de opinião não é mudança de software.

**O despejo é LRU, e tem três exceções.** Acima de `cache_max` os clipes menos recentemente usados são apagados — LRU e não FIFO porque a rotação já garante que um clipe usado não volta tão cedo, então o menos recentemente usado é também o que mais demora a ser pedido de novo. Nunca são despejados: clipe de parte que ainda não renderizou (o `blender_worker` vai buscar essa chave, e apagá-la trocaria o freio de espaço por um render quebrado), o recém-materializado (é o clipe do render corrente), e qualquer coisa fora do manifesto (clipe subido à mão não é rebaixável).

**Falhar em baixar é a sétima degradação silenciosa.** O download depende de rede e de site de terceiro, e um soluço ali custaria um run que já pagou LLM, TTS e Whisper. Então a queda é para outro clipe já disponível e, em último caso, para o fundo fixo. O `background_key` é reescrito com o que foi de fato usado — senão um re-render insistiria para sempre na fonte que não baixa. Como em todas as outras, o vídeo sai e o run termina `scheduled`: nenhum status distingue o fundo sorteado do fundo de emergência, e é por isso que existe o `notify`.

⚠️ **O manifesto vive fora de `background_prefix`.** `list_keys` devolve o prefixo inteiro, então um `.json` ali dentro entraria no sorteio e o render tentaria montar um JSON como movie strip. O `is_clip()` filtra por extensão como segunda linha de defesa, para qualquer arquivo solto que apareça ali depois.

#### Caixa de entrada de fundos (`orchestrator/src/orchestrator/background_inbox.py`, 01/09/2026)

Adicionar um clipe à biblioteca era terminal: subir um arquivo à mão sob `background_prefix`, ou rodar `build_background_manifest.py <playlist-url>` de uma máquina com o repo. Isto abre um segundo caminho, no mesmo padrão da caixa de entrada de roteiros do `content_scout` (`docs/vision.md` → "Roteiro viral entra como matéria-prima"): um link de vídeo compartilhado do celular vira entradas novas no manifesto, sem terminal.

**Por que ntfy, de novo.** Os mesmos três motivos da caixa de roteiros: o app já está instalado, o ntfy aparece na aba de compartilhar do Android, e um endpoint HTTP exigiria estar na LAN de casa.

**Por que no `orchestrator`, e não no `content_scout`.** O `content_scout` é dono do padrão de assinatura ntfy, mas não conhece nada de fundo — `plan_segments`, `segment_key` e o manifesto vivem no `orchestrator`, que é quem já materializa clipes sob demanda. Duplicar essa lógica noutro serviço criaria uma segunda fonte de verdade sobre como um segmento é cortado; a alternativa seria o `content_scout` fazer uma chamada HTTP nova ao `orchestrator` só para isso, o que exigiria um endpoint que hoje não existe e não serve a nada mais. Fica mais barato o `orchestrator` assinar seu próprio tópico.

**Nenhum vídeo é baixado neste laço — só a duração.** `yt-dlp` com `download=False` lê metadados (id, duração, título); o download real do trecho continua acontecendo só quando `ensure_available` materializa o clipe sorteado, exatamente como já funciona para o resto da biblioteca. Isso também é o que torna o laço barato o bastante para não precisar de checagem de capacidade: não há LLM, não há Whisper, não há render — só uma consulta de metadado e um upload de JSON.

**Um vídeo por mensagem, não uma playlist.** `noplaylist=True` faz um link de playlist virar só o primeiro vídeo. Suportar playlist pelo celular replicaria a lógica de `extract_flat` de `build_background_manifest.py` para um caso que já tem solução manual e mais barata — o ntfy resolve o caso comum ("achei um vídeo bom, quero usar"), a playlist continua sendo o script.

**Dedup é reentrada no próprio manifesto, sem tabela nova.** Um vídeo já catalogado gera as mesmas chaves (`segment_key` é função de `(video_id, start)`), então mandar o mesmo link duas vezes não duplica nada: se todas as chaves já estão no manifesto, o desfecho é `duplicate` e nada é reenviado ao bucket. Isso evita precisar de uma tabela de "já visto" só para esta porta de entrada.

**Falha de metadado (site fora do ar, vídeo removido, URL não suportada) e vídeo curto demais (`plan_segments` devolve lista vazia) são avisadas e descartadas — sem fila de retry.** Mesma postura da caixa de roteiros: sem fila de espera, quem mandou é avisado e decide se reenvia.

Config em `config.ini [background_inbox]`: `enabled`, `reconnect_delay_seconds`, `connect_timeout`, `failures_before_alert` — mesmas chaves e mesmo motivo do `[inbox]` do `content_scout`. Tópico em `NTFY_BACKGROUND_INBOX_URL` (env), que **tem que ser diferente** de `NOTIFY_WEBHOOK_URL` e de `NTFY_INBOX_URL` — no mesmo tópico do primeiro o serviço leria as próprias notificações de saída como se fossem link.

---

## Cards de comentário (`blender_worker`)

O `POST /images/render` compõe um PNG estilo "comentário do TikTok" com Pillow (não Blender): retângulo arredondado, cabeçalho no topo e texto quebrado automaticamente. O layout inteiro vem de um guide JSON versionado em `blender_worker/templates/`, então ajustar o visual não é mudança de código.

### Canvas fixo, card móvel

O guide (`version: "2.0"`) separa duas coisas que antes eram uma só:

- **`canvas`** — o PNG de saída. Largura **fixa em 1080**, a mesma do frame do TikTok; só a altura varia com o texto.
- **`card`** — a caixa branca, mais estreita, posicionada dentro desse frame por `card.offset`.

O resto do frame fica transparente. Assim a imagem é aplicada sobre o vídeo em largura cheia e o alinhamento é trivial — não há cálculo de posição do lado de quem consome, que era o ponto fraco do contrato da v1 (lá `canvas.width` era o card e o PNG crescia junto com a sombra, obrigando quem posiciona a alinhar pelo centro).

**O card fica à esquerda do centro de propósito.** Com 1080 de canvas e 880 de card, centralizar daria `x = 100`; o template usa `60`. Os 140px de goteira à direita são para a barra de ações do TikTok (curtir, comentar, compartilhar) — um card centralizado passa por baixo dela.

**Os assets empilham acima do texto.** A altura do card é `padding + linha_de_assets + gap + texto + padding`: as duas alturas somam em vez de `max()`, que é o que o layout lado a lado fazia. O `gap` só é cobrado quando o guide declara assets, senão sobra um vão morto acima do texto.

### Sombra projetada

Bloco `background.shadow` no guide: `enabled`, `color` (RGBA), `blur`, `spread` e `offset` (x, y). O padrão do `comment_default.json` é uma sombra preta a 110/255, blur 16, sem spread, caindo 10px para baixo — luz vindo de cima, que é a convenção que o olho lê como "o card está sobre o vídeo" em vez de "o card é parte do vídeo".

**Na vertical o canvas cresce; na horizontal não pode.** Uma sombra borrada e deslocada ocupa espaço *fora* da caixa do card. `shadow_margins()` calcula quanto de padding transparente cada lado precisa; a altura do PNG é `margem_topo + altura_do_card + margem_base`, então o esmaecimento sempre cabe. Mas a largura é fixa em 1080, e ali não há para onde crescer — o espaço tem que vir de `card.offset.x` e da goteira direita.

Daí `check_card_fits()`: um card grudado demais numa borda cortaria o desfoque numa linha reta vertical, o artefato que denuncia uma sombra falsa. Em vez de deixar isso passar, o compose levanta `ValueError` nomeando o lado e o excesso em pixels. É erro de autoria de template, pego uma vez no design — e já pegou um estouro real de 14px durante a escrita deste template.

**A margem é `blur × 3`.** O `radius` do `GaussianBlur` do Pillow é um desvio-padrão, e ~3σ concentra >99% do peso do kernel — além disso a contribuição fica abaixo de um passo de alpha de 8 bits, ou seja, invisível. Margem menor economizaria pixels ao custo de reintroduzir o corte.

**A sombra é clipada pela silhueta do card.** Com o card branco opaco isso não muda nada visível, mas a regra vale para qualquer `background.color` translúcido: sem clip a sombra atravessa e escurece o card de forma desigual, mais forte do lado para onde o offset aponta. O `box-shadow` do CSS clipa da mesma forma, e é nele que o card se espelha.

**Desligada por padrão no schema.** `Shadow.enabled` é `False`, então um guide sem o bloco renderiza a mesma geometria de sempre. Só o `comment_default.json` liga a sombra explicitamente.

### Cabeçalho do card

O topo do card é **um único PNG transparente** com a foto de perfil, o nome e os selos do autor já compostos — não um avatar redondo que o código monta junto de um texto de nome. O guide trata isso como um asset comum na linha do topo, então trocar a identidade do comentário é trocar um arquivo, sem tocar em layout.

O arquivo tem 834×122. Enquanto o cabeçalho media 417×61 lógicos isso era exatamente o que o `compose` pedia com `canvas.supersample: 2` — o asset entrava no tamanho de device pixel, sem reamostragem intermediária, e só era reduzido uma vez junto com o card inteiro.

**O cabeçalho e a fonte foram aumentados** (581×85 e 50px, contra 417×61 e 36px), porque o card é lido em movimento num feed vertical: no tamanho anterior ele ocupava pouco mais de um terço da largura do card e disputava atenção com a legenda, que é três vezes maior. A escala foi escolhida comparando quatro variantes renderizadas lado a lado; acima disso o texto do gancho passa a quebrar em três linhas e o cabeçalho começa a dominar o card.

**O preço é que o cabeçalho passou a ser pedido acima da resolução do arquivo:** 581 lógicos × supersample 2 = 1162px contra os 834 disponíveis, um upscale de 1,39×. Verificado num render 1080×1920: continua legível e sem artefato visível, mas a nitidez de antes só volta regerando `perfil-azul.png` com ~1200px de largura. Não vale reduzir o `supersample` para disfarçar — isso pioraria o card inteiro para consertar um asset.

O `card.gap` subiu de 18 para 26 no mesmo movimento: o espaçamento entre cabeçalho e texto é proporcional ao tamanho dos dois, e mantê-lo fixo colaria o texto no cabeçalho.

**`assets[].size` é um resize duro, sem preservar proporção.** Enquanto o slot era um quadrado de 72×72 isso era inofensivo; com uma faixa larga, um tamanho de aspecto errado achata a imagem e nada falha. `test_shipped_template_asset_keeps_the_source_aspect_ratio` compara o aspecto do spec com o do arquivo versionado em `blender_worker/assets/`.

### Tipografia e antialiasing

O texto é **Arial Bold preta sobre card branco**. A fonte é versionada em `blender_worker/assets/fonts/Arial-Bold.ttf` pelo mesmo motivo da Futura das legendas: a imagem Docker instala apenas `fonts-dejavu-core`, e a família Arial só viria do `ttf-mscorefonts-installer`, que exige aceite de EULA no build. Sem versionar, o card renderizaria em DejaVu no container sem nenhum erro visível.

**O peso é Bold, não Black.** A Black foi o primeiro corte e ficou pesada demais para o texto corrido do card; ela continua versionada como alternativa. A diferença não é só de espessura: a Black também é mais **larga**, então o mesmo texto quebra em mais linhas e a altura do card cresce junto. Trocar o peso é editar `text.font_path` no guide — nenhum código conhece o nome da fonte.

**Nota:** o card deixou de ser só um asset avulso — ele é a abertura de todo vídeo montado pelo pipeline. Ver "Abertura do vídeo (intro)" abaixo.

**O antialiasing já existia antes de haver um knob para ele.** O texto é desenhado pelo FreeType, que antialiasa por conta própria; os cantos arredondados já eram desenhados em 4× e reduzidos. `canvas.supersample` renderiza o card inteiro em N× e reduz uma vez com LANCZOS — é uma passada de uniformidade *em cima* disso, não a origem do efeito, e `supersample: 1` continua sendo uma escolha válida e mais barata. Os testes afirmam que o AA está presente nos dois ajustes, em vez de afirmar que o knob o cria.

---

## Trilha sonora

O `music_key` do `config.ini` apontava para `assets/music.mp3`, um arquivo de 35 segundos de **silêncio digital** — 1.543.500 amostras, nenhuma delas não-nula, pico 0. Não era um arquivo quebrado: era um placeholder que nunca foi trocado, e como o render não tem nada a dizer sobre o conteúdo de um áudio, todo vídeo publicado saiu sem trilha sem que nada falhasse. É o mesmo tipo de defeito silencioso do fundo preto e da legenda em DejaVu: o job reporta `completed` e o MP4 tem duração e tamanho plausíveis.

Trocado por uma faixa real (`assets/music/lofi-goularte.mp3`). Medido no render de validação: a trilha isolada dentro da mixagem dá **-29,7 LUFS** contra **-16,7 LUFS** da mixagem cheia — 13 LU abaixo, que é onde uma cama sonora se ouve sem disputar com a narração (o alvo da narração é -16 LUFS, ver "Normalização de loudness").

O prefixo `assets/music/` já era o formato de biblioteca dos fundos — acrescentar faixas é subir arquivo. O rodízio, quando ligou (01/09/2026), não foi por uso como o do fundo: foi **por mood** (ver abaixo).

**A trilha real expôs dois problemas. O primeiro está resolvido:**

- ~~**O fade começa cedo demais.**~~ **Resolvido.** O fade passou a ser contado a partir do fim (`music.fade_out_seconds`, 1,5s), e `timing.music_fade_out` não é mais lido. Ver "O vídeo não tem finalização" — inclusive a medição de quanto a trilha estava sendo perdida.
- **Só o primeiro minuto da faixa é ouvido.** O strip começa sempre no 0:00 do arquivo, então uma mix de 34 minutos rende sempre o mesmo trecho, e os 32 MB são baixados a cada render. Alternativas: cortar um trecho curto, ou dar um deslocamento determinístico de entrada por vídeo (`frame_offset_start`), no mesmo espírito do rodízio de fundos.

### A trilha combina com o mood da história (01/09/2026)

Até aqui `music_key` era um único valor fixo no `config.ini` — toda história, feliz ou trágica, saía com a mesma cama sonora. Quem decide se o clima combina é quem assiste, então a decisão precisa de um sinal por vídeo, não uma constante do config.

**De onde vem o mood.** O `llm_service` ganhou um campo de topo em `RefineResponse` (mudança de interface entre serviços): `mood`, valores `"sad" | "tense" | "hopeful" | "neutral"`. É campo de topo, fora de `classification`, pelo mesmo motivo do `narrator_gender`: mecanismo consumido diretamente pelo orchestrador, não uma leitura de audiência. `normalize_mood` — mesmo padrão de `normalize_narrator_gender` — aceita qualquer entrada e cai em `"neutral"` para o que não bate com o contrato; um modelo antigo que não devolve o campo também cai em `"neutral"`, que é a trilha que todo vídeo já usava.

**Não é `classification.tone`.** O `tone` (`suspenseful`/`funny`/`emotional`/`educational`/`inspirational`/`shocking`) informa hashtag e edição, e mistura registros que não mapeiam para música — `emotional` tanto cobre luto quanto reconciliação, e as duas pedem trilhas opostas. `mood` responde só "que cama sonora combina com isto", numa escala pensada para música, não para conteúdo.

**Onde a escolha acontece.** `orchestrator/src/orchestrator/music.py` — `pick_music(keys, run_id)`, determinístico por `sha256(run_id)` (mesmo motivo do `pick_background`: um run re-renderizado depois de restart tem que voltar com a mesma trilha). Sem contagem de uso: ao contrário do fundo, a biblioteca por mood tende a ter poucas faixas, e a mesma música repetindo em vídeos consecutivos não é o defeito visível que o mesmo clipe de fundo é — não vale o custo de uma tabela de uso para isso.

`worker._pick_music` roda uma vez por run, entre o card da intro e o processamento das partes — mesmo lugar do `card_key`, porque o mood é do run, não da parte, e todas as partes de uma série têm que sair com a mesma trilha. Lista `{prefix}{mood}/` (`config.ini [music] prefix`) e cai em `[music] default_key` (a faixa neutra de sempre) quando a pasta está vazia. `PipelineRun.mood` e `PipelineRun.music_key` (migration `008`) guardam o resultado; `_run_render` manda `run.music_key` no lugar do `settings.CONFIG.template.music_key` fixo.

**Pasta de mood vazia não é degradação — é o estado inicial da biblioteca.** Hoje só existe a faixa neutra (`assets/music/lofi-goularte.mp3`); `sad`, `tense` e `hopeful` ainda não têm arquivo. Por isso `_pick_music` não avisa quando a pasta de um mood está vazia: é o esperado até alguém subir faixas lá, e um aviso que dispara em todo run até isso acontecer é um alarme que ninguém lê. A **oitava** degradação silenciosa de verdade é outra: a listagem do bucket falhar (bucket fora do ar) — aí sim cai no `default_key` com `log.warning` e `notify()`, porque isso é sintoma de infraestrutura quebrada, não de biblioteca incompleta.

**Aditivo de propósito.** Nada no bucket foi movido — `lofi-goularte.mp3` continua fora de qualquer subpasta de mood, referenciado só por `default_key`. Preencher a biblioteca é subir `assets/music/sad/<arquivo>.mp3` (e os outros moods) sem tocar em código nem reorganizar o que já está publicado; o R2 é compartilhado entre ambientes, e mover o que já está lá afetaria todos de uma vez sem necessidade.

---

## Escolher o template por vídeo, não só pela conta inteira (06/09/2026)

Até aqui `BLENDER_TEMPLATE_ID` era uma env var única, lida em dois lugares (`_narration_rate` e `_run_render`) — todo vídeo da conta usava exatamente o mesmo template, e trocar de template significava trocar para todo mundo de uma vez. Isso bloqueava dois casos legítimos: reverter só um vídeo problemático sem afetar os outros, e rodar um formato novo em paralelo ao antigo (ex.: 1 vídeo no formato experimental, 2 no formato atual, no mesmo dia) para comparar antes de decidir migrar de vez. O rollback por template já existia como prática (registrar um `.blend`/`template.json` novo sob um `BLENDER_TEMPLATE_ID` novo, nunca sobrescrever o publicado — ver "O `template.json` do repo não é o que roda" no `CLAUDE.md` da raiz); o que faltava era um jeito de ter mais de um *em uso* ao mesmo tempo.

**Mudança de interface:** `POST /pipeline` (`PipelineCreate`) ganha `template_id: uuid.UUID | None = None`. Omitido, o run se comporta exatamente como antes — o campo é puramente aditivo.

**`PipelineRun.template_id`** (migration `009`, nullable, exposto em `PipelineResponse`) — a sobrescrita persistida para aquele run. Guardado na criação do run, antes até do refino, porque `_narration_rate` precisa dele no primeiro TTS.

**Resolvido num único lugar.** `_template_id_for(run) -> uuid.UUID` = `run.template_id or uuid.UUID(settings.env.blender_template_id)`, chamado tanto por `_narration_rate` quanto por `_run_render`. Os dois **têm que concordar**: `_narration_rate` lê `narration.rate` do template antes do primeiro TTS, e se o TTS saísse na velocidade de um template enquanto o render usa outro, a narração sairia rápida ou lenta demais para o corte que o vídeo realmente leva. Antes de `template_id` existir, os dois liam a mesma env var e não podiam divergir por construção; agora que o template pode variar por run, `_narration_rate` passou a receber `run` em vez de nenhum argumento.

**Quem escolhe hoje é só o disparo manual.** O `content_scout` (disparo automático) nunca manda `template_id` — todo vídeo automático continua no template default até existir um critério para escolher entre formatos sem intervenção humana, decisão deliberadamente não tomada agora (ver "Decisões em Aberto").

**Não precisa de nada novo no `blender_worker`.** Ele já registra N templates via `POST /templates` desde sempre — o que faltava era o orchestrador nunca ter usado mais de um por vez.

⚠️ Requer a migration `009` aplicada — sem a coluna, `create_pipeline` falha com `UndefinedColumn` na primeira leitura de `run.template_id`. Mesmo padrão das migrations anteriores: o `CMD` do Dockerfile roda `alembic upgrade head` no boot.

- Testes: `tests/test_template_selection.py` — campo aceito e omitido no schema, fallback pro default quando `None`, o mesmo `template_id` chegando ao `POST /jobs` e ao `GET /templates/{id}/config` (`_narration_rate`), e exposição em `PipelineResponse`.

---

## Contas de publicação — Fase 1 do multi-account (06/09/2026)

Implementa a Fase 1 de `docs/multi_account.md`: a conta vira dado, não mais env var
hardcoded. **Sem** split de `stories`/`renders`/`publications` (Fase 2) e **sem** o scout
escolher conta sozinho — decisão deliberada deste ciclo, revisitável quando fizer sentido
operar mais de uma conta em produção simultaneamente (ver "Decisões em Aberto").

**Onde cada coisa mora, e por quê.** O orchestrador nunca fala com o Buffer — sempre foi
assim, mesmo com uma conta só — então ele não é o lugar certo para guardar token. A tabela
`accounts` (orchestrador) guarda só identidade e overrides de produção (`slug`, `status`,
`template_id`); a tabela `account_credentials` (banco novo, próprio do `tiktok_poster`)
guarda o que é sensível: token do Buffer cifrado com Fernet, org id, canais de TikTok e
YouTube. As duas são ligadas pelo mesmo `id` (UUID), sem FK entre bancos — são serviços
diferentes, e a integridade é responsabilidade da aplicação, mesma escolha pragmática já
usada em outras colunas do projeto.

**Mudança de interface:** `POST /pipeline` (`PipelineCreate`) ganha `account_id: uuid.UUID |
None = None`, exposto em `PipelineResponse`. Omitido, o run se comporta exatamente como
antes — publica na conta default (as env vars de sempre do `tiktok_poster`). O orchestrador
repassa `account_id` (como string) ao `tiktok_poster` via `TikTokClient.schedule(...)`, no
mesmo payload de `POST /schedule`, omitido quando `None` — mesmo padrão de omissão já usado
em `youtube_title`/`follows_at`.

**Resolução de credenciais é só do `tiktok_poster`.** `_resolve_account` (`api/routes/
schedule.py`) busca `account_credentials` por `account_id`; achando, decifra o token e monta
um `BufferClient(access_token=..., org_id=..., channel_id=...)` — para o TikTok e, se a conta
tiver canal, para o YouTube também. **`account_id` desconhecido ou inválido nunca derruba a
publicação**: cai na conta default com um aviso (`log.warning`), porque o vídeo já está
renderizado e perdê-lo por um erro de operador custaria mais caro que publicar no lugar
"errado" (a conta de sempre). `BufferClient.__init__` ganhou `access_token`/`org_id`
opcionais para isso — omitidos, o comportamento é idêntico ao de antes desta mudança.

**`GET /health` é a exceção à regra de nunca falhar.** Diferente do `/schedule`, aqui não há
vídeo em jogo — é um diagnóstico. `account_id` pedido e não encontrado devolve `404`, não a
saúde silenciosa da conta default: misturar as duas enganaria justamente quem está
depurando aquela conta.

**Cadastro de credenciais é um endpoint, não configuração.** `POST /accounts` no
`tiktok_poster` (upsert por `account_id`) cifra o token na hora e nunca o devolve em nenhuma
resposta, nem cifrado — `GET /accounts` só expõe `slug`, canais e datas. Existe porque
centenas — ou mesmo duas — contas não cabem em `.env`, e trocar uma credencial não pode
exigir reiniciar o serviço.

⚠️ **Requer a migration `010` do orchestrador e a `001` do `tiktok_poster`** (banco novo
deste serviço) **aplicadas.** Mesmo padrão de sempre: o `CMD` dos dois Dockerfiles roda
`alembic upgrade head` no boot — o `tiktok_poster` não tinha banco antes desta mudança, e
passou a rodar migration no boot pela primeira vez.

- Testes: `orchestrator/tests/test_accounts.py` (CRUD de `/accounts`, `account_id`
  persistindo e chegando ao payload do poster, omissão preservando o comportamento antigo) e
  `tiktok_poster/tests/test_account_credentials.py` (cifra/decifra, upsert, o token nunca
  aparecendo em nenhuma resposta, `/schedule` usando a conta certa, conta desconhecida caindo
  no default, `/health` por conta).

---

## Rampa de publicação — aquecimento de conta (06/09/2026)

Implementa a Parte 1 (software) de `docs/aquecimento.md`: canal novo ou parado publica menos
no início, em degraus crescentes, em vez do ritmo cheio desde o primeiro dia — o padrão que
queima conta. A Parte 2 (rotina manual: perfil completo, uso real do app, sem automação de
engajamento — que continua fora de escopo por violar ToS e ser o próprio sinal que derruba
conta) é trabalho humano e não pede código; está descrita no doc.

**Regra de negócio: a rampa é do canal, não da conta.** TikTok e YouTube da mesma conta
podem estar em fases diferentes — um canal parado religando não pode herdar o ritmo cheio
do outro canal da mesma conta que nunca parou.

**Onde cada data mora.** Conta extra (Fase 1 do multi-account): `account_credentials`
ganha `tiktok_warmup_started_on`/`youtube_warmup_started_on` (migration `003` do
`tiktok_poster`), cadastradas pelo mesmo `POST /accounts`. Conta default (env vars, sem
`account_id`): `config.ini [warmup]` — ela nunca ganhou linha em `account_credentials` na
Fase 1, e este trabalho não muda isso. Os degraus (`steps`, ex. `"1x7,2x7,3"`) são
compartilhados entre contas e canais — é política de produto, não dado por conta.

**Decisões dos itens que `docs/aquecimento.md` deixava em aberto:**

| Decisão | Escolha |
|---|---|
| Unidade do teto | **história**, não post — uma continuação de série nunca é limitada pela rampa, mesma exceção que `continuation_slot` já faz para `preferred_times`/`posts_per_day` |
| Horário sob teto reduzido | **roda por dia** (rotação por `check_day.toordinal()`), não fixa sempre no primeiro horário — a mesma classe de assinatura de conta automatizada que a rotação de hashtags já corrigiu em 28/08 |
| Degraus | `1x7,2x7,3` — o palpite do doc, como default configurável, sem medição por trás |
| Divergência de ritmo entre TikTok e YouTube da mesma conta | **é o caso normal agora**, não exceção — o YouTube tem teto próprio, contado contra a fila própria dele |
| Aviso de canal trocado sem data de rampa | **implementado só para contas extras** (o upsert de `POST /accounts` já tem o valor antigo e o novo à mão); para a conta default exigiria persistir "qual canal era antes" em estado novo, e fica adiada |

**Implementação em `tiktok_poster/buffer/scheduler.py`** — detalhes técnicos em
`tiktok_poster/CLAUDE.md` → "Rampa de publicação". Resumo: `next_available_slot` passou a
aceitar `posts_per_day` como `int` **ou** `Callable[[date], int]`, retrocompatível com todo
chamador existente; o YouTube conta seu próprio teto do dia via o `BufferClient` que
`_schedule_youtube` já constrói, e pula (não falha) quando estourado.

⚠️ Requer a migration `003` do `tiktok_poster` aplicada.

- Testes: `tiktok_poster/tests/test_scheduler.py` (funções puras da rampa e o `Callable` em
  `next_available_slot`, com a retrocompatibilidade do `int` coberta) e
  `tiktok_poster/tests/test_account_credentials.py` (datas por conta, upsert não apagando
  campo omitido, `null` explícito apagando de propósito, e o YouTube pulando — ou não, numa
  continuação — pelo teto do próprio canal).

---

## Abertura do vídeo (intro: card + gancho)

O vídeo abre com o **card de comentário** trazendo a frase gancho, e com essa frase **narrada por cima dele**. A narração da parte começa quando o gancho termina. As duas peças já existiam separadas — o card (`POST /images/render`) e o `hook.mp3` (`_run_hook_tts`) — e nenhuma chegava ao vídeo: a intro era um bloco de 3 segundos de fundo rodando sozinho.

### Quem monta o quê

```
orchestrator
  ├─ _run_hook_tts   → audio/{run_id}/hook.mp3      (rate = narration.rate)
  ├─ _render_card    → cards/{run_id}.png            (POST blender_worker/images/render)
  └─ _run_render     → POST /videos {card_key, hook_voice_key, ...}
                          └→ blender_worker: ch6 imagem + ch5 som, antes da narração
```

`PipelineRun` ganhou `card_key` (migration `004`); `videos` ganhou `card_key` e `hook_voice_key` (migration `d4e5f6a7b8c9`). Ambas as colunas são nullable, e é essa nulidade que carrega o significado de "não há intro".

### A intro dura o gancho, não um número fixo

`narration_start_frame(default_start, hook_end, tail_frames)` decide onde a narração começa: `max(speech_start_do_template, fim_do_gancho + tail)`.

- **`max` e não soma** — um gancho curto não *encurta* a abertura: o `speech_start` do template (90 frames) continua sendo o piso, então um run sem gancho e um run com gancho de 1 segundo têm a mesma moldura.
- **O `tail` (0.3s por padrão)** separa a última palavra do gancho da primeira da narração. Sem ele as duas falas colam e viram uma frase só, o que denuncia a emenda.
- O card cobre exatamente `[intro_start, narration_start)` — a abertura inteira, sempre.

Medido no render de validação: gancho de 2,60s, `tail` de 0,3s → narração em 3,03s (o piso do template venceu), card de 0 a 3,00s. O mixdown mostra voz a ~-22 dBFS de 0 a 2,5s, -37 dBFS no intervalo (só a trilha a 0.2) e ~-19 dBFS a partir de 3,0s.

### O gancho vai no rate da narração

`_run_hook_tts` passou a receber o `narration.rate` do template, o que obrigou `_narration_rate()` a ser lido **antes** do primeiro TTS (era lido dentro de `_process_all_parts`). Enquanto o gancho era um artefato à parte, narrá-lo no `TTS_RATE` default não aparecia em lugar nenhum; montado na frente da narração, um rate diferente lê como uma segunda voz.

Vale igual para o **`narrator_gender`**, que o gancho recebe do run pelo mesmo motivo — ali a segunda voz não seria uma impressão, seria literal. Ver "Voz da Narração".

### Canais novos, com default no código

`channels.hook` (5) e `channels.card` (6) entram no `template.json`, mas `edit_video.py` traz os dois como default. O `template.json` **vive no bucket**: um template publicado antes desta feature não tem os campos, e sem default o card cairia no canal do vídeo de fundo — encobrindo o vídeo inteiro em vez de aparecer sobre ele. O card fica acima da legenda porque os dois nunca coexistem: as legendas começam com a narração, e aí o card já saiu.

### O card é um strip de imagem com alpha

`fit_method="ORIGINAL"` e `blend_type="ALPHA_OVER"`, ambos explícitos. O PNG é composto na largura exata do frame (1080) com margem transparente, então qualquer *fit* só reamostraria a imagem. E o `blend_type` de um strip criado pela API **não** é o `ALPHA_OVER` que a UI dá: sem setar, a moldura transparente do card renderiza como uma caixa preta sobre o vídeo.

`card.y_position` usa a mesma escala do `y_position` da legenda (fração da altura do frame, 0 = base), aplicada como `transform.offset_y` em pixels a partir do centro; clampada a 0..1 porque um valor fora do frame vira card sumido sem erro. O fade é limitado a ⅓ do strip pelo mesmo motivo que o da legenda — um fade maior que o strip nunca chegaria a opacidade cheia.

### O card entra sem fade (`fade_frames` só vale para a saída)

O card está em opacidade cheia **no primeiro frame** do vídeo. Só a saída tem fade, e é ela que `card.fade_frames` mede agora.

**A razão não é estética, é de distribuição.** Todo vídeo do canal abre com um card de comentário na mesma posição, e a rampa de `blend_alpha` por cima dela era idêntica em todos: os primeiros quatro frames de qualquer vídeo eram quase o mesmo par de imagens, variando só no texto ainda semitransparente. Isso é assinatura no nível do frame, e é o que um detector de conteúdo duplicado procura. Com o card já posto, o que abre cada vídeo é o card daquela história e mais nada.

O efeito colateral é bem-vindo: o gancho fica legível quatro frames antes, nos segundos em que a pessoa decide se continua assistindo.

**Compatibilidade.** `fade_frames` não mudou de nome nem de unidade — um template publicado no bucket continua sendo lido, e o número que ele traz passa a valer só para o fim da abertura. `fade_frames: 0` continua desligando o fade inteiro, e o teto de ⅓ do strip continua valendo.

### O gancho é dito uma vez só (`hook_muted`)

O gancho é **literalmente** a primeira frase da parte 1, então montar o `hook.mp3` na frente dela faria a abertura e a narração dizerem a mesma coisa em sequência. `_hook_is_muted(part, run)` marca essa parte, e o render entra num modo diferente:

| | gancho tocado (partes 2+) | gancho mudo (parte que abre com ele) |
|---|---|---|
| quem narra a frase | `hook.mp3` | a narração da própria parte |
| início da narração | depois do gancho + `tail` | junto com o vídeo |
| card sai em | `max(speech_start, gancho + tail)` | fim do gancho, sem `tail` e sem piso |
| legenda | começa com a narração | escondida enquanto o card está na tela |

**O arquivo continua sendo baixado e montado, mudo.** É ele que diz quanto tempo a frase leva para ser falada — mesma voz, mesmo `rate`, mesmo texto. Medido num render real: a narração termina o gancho em 2,560s e o `hook.mp3` dura 2,600s, 40 ms de diferença. Não é removido do timeline (`strip.mute = True`, não `remove()`) para que o `.blend` montado continue mostrando de onde sai a duração do card.

**A condição é o texto da parte, não o número dela** (`opens_with_hook`, puro): se um dia o refino devolver o gancho abrindo a parte 2, ela se comporta como a parte 1 sozinha. A comparação ignora espaço em branco, caixa e forma de acentuação (NFC/NFD normalizados na string inteira — em NFD o til de `manhã` é um caractere separado, e comparar caractere a caractere nem teria o mesmo número de posições dos dois lados). Um gancho que o modelo reescreveu dá `False`, e o vídeo volta a abrir com o áudio próprio: a frase é dita uma vez de um jeito ou de outro.

**A legenda some enquanto o card está na tela** (`drop_specs_before`). No modo mudo a narração roda *sob* o card, e o card e a legenda ocupam a mesma altura do frame — sem isso a mesma frase apareceria escrita duas vezes, empilhada. As palavras que sobram não são deslocadas: o áudio não se moveu.

**Sem `tail` no modo mudo.** O `tail` separa dois áudios diferentes, e aqui há um só. Cobrá-lo custa a legenda da primeira palavra da história: medido, o gancho falado acaba em 2,560s e a palavra seguinte começa em 2,759s — dentro dos 0,3s de `tail`, que segurariam o card por cima dela. Pelo mesmo motivo não há piso do template: o card acompanha uma frase que já está sendo dita, e segurá-lo além disso cobriria a segunda frase da história.

### Degradação

Card e gancho são **independentes e degradáveis**: cada etapa vira `warning` e deixa a key nula. Um vídeo sem gancho e sem card renderiza exatamente como antes desta feature, o que é o comportamento que se quer de uma camada visual num pipeline cujo produto é o vídeo. A assimetria com o TTS das partes (que derruba o run) é a mesma de sempre: sem narração não há vídeo, sem abertura há.

**A intro abre todas as partes**, não só a primeira — é ela que dá a mesma cara à série inteira quando as partes caem no feed em dias diferentes.

---

## Agendamento (tiktok_poster)

- Ritmo: 3 posts por dia, em `preferred_times`.
- Séries: partes encadeadas com `series_gap_minutes` a partir da parte 1 — ver "Agendamento de séries".
- O `tiktok_poster` é responsável por:
  - Escolher hashtags finais (com base na classificação; hoje sem retroalimentação de
    performance — ver "Análise de variantes e teste A/B" abaixo para o que existe disso)
  - Postar no horário agendado via Buffer (TikTok e, se ligado, YouTube)
  - Guardar, por publicação, qual hashtag, horário, template e voz foram usados
  - Puxar métricas (views, likes, shares, ...) do Buffer um dia depois de cada post e
    expor uma comparação descritiva entre variantes — ver "Análise de variantes e teste A/B"
- O orchestrador delega completamente — só recebe confirmação de `scheduled` e `posted`.

### Janela de publicação: 11h–20h de Brasília

`preferred_times` é gravado em **UTC**, porque é o que o Buffer recebe e o que `datetime.combine(..., tzinfo=timezone.utc)` produz em `next_available_slot`. Mas a decisão que esse campo carrega é editorial e está no fuso do público: os vídeos são em português e o público é brasileiro, então a janela é **11h–20h em Brasília** (UTC-3, sem horário de verão desde 2019).

| `preferred_times` (UTC) | BRT |
|---|---|
| `14:00` | 11:00 |
| `18:00` | 15:00 |
| `22:00` | 19:00 |

**O fuso é a armadilha.** Os horários anteriores (`00:00,12:00,20:00` UTC) eram 21:00, 09:00 e 17:00 em Brasília — dois deles fora de qualquer janela pretendida, um deles quase na virada do dia. Ninguém notou porque **não existe erro**: o run passa por todas as etapas, o Buffer aceita o post e o vídeo publica normalmente. A única evidência é o horário em que ele aparece.

**Por que o último slot é 19:00 e não 20:00.** Uma continuação não disputa `preferred_times` (ver "Agendamento de séries"): ela pendura `series_gap_minutes` depois da parte anterior. Um último slot em 20:00 BRT jogaria a parte 2 para 20:30, fora da janela. A folga de uma hora no fim é o que estende a garantia da janela da parte 1 para a história inteira — cabem duas continuações antes de encostar nas 20h, e desde 31/08/2026 não há divisão nenhuma, então na prática o slot das 19:00 é o último de um dia de três posts independentes.

Deliberadamente **não há guarda de janela dentro de `continuation_slot`**. As duas saídas possíveis seriam piores que o problema: manter o horário fora da janela não guarda nada, e empurrar para a janela do dia seguinte parte a história ao meio — exatamente o comportamento que o encadeamento foi criado para eliminar. A janela é garantida onde ela é escolhida, que é a configuração.

**A garantia é um teste, não um comentário.** `tiktok_poster/tests/test_posting_window.py` lê o `config.ini` publicado e falha se algum slot cair fora de 11h–20h BRT, se houver menos horários que `posts_per_day` (cota diária inalcançável), se dois horários forem iguais, ou se o último slot mais `series_gap_minutes` passar das 20h. É o único jeito de um erro sem sintoma virar um erro visível.

---

## Análise de variantes e teste A/B (06/09/2026)

Até aqui o `tiktok_poster` era stateless: agendava no Buffer e esquecia. Não existia registro
de qual hashtag, horário, template de edição ou voz de narração cada vídeo usou, e nenhuma
coleta de métricas de performance. Pedido: comparar o desempenho dessas variantes dentro da
conta atual — não envolve múltiplas contas, que é a Fase 1 do multi-account acima e continua
sendo uma dimensão separada (comparar entre contas fica para quando houver mais de uma em
produção real).

**Fonte de métricas: a API do Buffer, não a do TikTok/YouTube direto.** O Buffer expõe
`post(input:{id}).metrics` e `aggregatedPostMetrics` via GraphQL — tipos `views, likes,
shares, comments, reach, impressions, saves, totalTimeWatched, reactions, reposts, follows,
quotes, viewers`. Três restrições documentadas pelo próprio Buffer moldam o design:

- **Só para uso pessoal, com a própria API key.** Serve porque o token já é nosso (mesma
  conta que agenda), mas não escalaria para consultar métricas de contas de terceiros.
- **~24h de atraso.** Uma publicação só tem métrica depois de um dia; o sync não tenta puxar
  nada mais novo que isso, e a ausência de métrica não é tratada como zero.
- **Métrica ausente ≠ zero.** Se o Buffer ainda não calculou uma métrica para um post, ela
  simplesmente não aparece — a agregação exclui a publicação daquele grupo, em vez de contar
  como 0 e distorcer a média para baixo.

**Por que atravessa três serviços.** A voz e o template usados numa parte só são conhecidos
rio acima — o `tts_service` resolve a voz a partir do gênero do narrador, e o orchestrador
resolve o template a partir do override do run ou do default. Nenhum dos dois chegava ao
`tiktok_poster`, que é quem sabe qual post foi de fato publicado onde.

- `tts_service`: `POST /generate` passou a devolver `voice` (a voz resolvida) na resposta —
  campo novo, sem mudar nenhum comportamento existente. Ver `tts_service/CLAUDE.md`.
- `orchestrator`: guarda `PipelineRun.tts_voice` (a partir da primeira resposta do TTS do run)
  e manda `template_id`/`tts_voice` no `POST /schedule` do poster — os dois lidos com `.get()`
  em vez de acesso direto, mesmo contrato defensivo de `youtube_title`/`narrator_gender`: um
  serviço mais velho do outro lado não pode derrubar o run por um campo cosmético. Ver
  `orchestrator/CLAUDE.md` → "Voz resolvida (`tts_voice`)".
- `tiktok_poster`: `ScheduleRequest` ganha `template_id`/`tts_voice` opcionais, e uma
  publicação bem-sucedida grava uma linha própria com essas duas variantes mais a hashtag e o
  horário — ver `tiktok_poster/CLAUDE.md` para o schema exato (`publications`/`post_metrics`).

**Comparação é descritiva, não teste de significância.** `GET /analytics/variants` agrupa por
variante e devolve tamanho da amostra, média, min, max e soma da métrica escolhida — sem
p-value nem intervalo de confiança. Com o volume atual (3 posts/dia na conta), qualquer
significância calculada daria uma falsa sensação de rigor; uma média simples por grupo já é
suficiente para enxergar tendência, e é o que o `docs/product.md` também descreve.

**Sincronização é puxada por cron externo, não por loop interno.** `POST /metrics/sync`
existe para ser chamado 1x/dia (o Buffer só atualiza métricas nesse ritmo) — por um cron no
servidor, mesmo padrão do cron de disco já pendente em `docs/deploy.md`, não por um `asyncio`
loop dentro do processo. O serviço continua sem estado de longa duração além do banco.

## Publicação no YouTube

O mesmo MP4 vai para dois destinos, pelo mesmo Buffer, no mesmo slot. O serviço continua se chamando `tiktok_poster` — o nome é histórico, e renomear tocaria o compose, o repo individual, as URLs de serviço e o deploy da máquina de produção sem mudar comportamento nenhum.

### Por que pelo Buffer e não pela API do YouTube

A `videos.insert` da YouTube Data API v3 é o caminho direto e não tem intermediário. Mas todo vídeo enviado por um projeto de API **não auditado** fica travado como privado, sem apelação — a única saída documentada é reenviar por um cliente auditado ou submeter o projeto à auditoria do Google. A política vale desde julho de 2020 e continua na documentação atual. Some-se a cota: `videos.insert` custa 1600 unidades das 10.000 diárias, o que dá ~6 uploads por dia.

O Buffer é um cliente auditado, já estava no sistema com token, fila e backpressure funcionando, e o canal do YouTube entra na mesma organização. O custo de somar o destino virou um ID de canal e um bloco de metadata. A API direta continua sendo o caminho se um dia a auditoria for feita — ou se o Buffer se mostrar ruim com vídeo longo.

### O contrato do Buffer, verificado por introspecção

O que o YouTube exige e o TikTok não é o par `title` + `categoryId`, os dois **obrigatórios na criação**: sem eles o `createPost` é recusado inteiro. O bloco vai em `metadata.youtube` (`YoutubePostMetadataInput`), com `privacy` (`private`/`public`/`unlisted`), `madeForKids`, `notifySubscribers`, `embeddable`, `license` e `isAiGenerated`.

O formato não foi deduzido da documentação e sim lido do schema real por introspecção GraphQL. Era a peça load-bearing da feature: errar a forma da mutation faria nada funcionar, e nenhum teste com mock pegaria isso.

**A cláusula `metadata` é montada só quando há metadata**, em vez de mandar `metadata: null` sempre. O caminho do TikTok publica em produção hoje sem esse argumento; servidor GraphQL não é obrigado a tratar `null` explícito como ausente, e o destino que já funciona não pode mudar de forma para acomodar o novo.

### O título é um campo novo, não o gancho reciclado

O TikTok não tem título — tem legenda, que é lida por baixo de um vídeo que já está tocando. No YouTube o título é a única coisa lida **antes** de o vídeo abrir, numa lista ao lado de dezenas de outros. São textos com trabalhos distintos e momentos distintos de leitura, e reaproveitar um como o outro desperdiça exatamente o momento em que o clique se decide — o mesmo raciocínio que fez o gancho virar campo próprio em vez de continuar sendo "a primeira frase".

Então `youtube_title` é campo de topo do `RefineResponse`, com prompt próprio: até 100 chars (teto da API, não escolha editorial), o conflito sem o desfecho, sem clickbait falso, sem caixa alta, sem emoji e sem rótulo de parte.

**O rótulo `(Parte n/N)` é acrescentado pelo poster**, que é quem sabe `total_parts` — mesma razão pela qual o rótulo da caption saiu de `classification["parts"]` e passou a vir do request. E **o corte protege o rótulo, não o título**: numa série, saber qual parte é aquela é a informação que não pode faltar, então é o título que encolhe para caber.

**O título é do run, não da parte.** Uma história dividida é a mesma história; o que distingue as partes é o rótulo.

### As hashtags do YouTube são outras

`#tiktokbrasil` e `#fyp` eram inúteis lá: hashtag no YouTube é busca, não distribuição. Daí `[hashtags] youtube_mandatory` existir separado de `mandatory`. (As duas do TikTok saíram de cena em 28/08/2026 — ver abaixo —, mas a separação continua valendo: são eixos diferentes, não a mesma lista.)

**`#shorts` ficou de fora quando o formato era longo** — até 30 minutos de fala, contra os 3 do teto de Short —, e marcar como Short um vídeo que não é engana quem clica sem mudar a distribuição. ⚠️ **Essa razão caducou em 31/08/2026**: com todo vídeo em 10–40s, todos são Shorts de fato. A tag entra por `[hashtags] youtube_mandatory` no `config.ini`, sem código — está pendente de decisão, não de implementação.

### A cauda da legenda não pode ser a mesma post após post (28/08/2026)

`select_hashtags` montava a lista como obrigatórias → hints → pool. As obrigatórias eram `#tiktokbrasil,#fyp` em **todo** post, e o pool preenchia as vagas restantes sempre a partir do topo da lista — na prática, `#viral #foryou #foryoupage`. O resultado é que 47 vídeos saíram com a mesma cauda de legenda, nas mesmas posições. Isso é o análogo textual do fundo repetido e do fade-in idêntico: um traço estável, barato de medir, que separa conta operada por pessoa de conta operada por script.

Duas mudanças, e elas atacam pontas diferentes do mesmo problema:

- **`[hashtags] mandatory` vazio.** O mecanismo continua no código e na config — preencher a linha volta a fixar tags. O que mudou é a escolha de não fixar nenhuma no TikTok, onde hashtag genérica de alcance (`#fyp`) não compra distribuição e só acrescenta constante à legenda. O `youtube_mandatory` **fica**: lá a hashtag é termo de busca, e uma constante é exatamente o que se quer.
- **Pool reordenado por post.** `seed` reordena o pool por `sha256(seed:tag)`. Sem seed o comportamento antigo é preservado, o que mantém a função utilizável fora da rota.

**Embaralhado, não sorteado.** `random.shuffle` faria a legenda mudar entre a montagem e um reagendamento, e um retry publicaria texto diferente do que foi revisado — o mesmo requisito que torna a rotação de fundo determinística, resolvido do mesmo jeito. A seed é `{destino}:{series_id}:{part_number}`.

**Só o pool é embaralhado.** `mandatory` é escolha explícita de quem configurou e `hints` descrevem a história — reordenar qualquer um dos dois trocaria variedade por ruído, porque a ordem ali carrega intenção. O pool é o único trecho onde a ordem nunca significou nada, e é por isso que é ele que varia.

**A seed leva o destino no prefixo.** Sem isso, o mesmo vídeo no TikTok e no YouTube receberia a mesma reordenação do pool e as duas legendas terminariam iguais — reintroduzindo a constante em outro eixo.

### A falha no YouTube é degradável

O post do TikTok **já foi criado** quando o YouTube é tentado. Derrubar o request nesse ponto faria o orchestrador tratar como falha um run cujo destino principal saiu — e o retry republicaria a parte no TikTok, que apareceria duas vezes lá. Então `_schedule_youtube` nunca levanta: todo desfecho ruim vira string em `youtube_error`, e o run termina `scheduled`.

É a sexta degradação silenciosa do sistema, e a razão de existir é a mesma das outras cinco: o status não distingue vídeo publicado nos dois lugares de vídeo publicado em um só, então sem o aviso no WhatsApp não há aviso em lugar nenhum. `PipelinePart.youtube_video_id` torna a ausência auditável depois que o aviso passou.

**`youtube_enabled` separa "desligado" de "falhou"**, e só o segundo vira aviso. Sem essa distinção, toda parte de todo run dispararia um warning enquanto o canal não estivesse conectado — e um alarme que toca sempre é um alarme que ninguém lê. Destino desligado é configuração, não degradação.

### O destino é opcional por construção

`BUFFER_YOUTUBE_CHANNEL_ID` vazio desliga o YouTube e o serviço segue publicando só no TikTok. Um destino secundário não pode impedir o serviço de subir, e essa é também a razão de o campo ser opcional no request (`youtube_title`) e na resposta: deploy dos serviços não é atômico, e cada lado tem de sobreviver ao outro estando velho.

---

## Trigger (entrada)

Três portas de entrada, todas terminando no mesmo `POST /pipeline`:

1. **Manual** — `POST /pipeline` no orchestrador com `{ "script": "...", "metadata": {} }`.
2. **Automática** — o `content_scout` descobre roteiros sozinho e chama o mesmo endpoint.
3. **Caixa de entrada** — um link de vídeo compartilhado pelo celular, que o `content_scout`
   transcreve e manda para o mesmo endpoint (ver "A caixa de entrada de links", abaixo).

O trigger sempre normaliza para `plain text + metadata` antes de enviar ao orchestrador, independente da origem.

---

### Roteiro viral entra como matéria-prima, nunca como roteiro final

Reaproveitar roteiro de vídeo que já viralizou é tentador pelo motivo certo: o upvote do
Reddit mede quantas pessoas votaram, o view count mede que a história **reteve**, que é o
sinal que falta em toda a seleção atual. O erro é confundir o sinal com o texto.

**Texto recitado palavra por palavra é o modo de falha.** Não pela detecção automática —
Content ID e o fingerprint do TikTok casam áudio e vídeo, e narração própria com fundo
próprio não casa com nada. O risco é outro e é mais silencioso: as políticas de conteúdo
não-original (elegibilidade do For You no TikTok, conteúdo repetitivo/produzido em massa no
YPP) cortam **alcance e monetização sem emitir aviso**. Numa operação de três posts por dia
isso some sem sintoma, que é exatamente o tipo de falha que este projeto mais paga caro.
Segundo, roteiro é obra literária: recitar o texto é reprodução, e três strikes encerram o
canal do YouTube.

Daí a regra: o roteiro viral entra pela porta manual como **input do `/refine`**, no mesmo
lugar onde entraria um post do Reddit — nunca como o `script` final do `POST /pipeline`. O
refino reescreve premissa, ordem dos beats e gancho. O que sobrevive é a estrutura
dramática, que não é protegível e é justamente o que faz a história reter; o que morre é a
expressão literal, que é o que gera strike e o que gera o comentário "isso é copiado".

**O dedup do scout não cobre este caminho.** `seen_items` compara contra o que já foi
minerado, não contra a internet: material colado à mão não tem rede de segurança contra
republicar o mesmo roteiro duas vezes.

### A caixa de entrada de links

A regra acima diz *o que* fazer com um roteiro viral. Isto é *como* ele chega.

**O sinal que justifica a porta.** O ranking do Reddit mede quantas pessoas votaram; a
visualização de um vídeo mede que a história **reteve** quem começou a assistir. São coisas
diferentes, e a segunda é a que o pipeline nunca teve. Ela não é minerável: o TikTok não é
API pública, tem rate limit por IP e se defende de cliente automatizado. Então quem escolhe é
uma pessoa olhando o número, e o sistema só precisa não perder o link no caminho.

**O canal é o ntfy que já existe, num tópico separado.** Ele já é o destino das notificações
que saem, o app já está no celular e aparece na aba de compartilhar do Android — o caminho
vira TikTok → Compartilhar → ntfy, sem digitar nada e sem depender de estar na LAN de casa.
Um endpoint HTTP só funcionaria dentro do wifi; um bot de Telegram custaria token,
dependência e um serviço a mais para manter. **O tópico é outro**, porque no mesmo o serviço
leria as próprias notificações de saída e tentaria achar link nelas.

**A divisão entre os dois serviços segue o que cada um já tem.** O `tts_service` ganhou
`POST /transcribe` porque já carrega ffmpeg e faster-whisper na imagem; o `content_scout`
ganhou o laço porque já tem o dedup, o backpressure e o cliente do orchestrador. Nenhum dos
dois recebeu uma capacidade que o outro já tivesse.

**O que o TikTok cobra para entregar um vídeo** (medido em 27/08/2026, não suposto). O
diagnóstico importa porque cada obstáculo se parece com um problema diferente do que é:

| Obstáculo | Como aparece | Custo real |
|---|---|---|
| Fingerprint de TLS | HTTP **200** com casca de 1,4 KB; erro de "extractor" | uma flag e uma dependência |
| Desafio JS | — | zero, o yt-dlp resolve sozinho |
| Parser da página falhando | "Unable to extract universal data" | segunda tentativa pela API mobile |
| Rate limit por IP | URLs seguidas derrubam até a que funcionou | pausa entre requisições |

O primeiro é o que engana: um `200` com corpo vazio parece extractor desatualizado, e um
nightly compilado no mesmo dia falha igual. Nenhum dos quatro exige login, cookies ou
navegador — o que descarta as três soluções caras que pareciam necessárias antes da medição
(sessão logada, Playwright headless, API paga).

**Este caminho não escala, e não deve.** O rate limit é por IP e adaptativo: três URLs em
sequência rápida derrubaram as três, incluindo uma que funcionara segundos antes. Isso é
irrelevante para alguns roteiros por semana e proibitivo para mineração — que é exatamente a
divisão de trabalho pretendida. Minerar volume é papel do `content_scout` no Reddit; esta
porta existe para o punhado de histórias que já provaram reter.

**Ordem das checagens: barato antes de caro.** A transcrição custa ~70s de CPU por vídeo de
3 minutos (~0.4x tempo real), e é a etapa cara. Vêm antes dela a capacidade e a checagem de
reenvio do mesmo link — esta última existindo *só* para não pagar a transcrição, já que o
dedup de verdade (id do vídeo e fingerprint do texto) só fica disponível depois. Reenviar o
mesmo link é o engano mais provável de quem compartilha do celular, então a consulta barata
se paga.

**O fingerprint cruza as duas fontes.** Uma história que o scout já garimpou no Reddit é
reconhecida quando chega pelo TikTok, porque é a mesma coluna `content_fingerprint`. Isso
fecha o buraco que a regra anterior deixava explícito: material colado à mão passou a ter
rede contra republicação.

**Falha de plataforma não queima a história.** Duplicado e filtrado gravam linha em
`seen_items`; falha de transcrição **não**. Rate limit é transitório, e gravar ali faria o
dedup recusar o mesmo link no reenvio — que é justamente o que o aviso pede para fazer.

**Fila cheia recusa em vez de enfileirar.** Não há tabela de pendências, e engolir a
capacidade converteria o freio de memória do `blender_worker` em sugestão. O link é recusado
com aviso pedindo reenvio. É a limitação conhecida do desenho.

**Sem nota de storytelling e sem moderação.** A nota é sinal de *seleção* — ela ordena
candidatos entre si, e aqui não há ordenação: uma pessoa já escolheu olhando o view count. A
moderação continua valendo para o ciclo automático, onde ninguém leu o texto antes.

**O que a primeira medição revelou por acidente.** Os três vídeos testados eram posts de
subreddits **em inglês, traduzidos** — a hashtag do próprio criador diz `#reddit`. O
concorrente resolve a escassez de material pt-BR traduzindo o Reddit anglófono, não
transcrevendo. Os dois caminhos são complementares e nenhum descarta o outro: o TikTok dá
prova de retenção, o Reddit em inglês daria volume. O segundo está registrado como próximo
passo, não implementado.

---

---

## Descoberta de conteúdo (content_scout)

### Escolha da fonte

**Reddit, via feeds RSS.** Foram avaliadas três opções:

| Opção | Veredito |
|---|---|
| Reddit RSS | **Escolhida.** Sem credencial, sem aprovação, fora da cláusula não-comercial da Data API |
| Reddit Data API (OAuth) | Descartada por ora. 100 req/min sobrariam, mas exige pré-aprovação e o free tier proíbe uso comercial |
| YouTube (baixar + transcrever) | Descartada como fonte de roteiro — ver abaixo |

O `.json` sem autenticação do Reddit foi desativado em maio/2026 e responde 403. Os feeds RSS continuam abertos.

**Por que o YouTube não vira roteiro.** A transcrição de um vídeo *é* o roteiro de outra pessoa — republicá-lo com outra voz é cópia, não inspiração. Além disso, visualizações medem o canal, a thumbnail e o algoritmo, não o texto: otimizar por elas é perseguir o proxy errado. E o custo é ordens de grandeza maior (download + Whisper por vídeo, contra texto já pronto). Se o YouTube entrar, entra como **minerador de tema** — `search.list` para descobrir assuntos em alta, e o `llm_service` escreve roteiro original a partir do tema. Sem download, sem transcrição, sem risco de cópia.

### Escolha das comunidades

As fontes originais (`desabafos`, `relacionamentos`, `conselhos`) eram **relato real**: gente desabafando ou pedindo conselho. É um gênero sem terceiro ato — não há virada nem desfecho —, e o pipeline foi construído para história de entretenimento. A régua de storytelling chegava a pedir uma estrutura que aquele corpus não produz.

A troca foi decidida com medição ao vivo (28/07/2026), não por intuição: feed real, mesmo parser do pipeline, mesmos filtros de 600–6000 caracteres.

| Sub | passam os filtros | mediana | por quê |
|---|---|---|---|
| `EuSouOBabaca` | 15/15 | 1831 | o AITA brasileiro; o título já é o gancho |
| `story` | 13/15 | 1704 | história de entretenimento, em inglês |
| `stories` | 12/15 | 1587 | idem, e reposta muito de `r/story` |

Descartados por medição: `HistoriasDeReddit` e `HistoriasdeTerror` são em **espanhol**; `opiniaoimpopular` é opinião e não história (só 7/15 passam, mediana 551); e oito candidatos plausíveis (`Quem_Foi_O_Babaca`, `contosdevidareal`, `HistoriasBrasil`, `Creepypastas_Brasil` entre outros) devolveram **zero** posts na semana — são subs mortos. Não há sub de vingança em pt-BR.

**Consequência de interface:** duas das três fontes são em inglês, então o `/refine` passou a ter regra explícita de idioma — o roteiro final é sempre pt-BR, traduzido como quem reconta. Antes o prompt só pedia "preserve a essência", e o roteiro sairia em inglês para um TTS pt-BR. O `/story-quality` foi avisado do mesmo: julga a história, não o idioma.

**A régua também teve que mudar.** O prompt descontava por "pergunta direta ao fórum no lugar de história" — e todo post do `EuSouOBabaca` é literalmente "Sou babaca por…?". Sem qualificar a regra, o melhor corpus disponível tiraria nota baixa pelo motivo errado. Agora a distinção é explícita: a pergunta que vem *depois* do conflito e pede um veredito é estrutura de história; o que desconta é a pergunta que aparece *no lugar* da cena.

### Varredura do arquivo histórico

O feed `t=week` se renova sozinho; o `t=all` não. Pedir `top?t=all` a cada ciclo devolve **os mesmos quinze posts para sempre** — todos já em `seen_items` depois da primeira passada, ou seja, uma janela de rate limit gasta para não achar nada.

Os feeds Atom aceitam `?count=&after=`, verificado ao vivo: a segunda página voltou com **overlap zero** com a primeira. Então a varredura pagina para trás e um cursor por origem (`archive_cursors`) lembra onde parou. Isso destrava anos de acervo em vez de um top-15 fixo.

Regras de projeto:

- **Cadência no banco, não em contador de processo.** A varredura fica devida quando `last_swept_at` é mais velho que `archive_interval_hours`. Um contador em memória zeraria a cada deploy e dispararia varredura imediata.
- **No máximo uma varredura por ciclo, entre todas as fontes.** O recurso protegido é a janela de rate limit compartilhada, que não distingue quem a gastou. Subreddit nunca varrido tem prioridade, senão um sub recém-configurado esperaria o rodízio inteiro.
- **O cursor é o id da última `<entry>`, não do último candidato aproveitável.** Link e image posts são descartados na análise; paginar a partir do último sobrevivente faria a varredura re-pedir a cauda descartada toda vez.
- **Esgotar é normal.** Feed vazio devolve o cursor a `None` e a varredura recomeça do topo. O que a nova volta relê já está em `seen_items`, então uma volta custa requisição mas nunca republica.
- **Falha não derruba o ciclo, e o cursor não avança.** O arquivo é oferta extra sobre os feeds ao vivo — mesma assimetria da nota de storytelling. Tratar um 429 como esgotamento reiniciaria o sub do zero.

### Dedup por conteúdo: o mesmo texto sob outro id

`external_id` só reconhece o **mesmo post**. A varredura histórica alcança anos atrás e entra em comunidades que repostam umas às outras (`r/story` ↔ `r/stories`), então a mesma história chega de verdade duas vezes, com dois ids e títulos diferentes — e viraria dois vídeos iguais.

`seen_items.content_fingerprint` é o sha256 dos **1000 primeiros caracteres alfanuméricos** do corpo, minúsculo e sem acento. Cada decisão aí responde a uma forma de repost: descartar pontuação e caixa faz um texto redigitado casar; cortar no começo impede que um bloco `EDIT:` no fim derrube a comparação; tirar acento cobre o texto redigitado sem diacrítico.

- **Corpo que normaliza para vazio devolve `None`, não o hash de `""`.** Com o hash, todo candidato desses colidiria com todos os outros e o segundo seria descartado como repost do primeiro.
- **A coluna é indexada mas não é única.** Um repost precisa ser gravado com a própria linha de auditoria dizendo que foi pulado; uma constraint única rejeitaria exatamente essa linha e não sobraria registro da rejeição.
- **Cobertura começa na migration 004.** `seen_items` nunca guardou o corpo, só título, url e contagem de caracteres — o histórico anterior não pode ser reprocessado e fica `NULL`.
- A checagem roda **depois** do piso de tamanho e **antes** de moderação e nota: repost é a rejeição mais barata que existe e não pode custar chamada de modelo. Fica *antes* da nota justamente pelo contrário do que vale para o teto de tamanho (ver "Filtros") — um repost não é uma história que valeria a pena julgar, é uma que já foi julgada.

### Sinal de qualidade

O feed RSS **não carrega score**. Por isso pedimos `/r/{sub}/top/.rss?t=week`: a ordenação é feita pelo próprio Reddit e chega implícita na posição das entradas. É um sinal mais fraco que o upvote numérico, mas suficiente — o gargalo real é a fila de publicação, não a escassez de candidatos.

### Rate limit

Leitura não autenticada é limitada a aproximadamente **uma requisição por 40-60s** — a resposta traz `x-ratelimit-remaining: 0` e `x-ratelimit-reset: ~40` já na primeira chamada. Requisições em sequência fazem só o primeiro subreddit responder 200; o resto toma 429. Daí o espaçamento obrigatório (`request_delay_seconds`, padrão 60s — 45s ainda tomou 429 em teste real, a janela desliza). Cada subreddit extra custa uma janela por ciclo, então a lista deve conter só subs que rendem.

O limite é **por cliente, não por endpoint**: um feed de comentários pedido logo depois de um feed de listagem toma 429 com `x-ratelimit-used: 1`, porque a listagem já gastou a janela. Todo tipo de requisição divide o mesmo orçamento, e por isso o espaçamento é centralizado num throttle único dentro de `RedditSource`.

### Seleção entre candidatos

As fontes são buscadas e concatenadas na ordem do config. Pegar o começo dessa lista dava **todas** as vagas ao primeiro subreddit: medido ao vivo, as duas submissões vieram de `r/desabafos` enquanto `r/relacionamentos` contribuiu cinco candidatos e não ganhou nenhuma. Configurar mais subreddits era decorativo.

A seleção é por **rodízio entre origens** (`interleave_by_origin`), preservando o ranking interno de cada uma:

```
EuSouOBabaca[0], story[0], stories[0], EuSouOBabaca[1], ...
```

Isso mantém o ranking do Reddit como critério — continuamos pegando o melhor *disponível* de cada — e garante variedade de origem e tom entre vídeos consecutivos. Combinado com o dedup, o rodízio entre ciclos emerge sozinho, sem estado de rotação persistido.

**Não há score composto** (posição × tamanho × recência). Sem upvotes reais, qualquer peso seria inventado. Quando `seen_items` acumular histórico de performance, dá para ranquear com base em evidência.

Dentro de cada origem, a ordem deixou de ser só a posição no feed: o ranking interno é a **nota de revolta somada à de qualidade narrativa** (ver as duas seções abaixo), com a posição do feed como critério de desempate. O rodízio entre origens é anterior e independente — ele decide *de quem* é a vez, a nota decide *qual* história daquela origem.

### Qualidade narrativa: gancho e storytelling

O ranking do Reddit mede quantas pessoas votaram, não se a história **se conta bem**. São coisas diferentes: um desabafo desorganizado acumula upvotes por identificação e ainda assim não vira vídeo, porque a retenção no TikTok se decide nos primeiros dois segundos e ela depende da abertura, não do total de votos.

Daí um segundo sinal, independente do primeiro: `llm_service POST /story-quality` julga o **título + a abertura** de cada candidato e devolve, por candidato, um booleano `hook`, uma nota `score` de 0–10, a frase que serviu de gancho (`hook_line`) e um motivo curto.

**As duas perguntas são separadas de propósito.** `hook` pergunta se o título ou as primeiras linhas prometem um desfecho; `score` pergunta se a história como um todo se sustenta. Elas discordam nos dois sentidos, e cada discordância é um diagnóstico diferente: título ótimo sobre corpo que se perde, ou história boa que começa longe do conflito (*lide enterrado*). Colapsar as duas num número só apagaria a distinção que diz o que fazer com o post.

**Só a abertura é enviada, não o post inteiro.** Trinta posts completos seriam ~180 mil caracteres e destruiriam o batching (ver custo). Mas o recorte não é só economia: é o input honesto para a pergunta. O espectador decide com exatamente essa quantidade de texto, então julgar a abertura *é* julgar o que determina a retenção. O que essa escolha **não** consegue avaliar é se o post desanda no meio — uma limitação real, aceita porque um post que abre bem e desanda ainda é material aproveitável pelo refinamento, enquanto um que abre mal já perdeu o espectador.

**Custo: uma chamada por ciclo, não uma por candidato.** Este é o ponto que viabiliza a feature. A moderação pode rodar só nos 2–3 candidatos que vão ser publicados porque ela é um *gate*; a nota é um sinal de **seleção**, e pontuar só a cabeça da lista seria circular — é ela que define qual é a cabeça. Avaliar todos exigiria ~30 chamadas por ciclo, então o endpoint recebe uma lista e devolve uma lista: ~4k tokens de entrada numa chamada, mais barato do que a moderação já custa. Cada item leva o próprio `index` e os vereditos devolvem esse índice, de modo que um modelo que reordena ou omite entradas não desloca nota para a história errada.

**Falha aqui não derruba o ciclo — e essa assimetria com a moderação é deliberada.** A moderação decide se pode publicar, então "não deu para checar" tem que parar tudo. A nota decide apenas a *ordem*, então perdê-la custa o sinal e nada mais: `StoryQualityClient.score()` devolve mapa vazio em vez de levantar exceção, o ciclo cai de volta no ranking do Reddit e as colunas ficam `NULL`.

**Etiqueta, não filtro.** O veredito é gravado em `seen_items.story_tag`, derivado da nota contra `min_story_score`:

| Tag | Condição | Leitura |
|---|---|---|
| `weak_storytelling` | `score < min_story_score` | Não se conta bem |
| `no_hook` | nota ok, `hook = false` | Boa história, gancho enterrado |
| `strong` | nota ok e `hook = true` | Abre e se sustenta |

`weak_storytelling` tem precedência sobre `no_hook` — história fraca é o diagnóstico principal mesmo quando o título por acaso engancha —, e `has_hook` guarda a resposta crua nos dois casos, então nada se perde.

**Um candidato marcado como fraco continua sendo publicado** se não houver nada melhor atrás dele. Transformar a nota em corte rígido converteria um sinal probabilístico em filtro e poderia esvaziar a fila numa semana ruim, com o agravante de que o custo de um vídeo mediano é muito menor que o de não publicar. A nota atua na ordem; a etiqueta atua como informação — para o refinamento e para a calibragem.

**A escala vai até o topo, e o topo é alcançável.** O prompt reservava 9–10 para o excepcional, e a medição mostrou o efeito: **nenhum** dos 30 posts chegou lá, ou seja, a régua era efetivamente 2–8. Um teto que nunca é usado não é rigor, é resolução perdida — e a perda cai justamente onde a nota é usada, que é distinguir a história boa da ótima para decidir qual vai primeiro. O prompt agora manda usar a escala inteira: 9–10 é "você contaria isso adiante depois de ler", não uma raridade anual. **O meio não se moveu** — post comum de fórum continua em 4–6, e o corte `min_story_score` continua em 6, então relaxar o topo não inflaciona a taxa de `weak_storytelling`; ele só desempata melhor a cabeça da fila. Fixado em `tests/test_story.py`.

**A tag é derivada, não pedida ao modelo.** O modelo devolve nota; a linha entre fraco e forte é config (`min_story_score`, padrão 6 — a régua do prompt põe post comum de fórum em 4–6). Assim o corte se move contra dados reais via `GET /scout/seen?story_tag=weak_storytelling`, do mesmo jeito que `min_chars`/`max_chars` moram em config. `story_score` fica gravado cru, então mover o corte permite re-derivar as linhas antigas.

**`story_tag IS NULL` ≠ fraco.** Nulo significa não avaliado: o candidato barrado pelos filtros baratos (a nota roda depois deles, e depois do backpressure — fila cheia não publica, então não deve pagar julgamento) e todo candidato de um ciclo em que o `llm_service` caiu. **Filtrado não implica nulo**: quem caiu no teto de tamanho foi julgado antes de cair, e tem as colunas preenchidas. Um candidato sem nota ordena **no próprio limiar**, não no fim da fila: manda-lo para o fim converteria uma falha de modelo em handicap permanente para uma história que ninguém julgou, e são justamente as sobras de cada ciclo que herdariam esse handicap.

### Revolta: a emoção pela qual o canal seleciona

A nota de storytelling responde se a história **se conta bem**. Ela não responde se a história dá vontade de **comentar**, e é o comentário que move o alcance. O que produz comentário, no gênero que o canal publica, é a indignação: alguém claramente errado fazendo algo indefensável com quem não merecia — o namorado que traiu e quer voltar, a sogra que sabota, a amiga que conta o segredo.

Por isso o `POST /story-quality` passou a devolver dois campos a mais por candidato:

| Campo | Pergunta |
|---|---|
| `villain` (bool) | Existe alguém cujo comportamento é claramente indefensável? |
| `outrage` (0–10) | Quanta revolta essa história provoca em quem assiste? |

**São eixos separados de `score`, pelo mesmo motivo que `hook` e `score` são separados entre si.** Um relato mal escrito pode ser revoltante; uma história muito bem contada pode não ter vilão nenhum. Colapsar tudo numa nota só apagaria qual dos dois está faltando — e é essa distinção que decide se a história é boa para este canal ou só boa.

**O público está escrito no prompt.** O topo da escala (8–10) é reservado para a revolta que atinge o público principal: **mulheres de 18 a 35**. Revolta que só funciona para outro público — rixa entre desconhecidos, briga de trânsito — é revolta de verdade e pontua, mas não chega ao topo. Sem essa cláusula o modelo dá 9 para qualquer injustiça, e a nota deixa de separar o que converte do que apenas irrita.

**Não é decisão de segurança.** O prompt separa explicitamente as duas coisas: julgar potencial de reação não é aprovar o comportamento do vilão nem decidir se o assunto pode ir ao ar. Quem decide isso continua sendo o `POST /moderate`, e nada nesta seção afrouxa aquele gate.

#### A ordenação é uma soma com peso, não uma ordem de prioridade

```
chave = outrage_weight × outrage + story_score      (outrage_weight = 2)
```

Ordenar **só** por revolta poria uma história 10 de revolta e 3 de narrativa na frente de uma 9/9 — e uma história que ninguém termina de assistir não rende o comentário pelo qual a revolta foi escolhida em primeiro lugar. Com peso 2, dois pontos de revolta valem mais que quatro pontos de narrativa, e nada além disso. `outrage_weight = 0` reproduz exatamente a ordenação anterior, o que torna o botão auditável: existe um teste que fixa esse significado.

#### `min_outrage_score` é rótulo e contador, nunca portão

Mesma regra do `min_story_score`, pela mesma razão: num ciclo em que nada é revoltante, a melhor história disponível publica assim mesmo. Dia sem vídeo é pior que vídeo mais calmo, e o custo de errar para o lado do gate é uma fila vazia — que é o modo de falha mais caro que este pipeline tem. O corte serve para duas coisas: ordenar o candidato **não julgado** no ponto neutro, e contar `low_outrage` no relatório do ciclo, que é como a oferta passa a ser vigiada. `with_villain` conta o outro lado.

#### `outrage IS NULL` não é zero

Zero é um julgamento — "não há com quem se indignar". Nulo é o modelo não ter respondido o campo. Um veredito sem `outrage` **continua valendo pelo `score`**: a degradação é por campo, não por candidato, porque perder a nota inteira de uma história por causa de um campo ausente seria pagar caro por pouco. Toda linha anterior à **migration 005** também é nula, e pelo mesmo motivo do resto do histórico: `seen_items` não guarda o corpo, então nada pode ser re-julgado retroativamente.

`seen_items.outrage_score` (indexada) e `seen_items.has_villain` ficam cruas ao lado de `story_score`, exatamente para que o peso entre os dois eixos possa ser re-derivado depois contra o que já foi publicado, em vez de discutido.

#### O CTA da legenda passou a pedir o veredito — depois substituído (ver abaixo)

Até 31/08/2026 o `cta_per_part` ia para a legenda, e o prompt do `/refine` mandava a pergunta pedir o **veredito do espectador sobre o vilão** ("ela tava errada de perdoar?") quando havia um. A regra proibia xingamento e proibia mandar odiar alguém: a pergunta era o convite, a raiva era de quem respondia.

Esse mecanismo saiu da legenda em 31/08/2026, junto com o resto de `cta_per_part` (ver acima) — desde então a legenda não leva pergunta vinda do texto narrado. A ideia de "fechar o ciclo pedindo posição" continua, mas por outro campo: ver "CTA de votação binária na legenda", logo abaixo.

#### CTA de votação binária na legenda (02/09/2026)

A seleção já coloca uma história com vilão claro na frente (`outrage_weight`, acima); esta é a frente que tenta transformar isso em comentário de fato, sem tocar na REGRA DO FECHAMENTO (`llm_service/CLAUDE.md`) — a pergunta que fecha a *narração* continua nunca indo para a legenda.

`classification.binary_cta` (`llm_service/src/llm_service/schemas/refine.py`) é um campo **novo e independente** de `cta_per_part`: o prompt pede uma pergunta de **escolha entre duas opções** — "quem errou mais: o marido ou a sogra?", "comenta 1 se perdoaria, 2 se terminava na hora" — sobre o **dilema** da história, nunca sobre o desfecho. Escolher entre duas coisas custa menos atrito do que formular uma opinião do zero, e é isso que deveria aumentar o volume de comentário; não há medição ainda (ver `docs/comentarios.md`).

- Teto de `MAX_BINARY_CTA_CHARS` (100 caracteres), truncado sem partir palavra — o mesmo padrão de `truncate_title`. Existe porque o campo abre a legenda, e só os primeiros ~50–80 caracteres do TikTok aparecem antes do "...mais"
- Vazio (`""`) é resultado normal: história sem dois lados claros para dividir opinião não força uma pergunta artificial. Nesse caso a legenda segue como antes do campo existir — só hashtags e, em série, o rótulo de parte
- `tiktok_poster/hashtags/selector.py` → `compose_caption(cta, hashtags, part_number, total_parts, binary_cta="")` — `binary_cta`, quando presente, abre a legenda, antes do rótulo de parte e das hashtags. `cta` continua sem entrar no texto: é outro campo, com outra regra
- O dict `classification` trafega opaco do `llm_service` até o `tiktok_poster`, passando pelo orchestrador sem ser remapeado campo a campo (`orchestrator/src/orchestrator/clients/llm.py`, `clients/tiktok.py`) — por isso o campo não exigiu nenhuma mudança no orchestrador nem migração de banco. Um orchestrador antigo, que nunca viu este campo, simplesmente não o repassa, e `body.classification.get("binary_cta", "")` cai no vazio de sempre

### Filtros

Três etapas, separadas de propósito por custo e por natureza — e **encenadas nessa ordem porque o que a ordem decide não é só custo, é o que o sistema fica sabendo**:

**1. Piso de tamanho (`filters.evaluate`).** Curto demais não sustenta um vídeo. Roda sobre todo candidato, é grátis.

**2. Teto de tamanho (`filters.exceeds_length`), *depois* da nota.** Longo demais custa mais narração e render do que uma vaga vale — mas isso é restrição de **produção**, não juízo sobre a história, e por isso não pode rodar antes de a história ser julgada.

O teto morava na etapa 1, junto com o piso, e a assimetria foi medida sobre 45 posts reais das três comunidades configuradas (14/08/2026):

| | dentro do teto | acima do teto |
|---|---|---|
| candidatos | 34 | 11 |
| notas | 2,3,3,4,4,5,5,5,6×8,7×5,8×9,9×4 | 6, 7×3, 8×3, 9×4 |

**Nenhum dos 11 acima do teto tirou menos que 6**, e todos os oito candidatos com nota ≤5 estavam dentro dele. Metade das notas 9 estava acima. Não é coincidência — `r/story` e `r/stories` são onde mora o gênero "história escrita para entreter", e o gênero premia texto longo. Cortar por tamanho antes de julgar era, na prática, correlacionar o filtro negativamente com a qualidade.

O piso não tem esse problema e por isso ficou onde estava: abaixo de `min_chars` não há história para o modelo pesar, então a rejeição é tão verdadeira antes do julgamento quanto depois. O teto rejeita algo que o julgamento tinha o que dizer sobre.

Consequência: o candidato longo é buscado, deduplicado, **pontuado** e gravado em `seen_items` com `story_score`/`story_tag` preenchidos, `status=filtered` e `skip_reason=too_long:{n}`. Ele nunca vira vídeo, mas a trilha de auditoria passa a responder *o que* foi deixado passar — que é o dado necessário para decidir se `max_chars` está no lugar certo. Sem isso, mover o teto seria chute: as linhas rejeitadas não diziam nada sobre a qualidade do que se estava recusando.

**O teto continua sendo o gate de produção.** Subi-lo é o que transforma essas linhas em vídeo, e o refino sabe lidar com o resultado. Não há nada abaixo do scout que quebre com roteiro longo — `raw_script` e `script` são `Text` sem limite.

**E foi subido: 6000 → 30000.** Com a medição acima dizendo que o teto antigo recusava o melhor material, mantê-lo em 6000 seria conhecer o erro e não corrigi-lo. O valor novo era **derivado, não escolhido** *(a conta de então — ver o aviso abaixo, ela não vale mais)*: era o maior post cru que o refino ainda entrega como *um* vídeo. `MAX_PART_WORDS` são 5850 palavras; o pt-BR mede **5,54 caracteres por palavra** sobre os 30 posts de `content_scout/docs/story_quality_baseline.json`; logo uma parte comporta ~32400 chars. O corte em 30000 deixa ~7% de folga para o refino expandir o texto ao reescrever — ele reconta a história, não a copia, então encostar em 32400 arriscaria uma divisão acidental.

Por que essa era a única linha não arbitrária disponível: acima dela a história **não era recusada pelo pipeline**, ela virava série com cliffhanger. O teto deixou de ser um palpite sobre custo de render e passou a marcar uma fronteira de formato — onde um post deixava de ser um vídeo.

⚠️ **Esta derivação caducou em 31/08/2026, e o valor sobreviveu por outro motivo.** Com o
formato curto não existe mais `MAX_PART_WORDS`, não existe divisão e nenhum tamanho de post
cru "estoura uma parte": o refino condensa qualquer entrada nas mesmas 150 palavras. O teto
deixou de marcar fronteira de formato e passou a ser o que sempre foi por baixo — **um limite
de custo e de qualidade de entrada**: o post cru é o prompt do refino, e um texto de 30000
chars já é uma chamada cara. 30000 fica onde está porque a medição acima mostrou que ele não
recusa o bom material; a conta que o derivava não vale mais, e subi-lo agora **não tem
consequência nenhuma de formato** — só de token.

O contador `too_long` no `ScoutReport` é o recorte dessa rejeição dentro de `filtered`, que continua sendo o total.

⚠️ **A rejeição por teto roda depois do backpressure, como a nota.** Fila cheia encerra o ciclo antes de pontuar, então o candidato longo **não** é gravado nesse ciclo — ele volta inteiro no próximo. Gravá-lo ali o queimaria sem nota, que é exatamente o estado que esta mudança existe para evitar.

⚠️ **O teto roda antes da moderação, não dentro do laço de submissão.** A nota é uma chamada em lote por ciclo; a moderação é uma chamada por candidato. Deixar o candidato longo entrar no laço faria uma história que nunca seria publicada pagar uma chamada de moderação, e a rejeição por tamanho é determinística — não precisa de modelo para acontecer.

**3. Moderação por LLM (`llm_service POST /moderate`).** Decide se publicar coloca a conta em risco de remoção.

A versão anterior era uma blocklist por substring, e ela errou de forma instrutiva: `me matar` casou dentro de `"Eram 3 mil que não me mataria"` — figura de linguagem sobre dinheiro — descartando uma história boa. Enquanto `"disseram que depois de me matar iam fazer com ela..."` é ameaça real e precisa ser barrada. **As duas contêm a mesma sequência de caracteres.** Segurança é julgamento de contexto, não casamento de padrão.

O prompt é explícito em aprovar histórias pesadas — término, traição, briga de família, demissão, dívida, luto — porque esse é o material do produto. O que barra é automutilação, abuso sexual, violência gráfica, ódio e conteúdo envolvendo menores.

**Custo.** Roda por publicação, não por post buscado: só nos candidatos que já passaram tamanho, dedup e ordenação, e apenas até o orçamento do ciclo encher. Duas a três chamadas por ciclo em vez de ~30. Modelo configurado em `LLM_MODERATION_MODEL`, separado do modelo de refino — a chamada é um sim/não.

**Falha de moderação não é veredito.** `ModerationError` é distinto de `safe=false`: o candidato **não** é gravado em `seen_items`, o ciclo encerra, e a história continua disponível depois. Uma indisponibilidade não pode nem publicar sem checagem, nem queimar história boa em definitivo.

### Enriquecimento com comentários

O que o feed RSS entrega por entrada, medido: `author` (nome e URL da conta), `category` (o subreddit), `content` (corpo), `id` (fullname `t3_…`), `link`, `published` e `updated`. **Não** entrega score, número de comentários, thumbnail, flair, prêmios nem flag NSFW — nenhum deles existe no XML.

O número de comentários é obtido do feed do próprio post (`/comments/{id36}/.rss`), que devolve a submissão como primeira entrada (`t3_`) seguida das respostas (`t1_`). Contar os `t1_` é a contagem. Ela é **piso, não censo**: respostas apagadas, removidas ou colapsadas não aparecem. Serve como sinal de repercussão, não como métrica exata — e não deve ser reportada como se fosse.

**Por que contagem de comentários e não upvotes.** Os feeds não expõem voto em lugar nenhum — nem do post, nem dos comentários (verificado nos dois feeds). Quantas pessoas responderam é o proxy de engajamento disponível. É um sinal diferente do upvote, não um substituto: mede quem se sentiu compelido a escrever, o que num subreddit de desabafo tende a acompanhar história que mexeu com alguém.

**Custo e onde ele cai.** O rate limit do Reddit é **por cliente, não por endpoint** — medido ao vivo, um feed de comentários pedido logo após um feed de listagem responde 429 com `x-ratelimit-used: 1`: a listagem já gastou a janela. Cada post enriquecido custa portanto uma janela inteira (~60s). Enriquecer todos os candidatos custaria ~45 min por ciclo, inviável.

Por isso o enriquecimento roda **no mesmo ponto que a moderação**: só nos candidatos que já passaram tamanho, dedup e ordenação e estão prestes a ser publicados. Com `max_per_cycle = 2`, são ~2 minutos extras por ciclo. É desligável em `[scout] fetch_comments`.

Como o limite é do cliente e não do método, o espaçamento vive num throttle compartilhado dentro de `RedditSource`, atravessado por toda requisição. Deixá-lo dentro de `fetch()` protegeria só as chamadas daquele método, e cada endpoint novo teria que reinventar o espaçamento.

**"Não consultado" ≠ "zero comentários".** `comment_count` é nulo quando o item nunca foi enriquecido — todo candidato filtrado, e todo caso em que o feed falhou. Gravar `0` afirmaria que o post não teve reação alguma, o que é uma alegação diferente. Falha de enriquecimento nunca bloqueia a publicação: é um bônus, não um pré-requisito.

**Bodies são amostra, contagem é o sinal.** Só as primeiras `max_comments_stored` (padrão 20) respostas têm o texto guardado, em `item_comments`. Tabela própria em vez de JSON em `seen_items` porque as perguntas interessantes são *entre* comentários — que autores reaparecem, que tamanho as reações têm — e isso é desconfortável contra JSON aninhado. Não há coluna de score, pelo motivo acima; `position` preserva a ordem da fonte, que é o único ranking disponível.

**Capacidade opcional.** Comentários são um Protocol separado (`CommentCapableSource`), não parte de `Source`. Uma fonte sem comentários — ou cuja API os torne caros demais — continua sendo uma fonte válida; o scout testa a capacidade e pula o enriquecimento quando ela não existe.

### Contrato com o orchestrador

O metadata enviado em `POST /pipeline` ganha campos opcionais, todos vindos do scout:

| Campo | Origem | Quando está presente |
|---|---|---|
| `author` | `<author><name>` do feed | Sempre que a fonte expõe autor |
| `comment_count` | contagem de `t1_` no feed do post | Só quando o enriquecimento rodou e teve sucesso |
| `story_tag` | derivado de `story_score` | Só quando a avaliação narrativa rodou |
| `story_score` | `POST /story-quality` | idem |
| `has_hook` | `POST /story-quality` | idem |
| `hook_line` | `POST /story-quality` | Só quando o modelo achou uma frase de gancho |

São aditivos e opcionais — o orchestrador e o `llm_service` seguem funcionando sem eles, e submissões manuais nunca os terão. O orchestrador repassa o dict inteiro ao `POST /refine` sem interpretar nada, então nenhuma mudança de schema é necessária nele.

**Por que os campos de história viajam.** O prompt de refino manda abrir com um gancho forte. Saber que o post **não** tem gancho é a diferença entre polir uma frase que já existe e ter que construí-la; e quando existe, `hook_line` diz qual é a frase que merece ficar na frente. Os campos ausentes são deliberadamente ausentes e não `null`: dizer ao refinador que a história é fraca quando ninguém a avaliou seria pior que não dizer nada.

### Dedup e auditoria

`seen_items` guarda **todo** candidato avaliado — inclusive os rejeitados, com o motivo. Serve a dois propósitos: impedir que a mesma história vire um segundo vídeo quando reaparece no top da semana seguinte, e permitir calibrar os limiares contra dados reais em vez de chute.

Além do veredito, a linha guarda `author` e — para os enriquecidos — `comment_count` e as respostas em `item_comments`. Com o tempo isso vira a base para ranquear por evidência: repercussão no Reddit contra performance real no TikTok.

### Backpressure

Antes de submeter, o scout conta os runs ativos no orchestrador (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se atingiu `max_pending_runs`, o ciclo não submete nada.

A razão é a fila do Buffer, que segura 10 posts: ingerir mais rápido do que se publica não gera mais vídeos, só converte roteiro bom em run falho. Enquanto a feature "Fila de espera quando o Buffer está cheio" (ver Backlog) não existir, o backpressure é a única proteção contra isso.

**Ordem das etapas.** A capacidade é verificada *depois* de registrar os filtrados e *antes* de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o ciclo seguinte parte de uma pilha menor.

### Periodicidade

Loop `asyncio` iniciado no `lifespan` do serviço, intervalo configurável. Não precisa de scheduler durável porque `seen_items` torna o ciclo idempotente: um restart no pior caso repete uma passagem que não encontra nada novo.

### Um ciclo por vez

`run_cycle` é serializado por um lock de processo. Quem chega no meio de um ciclo recebe um relatório vazio com `already_running=True` em vez de esperar minutos ou — pior — correr em paralelo.

**O que dois ciclos simultâneos causavam,** medido ao vivo: o loop periódico dispara um ciclo no startup, e um `POST /scout/run` logo depois de um deploy corria junto com ele. Duas consequências, ambas observadas:

1. **Espaçamento do Reddit pela metade** (34s em vez de 60), e os dois ciclos tomando 429. O throttle era criado por instância de `RedditSource`, e `build_sources()` cria uma nova a cada ciclo — então ele espaçava requisições *dentro* de um ciclo e mais nada. Agora o `_Throttle` é estado de processo (`shared_throttle()`), porque o limite do Reddit é por cliente, não por objeto.
2. **`UniqueViolationError` em `seen_items.external_id`**, derrubando o ciclo com 500. O dedup é check-then-insert, e os dois ciclos passaram pela mesma leitura antes de qualquer escrita.

### Gravação por linha, não em lote

O commit em lote no fim do ciclo era o que transformava o conflito acima em perda de dados: um único duplicado desfazia **todas** as linhas do ciclo, inclusive as `submitted` cujos `pipeline_run` já existiam no orchestrador. Essas histórias voltavam a aparecer como inéditas no ciclo seguinte e seriam publicadas duas vezes. Medido: 4 runs criados, 2 registrados.

`_record()` grava e commita cada linha na hora, tolerando `IntegrityError` (o candidato já foi registrado por outro escritor) em vez de propagar. A linha `submitted` é gravada imediatamente após a submissão, com o `run_id` em mãos — nada que falhe depois pode apagar o registro de uma história que já está no pipeline.

### Adicionando fontes

Toda fonte implementa o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) e devolve `Candidate` com `external_id` estável, que é a chave de dedup. Dedup, filtros, orçamento e backpressure tratam todas as fontes igualmente.

Comentários são uma **capacidade opcional**, no Protocol separado `CommentCapableSource` (`async fetch_comments(candidate) -> CommentThread | None`). Implementar é opcional: o scout detecta a capacidade e simplesmente não enriquece quem não a tem. `None` significa "não deu para consultar" e é distinto de uma thread vazia, que significa "não teve resposta".

---

## Operação 24h

O alvo é uma máquina ligada o tempo todo, sem ninguém olhando. O que o `docker-compose.yml` garante:

**`restart: unless-stopped` em todos os serviços**, via a âncora `x-runtime`. Sem isso, um container que morre — ou um reboot do host — deixa o serviço fora do ar até alguém reparar. `unless-stopped` e não `always` para que uma parada deliberada continue valendo depois do reboot.

**Log limitado** (`max-size: 10m`, `max-file: 3`) e `log_level = INFO` em todos os `config.ini`. Em `DEBUG` cada chamada de boto3 e httpx despeja cabeçalhos completos de request e response: o log do `blender_worker` gerava megabytes por render, e o driver `json-file` sem limite não descarta nada. Numa máquina que roda meses, é o disco que acaba primeiro.

**Cache do Whisper em volume** (`whisper_cache` em `/root/.cache/huggingface`). Os pesos (~420 MB) são baixados no primeiro uso e iam para a camada gravável do container: todo recreate baixava de novo, e um restart com o HuggingFace fora do ar deixava o serviço incapaz de transcrever — sem SRT, o render não acontece.

**Um render por vez, e um teto de memória para ele.** A máquina alvo tem 8 GB e um render 1080×1920 custa ~1–2 GB, então a concorrência de render é o único item do orçamento que estoura. Ela era ilimitada em dois pontos, e os dois foram fechados:

- **`[blender] max_concurrent_renders` (padrão 1)** — `POST /jobs` entrega todo job a `BackgroundTasks`, que não impõe limite nenhum: N jobs aceitos eram N processos Blender disputando a mesma RAM. `render_job` agora espera um `asyncio.Semaphore` antes de começar, e quem espera **continua `pending`** — que é exatamente o que esse status já significa para o orchestrador, que faz polling. Nada é recusado, nada se perde; só deixa de acontecer junto.
- **`[scout] max_pending_runs`: 5 → 2** — o freio a montante. Ele limita quantos runs ficam em voo, o que limita quanto trabalho chega ao gate acima. Com 3 publicações/dia, 2 em produção simultânea mantém a fila cheia: o corte remove um pico, não throughput.

**`mem_limit: 3g` no `blender_worker`.** Contraintuitivo, mas é proteção. Sem limite, quem o OOM killer derruba é arbitrário — e se for o Postgres, perde-se o estado de **todos** os runs, não um render. Com limite, quem morre é o render: o job vira `failed` com o motivo, e a recuperação de runs órfãos no boot cuida do resto. É trocar uma falha catastrófica por uma recuperável. O valor é ~2× a estimativa de um render; medir um real e apertar.

**O que continua sendo responsabilidade de fora do compose:**

- Um daemon Docker Linux. O alvo é uma máquina Debian dedicada (ver [`deploy.md`](deploy.md)); Docker Desktop no Windows não serve para 24h, porque exige sessão de usuário logada.
- Espaço em disco: as imagens somam ~36 GB e o build cache cresce sem limite (`docker builder prune`).
- Backup do Postgres e retenção dos `outputs/` no R2 — nada é apagado hoje.

**O plano de deploy está em [`deploy.md`](deploy.md)** — máquina alvo, orçamento de RAM, os ajustes que sobraram para a hora de subir e o desenho do monitoramento em camadas com notificação por WhatsApp. Os ajustes de concorrência e o `mem_limit` que aquele documento pedia já estão aplicados no repo (acima); o que resta lá é configuração da máquina. **A camada 3 daquele documento — a que depende de código — está implementada** (ver abaixo); as camadas 1, 2 e 4 são configuração da máquina e continuam pendentes.

---

## Notificação de operação

O pipeline roda sozinho e não tinha nenhuma voz. Um run `failed` não avisava ninguém, e a falha pior nem se registrava em lugar nenhum: todos os `/health` respondendo `200` e mesmo assim nenhum vídeo saindo.

O módulo é `src/core/notify.py`, **copiado byte a byte** entre `orchestrator` e `content_scout` — mesmo padrão de `bootstrap.py` e `logger.py`, que já são duplicados assim. Ele não importa o `EnvSettings` de nenhum serviço justamente para permanecer copiável para um serviço novo sem edição.

### Duas garantias, e elas definem a forma do módulo

**Nunca levanta exceção.** `notify()` é envolvido inteiro num `try`. Um monitoramento que derruba o run que ele monitora produz exatamente a falha que ninguém consegue diagnosticar, porque o canal de diagnóstico é ele mesmo.

**Nunca bloqueia quem chama.** `notify()` é **síncrono** e só enfileira; quem fala com a rede é o `sender_loop`, no fundo. Ser síncrono é o que impede que um enganche de notificação vire ponto de suspensão no meio de uma transação. Um envio leva ~1s e um run emite ~14 eventos — síncrono, seriam minutos de pipeline gastos com mensagem de celular.

### Eventos e níveis

Cada evento tem um nível (`debug` / `info` / `warning` / `error`) e o corte mora em `[monitoring] level`, nos dois `config.ini`. O default é **`debug` — tudo**, deliberadamente: no começo a pergunta a responder é "o que este sistema faz quando roda sozinho", e ela não se responde vendo só os erros. Subir para `info` corta o miúdo (TTS e render de cada parte, card, gancho, resumo do ciclo do scout) e deixa os marcos.

**A categoria `warning` é a que não existia em lugar nenhum.** São as degradações que não marcam o run: gancho não narrado, card não composto, `narration.rate` não lido, biblioteca de fundos vazia, parte sem `video_key`. Em todas, o vídeo publica e o run termina `scheduled` — o status não distingue um vídeo íntegro de um vídeo capado. Sem o aviso aqui, não há aviso em lugar nenhum.

**O estágio da falha é lido antes de `status = failed`.** Depois da atribuição todo run falha "em `failed`", e o estágio é a única pista na mensagem sobre onde procurar.

### Destinos

Credenciais vêm do **ambiente**, comportamento vem do `config.ini`. Chave de API não entra em arquivo versionado; verbosidade e cadência não são segredo.

| Destino | Var | Papel |
|---|---|---|
| Webhook | `NOTIFY_WEBHOOK_URL` | POST — ntfy, Discord, WAHA. **É o destino em produção desde 16/08/2026** |
| CallMeBot | `CALLMEBOT_PHONE` + `CALLMEBOT_APIKEY` | WhatsApp, grátis. **Desligado em produção — cota esgotada** |

Os dois podem estar ligados ao mesmo tempo, que é o caminho de migração para um destino novo sem apagar o antigo antes de saber que o novo funciona.

#### O corpo do webhook: `[monitoring] webhook_format`

`json` manda `{"text": ...}` (Slack e proxies que esperam isso); `text` manda a mensagem crua no corpo. O ntfy mostra o corpo como veio — em `json` o celular receberia o literal `{"text": "🎬 Vídeo renderizado..."}`, com chaves e aspas. **O default do código continua `json`**, e só o `config.ini` dos serviços está em `text`: mudar o default quebraria em silêncio qualquer destino já apontado para o formato antigo, que é a classe de falha que esta seção inteira existe para evitar.

#### A recusa disfarçada de sucesso

**O CallMeBot responde `200` mesmo quando não envia.** Cota esgotada, apikey inválida, número não autorizado — o status é sempre `200` e o motivo vem só no corpo:

```
<p>Message to: +55...<p style="color:red">You have <b>0</b> messages left.</p>
<p style="color:red"><b>Message not sent</b>
```

Como `_deliver` só fazia `raise_for_status()`, toda recusa passava como entrega. Em 16/08/2026 a cota grátis zerou e **o monitoramento morreu em silêncio**: o log registrou `200 OK` por dias, para mensagens que nunca saíram, e a falha só foi descoberta porque nenhuma mensagem chegou ao celular.

É a pior forma da falha, e não por acaso: o canal de aviso é o único componente cuja morte não pode ser anunciada por ele mesmo. Um canal que morre em silêncio é **pior** que canal nenhum, porque a ausência de mensagens passa a ser lida como "nada aconteceu" em vez de "não estou mais te avisando" — a mesma inversão que os dead-man's switches abaixo existem para desfazer.

Agora `_deliver` lê o corpo e loga `notify_rejected` com o motivo. ⚠️ **A checagem é pelo marcador de sucesso (`queued`), não por uma lista de textos de erro conhecidos.** Uma lista de erros só pega o que já se viu: um modo de recusa novo não estaria nela e voltaria a passar batido — exatamente o buraco que custou esses dias. Checando o sucesso, o desconhecido cai no `else` e vira aviso. A troca é assumida: o risco vira um alarme falso no log se o CallMeBot mudar a palavra de sucesso, e alarme falso no log é muito mais barato que outro silêncio.

⚠️ **Isto continua sendo um `log.warning`, não um `notify()`** — avisar pelo canal que acabou de falhar seria circular. Quem fecha essa volta é o dead-man's switch, de fora. **Sem nenhum dos dois configurados, todo `notify()` é no-op** — não é só o envio que para, é o enfileiramento: uma fila que ninguém drena encheria em ambiente de desenvolvimento e na suíte de testes.

O `sender_loop` espaça os envios (`min_interval_seconds`, 3s). Não é educação com o servidor: o CallMeBot recusa rajadas, e rajada é exatamente o que um run produz — refino, gancho e card saem em segundos um do outro. Sem o espaço, quem se perde é a metade final de cada run.

### Dead-man's switches — a camada 3 do `deploy.md`

O inverso de um alerta: quem avisa é o **silêncio** dos pings, do lado de fora. É a única forma de detectar uma máquina que morreu sem conseguir reportar a própria morte.

| Check | Quem pinga | Janela | Que falha pega |
|---|---|---|---|
| `alive` | `maintenance_loop` do orchestrador, a cada ciclo (15 min) | curta | o processo morreu |
| `scout` | fim de cada ciclo do scout (1h) | média | a fila secou em silêncio |
| `produced` | **só** quando um run termina em `scheduled` | **larga** (24–36h) | parou de sair vídeo com tudo respondendo 200 |

Destino em `HEALTHCHECK_{CHECK}_URL`; sem a var, é no-op.

⚠️ **São três, e não um, de propósito.** O `deploy.md` desenhava um único check pingado no sucesso, com alerta em 12h. Isso confunde "o pipeline quebrou" com "o scout não achou material bom": num fim de semana devagar, 12h sem run concluído é plausível com tudo funcionando, e alarme falso de madrugada treina a pessoa a ignorar o alerta — que é como monitoramento morre de verdade. Com `alive` e `scout` cobrindo "os laços estão de pé", o `produced` pode ter janela larga sem virar ponto cego.

⚠️ **O `produced` não é pingado quando o Buffer está cheio.** Um run parado esperando vaga não produziu nada. Pingar ali faria o switch afirmar que o sistema está entregando justamente enquanto ele parou — e um dead-man's switch que mente é pior que não ter nenhum, porque **compra silêncio**. Coberto por teste.

### O que ainda não é observável

**"Vídeo publicado" não existe como evento.** O pipeline termina em `scheduled` — daí em diante quem publica é o Buffer, no horário marcado, e nada no sistema marca `PipelineStatus.posted`. A notificação mais próxima é `📅 Publicação agendada`, com o horário. Fechar essa lacuna exige o `tiktok_poster` consultar o Buffer depois do horário e reportar de volta; não está implementado.

### Fuso horário

As mensagens mostram o horário agendado de cada publicação, formatado com `astimezone()` — o fuso do processo. Daí `TZ` no `docker-compose.yml` para `orchestrator` e `content_scout`. Sem ele o container roda em UTC e o horário sai 3h adiantado: errado de um jeito que parece certo, que é o pior tipo de erro numa mensagem de alerta.

---

## Tech Stack por Serviço

| Serviço | Stack |
|---|---|
| `orchestrator` | FastAPI + SQLAlchemy + PostgreSQL |
| `llm_service` | FastAPI + OpenRouter / Claude API / Chutes AI (configurável por env) |
| `tts_service` | FastAPI + Azure Speech (padrão) / edge-tts (fallback) + ffmpeg (→ ElevenLabs futuramente) |
| `blender_worker` | FastAPI + Blender 4.2 LTS + Pillow (existente) |
| `tiktok_poster` | FastAPI + TikTok API |
| `content_scout` | FastAPI + SQLAlchemy + PostgreSQL + httpx |
| `dashboard` | HTML/JS servido pelo orchestrador (MVP) |

---

## Fluxo de Arquivos (MinIO)

```
tts_service     → audio/{pipeline_run_id}/part_{n}.mp3
                → subs/{pipeline_run_id}/part_{n}.srt   (legenda word-level, via Whisper)
                → audio/{pipeline_run_id}/hook.mp3      (frase gancho narrada sozinha)
                → subs/{pipeline_run_id}/hook.srt
blender_worker  → outputs/{job_id}.mp4   (vídeo renderizado)
                → outputs/{job_id}.blend  (cena Blender montada, para inspeção/reuso)
```

O orchestrador armazena as keys MinIO de cada artefato no `pipeline_run` para passá-las para os próximos serviços.

---

## Backlog de Features

Features planejadas, ainda não implementadas. Cada entrada descreve o problema, o comportamento proposto e o que precisa mudar — o suficiente para uma sessão futura implementar sem redescobrir o contexto.

### Fila de espera quando o Buffer está cheio — **implementado**

Fila cheia deixou de ser falha terminal. O `429 buffer_queue_full` mantém o run em `scheduling` com os vídeos intactos, e `retry_pending_schedules()` (no `maintenance_loop`) reoferece a cada `[pipeline] retry_interval_seconds`. Detalhes e razões em "Estado do Pipeline → `scheduling` é um estado de espera".

**Duas decisões diferem do que este backlog previa:**

- **Sem estado `awaiting_slot`.** `scheduling` já significa exatamente isso — "renderizado, aguardando o poster" — e já era contado como capacidade ocupada pelo scout, que é o efeito que se queria. Um valor novo no enum exigiria migration e um segundo estado com a mesma semântica.
- **Sem scheduler durável.** O pré-requisito registrado aqui (Celery/APScheduler) não foi necessário porque o retry é idempotente e o estado vive no Postgres, não na memória: `recover_interrupted_runs()` reconcilia no boot e o loop periódico faz o resto. Um restart no meio custa uma varredura repetida, não um run perdido.

**O que ainda falta: atomicidade de série.** Hoje as partes são agendadas uma a uma; se a fila fechar entre a parte 1 e a 2, a parte 1 fica agendada e a 2 espera a próxima varredura. Como as partes são agendadas em dias consecutivos e o retry roda a cada 15 min, a janela é pequena, mas existe — e publicar um cliffhanger sem continuação é pior que atrasar a série inteira. Resolver exige que o `tiktok_poster` exponha as vagas livres na resposta do `429`, para o orchestrador decidir antes de começar.

---

### Multi-conta (dezenas a centenas de contas) — **proposta, não implementado**

Operar N contas de TikTok+YouTube reaproveitando roteiro entre elas. O desenho completo está
em `docs/multi_account.md`: o que trava primeiro (render a ~113 partes/dia, disco a ~30
GB/dia, oferta do scout — o banco não), a separação de `pipeline_runs` em
`stories`/`renders`/`publications`, onde as credenciais passam a morar, e as três fases.

A tensão que o documento existe para registrar: **reuso economiza roteiro, não render.**
Mesmo MP4 em N contas é o fingerprint que derruba a rede inteira de uma vez; variante por
conta devolve o custo de render por conta. O que decide a escala é o custo da variante mais
barata que ainda é distinta — e esse número ainda não foi medido.

---

## Decisões em Aberto

- **Schema de classificação por tipo de conteúdo**: o LLM recebe um schema fixo ou gera livremente e o orchestrador valida? → Definir quando implementar o llm_service.
- **TikTok API**: autenticação OAuth vs. token estático de longa duração → Definir quando implementar o tiktok_poster.
- **Dashboard**: servido pelo orchestrador (FastAPI + Jinja2) ou container Next.js separado → MVP usa Jinja2, pode migrar depois.
- **Retry automático**: se TTS ou render falhar, o orchestrador retenta automaticamente ou só marca como `failed`? → MVP marca como failed. O caso de fila cheia do Buffer é diferente (falha temporária, não erro) e já tem solução desenhada em "Fila de espera quando o Buffer está cheio", no Backlog de Features.
- **Escolha automática de template pelo `content_scout`**: hoje o disparo automático nunca varia de template (ver "Escolher o template por vídeo, não só pela conta inteira"). Split fixo por slot, sorteio com peso, ou continuar manual-only — critério não definido, e não vale decidir sem primeiro ver o formato novo rodando manualmente.
- **O `content_scout` escolher conta sozinho** (Fase 1 do multi-account, ver "Contas de publicação"): hoje toda descoberta automática publica na conta default, e a conta extra só recebe run por disparo manual com `account_id`. Round-robin com backpressure por conta é o próximo passo natural, mas foi adiado por decisão deste ciclo — mesmo raciocínio da escolha automática de template, acima: não vale desenhar a distribuição antes de ter mais de uma conta rodando de verdade em produção.
