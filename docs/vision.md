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

`_schedule()` é idempotente: parte com `scheduled_at` preenchido é pulada, então re-oferecer um run nunca republica o que já tem vaga. `retry_pending_schedules()`, no `maintenance_loop`, drena os runs parados a cada `[pipeline] retry_interval_seconds` (900s).

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

**`cta_per_part` é legenda, não roteiro.** O prompt de refino pedia que *cada parte terminasse com um CTA*, e as partes são narradas literalmente (`text=part.script`) — o CTA era falado no vídeo, depois do desfecho da história. São dois artefatos diferentes com o mesmo nome: o campo, que o `tiktok_poster` usa em `compose_caption()`, e uma frase escrita dentro do texto narrado. O primeiro fica; o segundo saiu do prompt, junto com qualquer outra forma de finalização (despedida, moral, "e é isso"). O texto narrado termina na última coisa que acontece na história.

O corte com cliffhanger continua valendo: um corte no meio da tensão é parte da história, não uma finalização colada nela.

---

## Divisão em Partes

**O padrão é não dividir.** A história completa vai num vídeo só; o LLM devolve `parts` com um elemento e `split_rationale` nulo. Dividir é a exceção, e só quando a narração passaria de **30 minutos** — `MAX_PART_MINUTES` em `llm_service/prompts/refine.py`.

O formato anterior cortava em 600 palavras (~1 min de fala, segundo o prompt). Era o inverso da regra: uma história de 6000 caracteres — o teto de ingestão do scout — virava seis vídeos, cada um abrindo com "Na parte anterior..." e fechando com um CTA pedindo a próxima parte. O espectador que engatava na parte 1 precisava caçar mais cinco publicações, espalhadas por seis dias (ver "Agendamento de séries"), para chegar ao fim de uma história de seis minutos. Vídeo longo com história inteira retém melhor que seis fragmentos porque não pede nenhuma ação para continuar.

- Se dividido, o LLM escolhe o ponto de corte que maximize a curiosidade (cliffhanger natural).
- Para partes 2+, o LLM gera um resumo curto ("Na parte anterior...") que é inserido no início do roteiro daquela parte antes de ir para o TTS.
- O orchestrador cria um `pipeline_part` por parte e processa cada uma em sequência. Nada nessa mecânica mudou — o que mudou é quantas partes existem, que no caso normal passou a ser uma.

**O teto é declarado em minutos e traduzido para palavras.** `MAX_PART_WORDS = MAX_PART_MINUTES × NARRATION_WPM` (30 × 195 = 5850). O prompt fala em palavras porque é o que o modelo consegue contar; minutos é o que a regra significa. `NARRATION_WPM = 195` sai de voz neural pt-BR em ~150 wpm acelerada pelo `narration.rate` do template (`+30%`) — é estimativa, e a única decisão que depende dela é o corte em 30 minutos, muito acima do que um roteiro real ocupa.

**O prompt proíbe encurtar para caber.** Sem isso, um modelo que recebe "não divida" e um roteiro longo resolve resumindo — trocaria a divisão indesejada por uma perda de conteúdo pior e invisível, porque o resultado é um `parts` de tamanho 1 com a história mutilada.

**Por que não há guarda determinística.** Reunir partes que o modelo devolveu contra a regra exigiria remover os "Na parte anterior..." e os CTAs de meio de história que ele escreveu para o corte — reescrita de texto, não validação. A obediência é observável em `parts_count` e `split_rationale`, que já ficam no DB de todo run.

---

## Agendamento de séries

Quando há mais de uma parte, elas saem **encadeadas**: a parte N é agendada `series_gap_minutes` (30) depois do horário já agendado da parte N-1. Só a parte 1 disputa `preferred_times` / `posts_per_day`.

```
_schedule (orchestrator)          POST /schedule (tiktok_poster)
  parte 1 → follows_at ausente  → next_available_slot()   → 20:00
  parte 2 → follows_at = 20:00  → continuation_slot()     → 20:30
  parte 3 → follows_at = 20:30  → continuation_slot()     → 21:00
```

**Por que a continuação ignora os horários preferidos.** `preferred_times` e `posts_per_day` existem para espaçar histórias independentes ao longo do dia. Uma história dividida não são N posts: é uma história continuada, e submetê-la a esse ritmo jogava a parte 2 para o dia seguinte — que era exatamente o comportamento anterior ("cada parte em um dia consecutivo"). Com o corte agora só acontecendo acima de 30 minutos, a divisão é rara e sempre significa "a história não acabou": 30 minutos é curto o bastante para o espectador ainda estar por perto.

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

