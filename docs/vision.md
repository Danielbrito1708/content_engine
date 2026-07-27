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
  │     └─ melhora ganchos, CTAs, fluxo narrativo
  │     └─ classifica: público-alvo, tom, tipo de conteúdo
  │     └─ decide se divide em partes (e onde cortar com cliffhanger)
  │     └─ gera resumo das partes anteriores (para parte 2+)
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
        └─ agenda 2 posts por dia
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

---

## Divisão em Partes

- O LLM recebe o roteiro e decide se ele cabe em um único vídeo (≤ ~60s de fala) ou precisa ser dividido.
- Se dividido, o LLM escolhe o ponto de corte que maximize a curiosidade (cliffhanger natural).
- Para partes 2+, o LLM gera um resumo curto ("Na parte anterior...") que é inserido no início do roteiro daquela parte antes de ir para o TTS.
- O orchestrador cria um `pipeline_part` por parte e processa cada uma em sequência.

---

## Velocidade da Narração

O `tts_service` acelera a narração via `TTS_RATE` (padrão `+15%`). No provider `edge` vai para `edge_tts.Communicate(..., rate=...)`; no `azure`, para o `<prosody rate='...'>` do SSML. É o mesmo parâmetro nos dois casos, então trocar de provider não muda o ritmo da narração.

**Por que no motor de voz, e não em pós-processamento.** Acelerar o MP3 depois de pronto (resample no `pydub`/ffmpeg) sobe o pitch junto e a voz vira "esquilo"; corrigir isso exige time-stretch, que introduz artefato. O `rate` do edge-tts é `prosody rate` do SSML — a Microsoft sintetiza já no ritmo pedido, com o pitch intacto e sem perda de qualidade. Custo zero: não há etapa de áudio extra no pipeline.

**Formato.** Percentual com sinal obrigatório (`+15%`, `-10%`, `+0%`). Validado no boot em `TTSEnvSettings._check_rate_format` — o edge-tts só rejeitaria o formato na hora de sintetizar, o que transformaria um erro de config em falha de request no meio do pipeline.

**Ordem no pipeline.** O `rate` age na síntese, antes de tudo. Logo a remoção de silêncio e a transcrição já operam sobre o áudio acelerado, e o SRT sai com o timing certo sem nenhum ajuste — mesma razão pela qual a transcrição roda depois do corte de silêncio (ver "Legendas"). Nada no `blender_worker` muda: ele consome o par MP3+SRT como sempre.

**Efeito na divisão em partes.** O limite de ~60s é de fala, não de texto, e narração mais rápida encurta o áudio para o mesmo roteiro. Mudar `TTS_RATE` muda de fato quantos roteiros cabem em um vídeo só. O LLM decide o corte a partir do texto, sem conhecer o `rate` — a estimativa dele fica conservadora quando o rate é positivo (divide roteiros que caberiam inteiros), o que é o lado seguro do erro. Deriva relevante só com valores agressivos (`> +30%`).

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

- **Futura Bold como padrão, com fallback em cadeia** — `resolve_font_path()` testa, em ordem: `subtitles.font_path` do template → `assets/fonts/Futura-Bold.ttf` → `.otf` → `DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`, já na imagem). Se nada existir, retorna `None` e a strip fica com a fonte embutida do Blender. Decisão: fonte ausente é problema de estilo, não motivo para falhar um render que já consumiu LLM, TTS e transcrição — degrada o visual, nunca o job.
- **A fonte mora em `blender_worker/assets/fonts/`, não na raiz do monorepo** — o compose usa `build: ./blender_worker`, então o build context da imagem é o diretório do serviço. Um `.ttf` na raiz do monorepo é invisível para o `COPY . .` do Dockerfile e o container renderizaria em DejaVu sem nenhum erro visível. O nome do arquivo é case-sensitive no Linux (`Futura-Bold.ttf`).
- **Binários marcados no `.gitattributes`** — o repo é desenvolvido no Windows com `core.autocrlf=true`. Sem regra explícita, o git decide por heurística de conteúdo se converte newlines; um `.ttf` ou `.blend` convertido quebra em tempo de render, não em tempo de commit.
- **Datablock carregado uma vez** — `bpy.data.fonts.load(..., check_existing=True)` fora do loop. Um vídeo tem centenas de strips word-level; carregar por strip criaria centenas de datablocks duplicados no `.blend`.
- **Branco com contorno preto** — o fundo é vídeo em movimento, então não há cor de texto que funcione sozinha: texto branco desaparece em cena clara. O contorno resolve isso sem tarja/caixa atrás do texto, que roubaria área da tela num formato vertical.
- **`outline_width` padrão 0.24, não 0.05** — 0.05 é o padrão do Blender e renderiza como um fio de cabelo que some sobre fundo claro. 0.24 é o limite superior que ainda preserva as formas das letras: acima de ~0.30 os contornos se fundem entre glifos vizinhos e as contraformas das letras redondas começam a fechar, o que custa legibilidade na velocidade de uma palavra por vez. O valor é clampado em 0..1 na leitura do template (o Blender clampa em silêncio; clampar aqui evita que um valor errado renderize como outra coisa).
- **`font_size` não tem padrão no código** — quando ausente, o tamanho que o Blender deu à strip (60) é preservado, e 60 é pequeno demais para 1080×1920. O `template.json` define 140, que ocupa a largura útil sem encostar nas bordas. A decisão fica no template e não no código porque corpo é escolha de design por template, não invariante do pipeline.
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

**O caso espelhado é deliberadamente não tratado.** Um bed *mais curto* que a narração deixa o final preto. Encurtar a timeline até o fundo cortaria narração no meio da frase — o defeito está no asset e é lá que se corrige.

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

### Sinal de qualidade

O feed RSS **não carrega score**. Por isso pedimos `/r/{sub}/top/.rss?t=week`: a ordenação é feita pelo próprio Reddit e chega implícita na posição das entradas. É um sinal mais fraco que o upvote numérico, mas suficiente — o gargalo real é a fila de publicação, não a escassez de candidatos.

### Rate limit

Leitura não autenticada é limitada a aproximadamente **uma requisição por 40-60s** — a resposta traz `x-ratelimit-remaining: 0` e `x-ratelimit-reset: ~40` já na primeira chamada. Requisições em sequência fazem só o primeiro subreddit responder 200; o resto toma 429. Daí o espaçamento obrigatório (`request_delay_seconds`, padrão 60s — 45s ainda tomou 429 em teste real, a janela desliza). Cada subreddit extra custa uma janela por ciclo, então a lista deve conter só subs que rendem.

O limite é **por cliente, não por endpoint**: um feed de comentários pedido logo depois de um feed de listagem toma 429 com `x-ratelimit-used: 1`, porque a listagem já gastou a janela. Todo tipo de requisição divide o mesmo orçamento, e por isso o espaçamento é centralizado num throttle único dentro de `RedditSource`.

### Seleção entre candidatos

As fontes são buscadas e concatenadas na ordem do config. Pegar o começo dessa lista dava **todas** as vagas ao primeiro subreddit: medido ao vivo, as duas submissões vieram de `r/desabafos` enquanto `r/relacionamentos` contribuiu cinco candidatos e não ganhou nenhuma. Configurar mais subreddits era decorativo.

A seleção é por **rodízio entre origens** (`interleave_by_origin`), preservando o ranking interno de cada uma:

```
desabafos[0], relacionamentos[0], conselhos[0], desabafos[1], ...
```

Isso mantém o ranking do Reddit como critério — continuamos pegando o melhor *disponível* de cada — e garante variedade de origem e tom entre vídeos consecutivos. Combinado com o dedup, o rodízio entre ciclos emerge sozinho, sem estado de rotação persistido.