Definida no `template.json`, no bloco `narration.rate` (padrão `+30%`), e aplicada pelo `tts_service`. No provider `edge` vai para `edge_tts.Communicate(..., rate=...)`; no `azure`, para o `<prosody rate='...'>` do SSML. É o mesmo parâmetro nos dois casos, então trocar de provider não muda o ritmo da narração.

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

**Precedência:** `narration.rate` do template → `TTS_RATE` do `tts_service` → `+30%`. O env var deixa de ser a fonte primária e vira fallback: cobre templates sem o bloco `narration` (compatibilidade) e chamadas diretas ao `tts_service` fora do pipeline. Os dois são mantidos **no mesmo valor** — divergi-los faria o caminho de fallback narrar num ritmo diferente do resto do canal, e a diferença só apareceria no vídeo pronto.

**Degradação.** Falha ao ler o config — template sem bloco, blender_worker fora do ar, JSON inválido — cai no `TTS_RATE` com warning, sem derrubar o run. Narração é estética; o render, não. Mesmo critério da remoção de silêncio (degrada) versus a transcrição (derruba).

**Validação em um lugar só.** A regex `^[+-]\d+%$` mora no `tts_service` (`validate_rate`), usada tanto no boot (env var) quanto no campo da request (`422`). O orchestrador repassa o valor sem validar — duplicar a regra criaria duas fontes de verdade que divergem com o tempo.

**Por que no motor de voz, e não em pós-processamento.** Acelerar o MP3 depois de pronto (resample no `pydub`/ffmpeg) sobe o pitch junto e a voz vira "esquilo"; corrigir isso exige time-stretch, que introduz artefato. O `rate` do edge-tts é `prosody rate` do SSML — a Microsoft sintetiza já no ritmo pedido, com o pitch intacto e sem perda de qualidade. Custo zero: não há etapa de áudio extra no pipeline.

**Formato.** Percentual com sinal obrigatório (`+15%`, `-10%`, `+0%` desliga). O edge-tts só rejeitaria o formato na hora de sintetizar, o que transformaria um erro de config em falha de request no meio do pipeline — daí a validação antecipada nos dois pontos de entrada.

**Ordem no pipeline.** O `rate` age na síntese, antes de tudo. Logo a remoção de silêncio e a transcrição já operam sobre o áudio acelerado, e o SRT sai com o timing certo sem nenhum ajuste — mesma razão pela qual a transcrição roda depois do corte de silêncio (ver "Legendas"). Nada no `blender_worker` muda: ele consome o par MP3+SRT como sempre.

**Efeito na divisão em partes.** O limite é de fala, não de texto, e narração mais rápida encurta o áudio para o mesmo roteiro. O LLM decide o corte a partir do texto, sem conhecer o `rate`: `NARRATION_WPM` já embute o `+30%` do template publicado, então mudar `narration.rate` sem mexer nessa constante desloca o teto real de 30 minutos — as duas andam juntas (a subida de `+15%` para `+30%` levou a constante de 170 para 195). A deriva é irrelevante no uso normal — com o scout ingerindo até 6000 caracteres (~1000 palavras), nenhum roteiro chega perto das 5850 palavras do teto, e o `parts` de tamanho 1 é o resultado independentemente do rate.

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

O `edge` continua registrado como fallback sem-configuração — útil em dev e quando não há key. Não é mais o padrão de produção.

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

`pick_background(keys, run_id, part_number)` escolhe o clipe de cada parte entre os objetos publicados sob `[template] background_prefix`.

**Determinístico, não aleatório.** Duas razões: uma parte re-renderizada depois de um restart tem que voltar com a mesma imagem, senão o retry produz silenciosamente um vídeo diferente do que já foi revisado; e a semente inclui o número da parte, então as partes de uma mesma série — publicadas em sequência, onde a repetição seria mais visível — caem em clipes diferentes. A semente é `sha256(run_id:part)`, não `hash()`, que é salgado por processo e mudaria a cada restart.

**Fallback preservado.** Prefixo vazio ou sem objetos cai no `background_video_key` único de antes. Uma biblioteca não preenchida degrada para o comportamento antigo em vez de falhar na última etapa.

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

O prefixo `assets/music/` já é o formato de biblioteca dos fundos, então acrescentar faixas é subir arquivo; o rodízio entre elas ainda não está ligado (com uma faixa só seria no-op).

**A trilha real expôs dois problemas. O primeiro está resolvido:**

- ~~**O fade começa cedo demais.**~~ **Resolvido.** O fade passou a ser contado a partir do fim (`music.fade_out_seconds`, 1,5s), e `timing.music_fade_out` não é mais lido. Ver "O vídeo não tem finalização" — inclusive a medição de quanto a trilha estava sendo perdida.
- **Só o primeiro minuto da faixa é ouvido.** O strip começa sempre no 0:00 do arquivo, então uma mix de 34 minutos rende sempre o mesmo trecho, e os 32 MB são baixados a cada render. Alternativas: cortar um trecho curto, ou dar um deslocamento determinístico de entrada por vídeo (`frame_offset_start`), no mesmo espírito do rodízio de fundos.

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