**Não há score composto** (posição × tamanho × recência). Sem upvotes reais, qualquer peso seria inventado. Quando `seen_items` acumular histórico de performance, dá para ranquear com base em evidência.

### Filtros

Duas etapas, separadas de propósito por custo e por natureza:

**1. Determinística (`filters.py`).** Tamanho nas duas pontas: curto demais não sustenta um vídeo; longo demais obrigaria o LLM a cortar tanto que o que vai ao ar já não é o post. Roda sobre todo candidato, é grátis.

**2. Moderação por LLM (`llm_service POST /moderate`).** Decide se publicar coloca a conta em risco de remoção.

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

O metadata enviado em `POST /pipeline` ganha dois campos opcionais, ambos vindos do scout:

| Campo | Origem | Quando está presente |
|---|---|---|
| `author` | `<author><name>` do feed | Sempre que a fonte expõe autor |
| `comment_count` | contagem de `t1_` no feed do post | Só quando o enriquecimento rodou e teve sucesso |

São aditivos e opcionais — o orchestrador e o `llm_service` seguem funcionando sem eles, e submissões manuais nunca os terão.

### Dedup e auditoria

`seen_items` guarda **todo** candidato avaliado — inclusive os rejeitados, com o motivo. Serve a dois propósitos: impedir que a mesma história vire um segundo vídeo quando reaparece no top da semana seguinte, e permitir calibrar os limiares contra dados reais em vez de chute.

Além do veredito, a linha guarda `author` e — para os enriquecidos — `comment_count` e as respostas em `item_comments`. Com o tempo isso vira a base para ranquear por evidência: repercussão no Reddit contra performance real no TikTok.

### Backpressure

Antes de submeter, o scout conta os runs ativos no orchestrador (`pending`, `refining`, `refined`, `processing`, `scheduling`). Se atingiu `max_pending_runs`, o ciclo não submete nada.

A razão é a fila do Buffer, que segura 10 posts: ingerir mais rápido do que se publica não gera mais vídeos, só converte roteiro bom em run falho. Enquanto a feature "Fila de espera quando o Buffer está cheio" (ver Backlog) não existir, o backpressure é a única proteção contra isso.

**Ordem das etapas.** A capacidade é verificada *depois* de registrar os filtrados e *antes* de submeter. Assim uma fila cheia não custa nada e não perde nada — o lixo é queimado e o ciclo seguinte parte de uma pilha menor.

### Periodicidade

Loop `asyncio` iniciado no `lifespan` do serviço, intervalo configurável. Não precisa de scheduler durável — diferente do retry do Buffer — porque `seen_items` torna o ciclo idempotente: um restart no pior caso repete uma passagem que não encontra nada novo.

### Adicionando fontes

Toda fonte implementa o Protocol `Source` (`name` + `async fetch() -> list[Candidate]`) e devolve `Candidate` com `external_id` estável, que é a chave de dedup. Dedup, filtros, orçamento e backpressure tratam todas as fontes igualmente.

Comentários são uma **capacidade opcional**, no Protocol separado `CommentCapableSource` (`async fetch_comments(candidate) -> CommentThread | None`). Implementar é opcional: o scout detecta a capacidade e simplesmente não enriquece quem não a tem. `None` significa "não deu para consultar" e é distinto de uma thread vazia, que significa "não teve resposta".

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
blender_worker  → outputs/{job_id}.mp4   (vídeo renderizado)
                → outputs/{job_id}.blend  (cena Blender montada, para inspeção/reuso)
```

O orchestrador armazena as keys MinIO de cada artefato no `pipeline_run` para passá-las para os próximos serviços.

---

## Backlog de Features

Features planejadas, ainda não implementadas. Cada entrada descreve o problema, o comportamento proposto e o que precisa mudar — o suficiente para uma sessão futura implementar sem redescobrir o contexto.

### Fila de espera quando o Buffer está cheio

**Problema.** O Buffer free aceita no máximo 10 posts agendados por vez (`config.ini [posting] buffer_queue_limit`). Quando o limite é atingido, `POST /schedule` no `tiktok_poster` responde `429` com `{"error": "buffer_queue_full"}`, o orchestrador marca o `pipeline_run` como `failed` e o vídeo — já renderizado, já pago em tempo de LLM, TTS e render — fica parado. A recuperação hoje é manual: esperar a fila baixar e resubmeter.

Isso é o único ponto do pipeline onde uma condição **temporária e esperada** produz uma falha terminal. Com 2–3 posts/dia e séries de múltiplas partes, a fila enche em poucos dias de operação normal.

**Comportamento proposto.** Fila cheia deixa de ser falha e passa a ser espera:

- O orchestrador ganha o estado `awaiting_slot` — o run terminou render, tem os vídeos no MinIO, e só aguarda vaga no Buffer. Distinto de `failed`: nada deu errado.
- Um worker periódico varre os runs em `awaiting_slot` (ordem FIFO por `created_at`) e retenta o agendamento. Ao conseguir, o run segue para `scheduled` normalmente.
- Séries são atômicas: só agenda se houver vaga para **todas** as partes restantes. Agendar a parte 1 e deixar a parte 2 na espera publica um cliffhanger sem continuação.
- O intervalo de varredura é configurável; algo na ordem de horas é suficiente — a fila só abre quando o Buffer publica.

**O que muda:**

| Onde | Mudança |
|---|---|
| `orchestrator/db/models.py` | novo valor `awaiting_slot` em `PipelineStatus` + migration |
| `orchestrator/worker.py` | `_schedule` trata `429 buffer_queue_full` como `awaiting_slot`, não `failed` |
| `orchestrator` (novo) | worker de retry periódico varrendo `awaiting_slot` |
| `tiktok_poster/api/routes/schedule.py` | expor vagas livres na resposta do `429`, para o orchestrador decidir sobre séries sem tentativa e erro |
| `docs/product.md` | reescrever "Fila cheia" na Feature 6 — deixa de ser falha |

**Pré-requisito.** O retry periódico exige um agendador que sobreviva a restart do container. Hoje o orchestrador usa `BackgroundTasks`, que não serve — a mesma limitação já registrada como tech debt no `blender_worker`. Resolver os dois juntos (fila real: Celery + Redis, ou APScheduler com store no Postgres).

**Alternativas consideradas.**

- **Buffer pago** — resolve por dinheiro (limite muito maior), não resolve o caso de a fila encher mesmo assim. Vale como mitigação, não como solução.
- **Publicar direto na TikTok Content Posting API** — elimina o Buffer e o limite de fila, mas troca um problema por outro: OAuth, refresh de token, e o agendamento passa a ser responsabilidade nossa. Ver "TikTok API" em Decisões em Aberto. A fila de espera é útil de qualquer forma, porque o limite de ritmo (posts/dia) continua existindo.

**Critério de aceite.** Submeter runs além do limite da fila: nenhum vai para `failed`, todos ficam em `awaiting_slot`, e cada um é agendado sozinho conforme a fila abre — sem resubmissão manual e sem perder a ordem.

---

## Decisões em Aberto

- **Schema de classificação por tipo de conteúdo**: o LLM recebe um schema fixo ou gera livremente e o orchestrador valida? → Definir quando implementar o llm_service.
- **TikTok API**: autenticação OAuth vs. token estático de longa duração → Definir quando implementar o tiktok_poster.
- **Dashboard**: servido pelo orchestrador (FastAPI + Jinja2) ou container Next.js separado → MVP usa Jinja2, pode migrar depois.
- **Retry automático**: se TTS ou render falhar, o orchestrador retenta automaticamente ou só marca como `failed`? → MVP marca como failed. O caso de fila cheia do Buffer é diferente (falha temporária, não erro) e já tem solução desenhada em "Fila de espera quando o Buffer está cheio", no Backlog de Features.