- Ritmo: 2 posts por dia.
- Séries: partes são agendadas em dias consecutivos, mesmo horário.
- O `tiktok_poster` é responsável por:
  - Escolher hashtags finais (com base na classificação + performance histórica)
  - Postar no horário agendado via TikTok API
  - Coletar métricas (views, likes, shares, watch time) após publicação
  - Armazenar métricas no próprio DB para informar decisões futuras de hashtag e horário
- O orchestrador delega completamente — só recebe confirmação de `scheduled` e `posted`.

---

## Trigger (entrada)

Duas portas de entrada, ambas terminando no mesmo `POST /pipeline`:

1. **Manual** — `POST /pipeline` no orchestrador com `{ "script": "...", "metadata": {} }`.
2. **Automática** — o `content_scout` descobre roteiros sozinho e chama o mesmo endpoint.

O trigger sempre normaliza para `plain text + metadata` antes de enviar ao orchestrador, independente da origem.

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

Dentro de cada origem, a ordem deixou de ser só a posição no feed: o ranking interno é a nota de qualidade narrativa descrita abaixo, com a posição do feed como critério de desempate. O rodízio entre origens é anterior e independente — ele decide *de quem* é a vez, a nota decide *qual* história daquela origem.

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

**A tag é derivada, não pedida ao modelo.** O modelo devolve nota; a linha entre fraco e forte é config (`min_story_score`, padrão 6 — a régua do prompt põe post comum de fórum em 4–6). Assim o corte se move contra dados reais via `GET /scout/seen?story_tag=weak_storytelling`, do mesmo jeito que `min_chars`/`max_chars` moram em config. `story_score` fica gravado cru, então mover o corte permite re-derivar as linhas antigas.

**`story_tag IS NULL` ≠ fraco.** Nulo significa não avaliado: o candidato barrado pelos filtros baratos (a nota roda depois deles, e depois do backpressure — fila cheia não publica, então não deve pagar julgamento) e todo candidato de um ciclo em que o `llm_service` caiu. **Filtrado não implica nulo**: quem caiu no teto de tamanho foi julgado antes de cair, e tem as colunas preenchidas. Um candidato sem nota ordena **no próprio limiar**, não no fim da fila: manda-lo para o fim converteria uma falha de modelo em handicap permanente para uma história que ninguém julgou, e são justamente as sobras de cada ciclo que herdariam esse handicap.

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

**O teto continua sendo o gate de produção.** Subi-lo é o que transforma essas linhas em vídeo, e o refino já sabe lidar com o resultado: o padrão é uma parte só, e acima de `MAX_PART_WORDS` (5850, ~30 min de fala) ele divide em partes com cliffhanger. Não há nada abaixo do scout que quebre com roteiro longo — `raw_script` e `script` são `Text` sem limite.

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

**O que continua sendo responsabilidade de fora do compose:**

- O Docker Desktop no Windows exige sessão de usuário logada; um daemon Linux (VM ou WSL como serviço) é o alvo certo para 24h.
- Espaço em disco: as imagens somam ~36 GB e o build cache cresce sem limite (`docker builder prune`).
- Backup do Postgres e retenção dos `outputs/` no R2 — nada é apagado hoje.
- Alerta de falha: um run `failed` não notifica ninguém.

**O plano de deploy está em [`deploy.md`](deploy.md)** — máquina alvo, orçamento de RAM, os ajustes a aplicar antes de subir (o principal: `max_pending_runs = 5` permite 5 renders Blender simultâneos, o que não cabe em 8 GB) e o desenho do monitoramento em quatro camadas com notificação por WhatsApp. Nada daquele documento foi aplicado ainda.

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

## Decisões em Aberto

- **Schema de classificação por tipo de conteúdo**: o LLM recebe um schema fixo ou gera livremente e o orchestrador valida? → Definir quando implementar o llm_service.
- **TikTok API**: autenticação OAuth vs. token estático de longa duração → Definir quando implementar o tiktok_poster.
- **Dashboard**: servido pelo orchestrador (FastAPI + Jinja2) ou container Next.js separado → MVP usa Jinja2, pode migrar depois.
- **Retry automático**: se TTS ou render falhar, o orchestrador retenta automaticamente ou só marca como `failed`? → MVP marca como failed. O caso de fila cheia do Buffer é diferente (falha temporária, não erro) e já tem solução desenhada em "Fila de espera quando o Buffer está cheio", no Backlog de Features.
