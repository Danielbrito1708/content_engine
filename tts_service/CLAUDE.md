# CLAUDE.md — tts_service

Serviço de geração de áudio a partir de texto. Expõe `POST /generate` que o orchestrador chama após o refinamento do roteiro. Gera o áudio e faz upload direto ao MinIO.

## Arquitetura

- `api/routes/generate.py` — endpoint principal
- `api/routes/health.py` — `GET /health` com provider, voice, rate e parâmetros de áudio ativos
- `tts/base.py` — `BaseTTSClient` com método abstrato `generate(text) -> bytes`
- `tts/azure.py` — `AzureTTSClient` (Azure Speech REST, **padrão de produção**)
- `tts/edge.py` — `EdgeTTSClient` (edge-tts, Microsoft Neural TTS, gratuito)
- `tts/elevenlabs.py` — stub para implementação futura
- `tts/factory.py` — `get_tts_client(rate, voice)` seleciona o provider via `TTS_PROVIDER` env var
- `tts/voices.py` — mapa gênero do narrador → voz (puro)
- `schemas/generate.py` — `GenerateRequest`, `GenerateResponse`
- `storage/client.py` — `upload_audio(bucket, key, data)` via boto3 + MinIO

## Features

### Endpoint de geração (`src/tts_service/api/routes/generate.py`)

`POST /generate` — gera áudio MP3 e salva no MinIO.

**Request** (`GenerateRequest`):
- `text` (str) — texto a ser narrado
- `run_id` (str) — UUID do pipeline run (usado na key MinIO)
- `part_number` (int, padrão `1`) — número da parte (1, 2, ...)
- `label` (str | None) — nome do arquivo dentro do run, quando o áudio **não** é uma parte do roteiro
- `rate` (str, opcional) — velocidade da narração desta request (`+15%`). Vem do `narration.rate` do `template.json`. Ausente ou `null` → usa `TTS_RATE`. Formato inválido → `422` antes de qualquer chamada ao TTS.
- `narrator_gender` (str, opcional) — `male` / `female` / `unknown`, vindo do refino. Escolhe a voz (ver **Voz do narrador**). Valor irreconhecível vira `unknown`, **não** `422`.

**Response** (`GenerateResponse`):
- `audio_key` (str) — key MinIO do áudio gerado: `audio/{run_id}/{slug}.mp3`
- `srt_key` (str) — key MinIO da legenda word-level: `subs/{run_id}/{slug}.srt`
- `voice` (str) — a voz resolvida por `resolve_voice()` para esta request (ex.:
  `pt-BR-AntonioNeural`). Existe para o orchestrador guardar qual voz narrou cada run e
  repassar ao `tiktok_poster` como variante de teste A/B — ver "Voz do narrador" abaixo e
  `tiktok_poster/CLAUDE.md`. Não é um nome novo: é o mesmo valor que a rota já calculava e
  logava, só que agora também sai na resposta.

`slug` = `label` quando presente, senão `part_{part_number}` — o comportamento antigo, byte a byte, para quem não manda `label`.

**Ordem das etapas:** TTS → pós-processamento (silêncio + loudness) → upload do áudio → transcrição → upload do SRT. A transcrição roda **depois** do pós-processamento, sobre o mesmo áudio que vai ao vídeo — é o que mantém a legenda em sincronia.

Erros: `502` se o TTS, o upload ao MinIO ou a transcrição falharem. `422` se o `label` não casar com o pattern.

### Nome do arquivo (`label`)

Existe porque a frase gancho é narrada sozinha (`_run_hook_tts` no orchestrador) e precisa de key própria: sem `label`, ela sobrescreveria `part_1.mp3`, que é a narração da parte inteira.

`label="hook"` → `audio/{run_id}/hook.mp3` + `subs/{run_id}/hook.srt`.

⚠️ **O pattern `^[a-z0-9][a-z0-9_-]{0,63}$` não é cosmético.** A key é montada por interpolação de string; um label com `/` ou `..` escreveria fora do prefixo do run. Rejeitar no schema é mais barato que sanitizar depois — 4 testes em `test_label.py` fixam isso.

### Providers TTS (`src/tts_service/tts/`)

Selecionado por `TTS_PROVIDER` env var:

| Provider | Env var | Qualidade | Status |
|---|---|---|---|
| `azure` (recomendado) | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | 48 kHz / 192 kbps | Implementado |
| `edge` (padrão do código) | `TTS_VOICE` | 24 kHz / 48 kbps (fixo) | Implementado |
| `elevenlabs` | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | 44.1 kHz | Stub (NotImplementedError) |

**Vozes PT-BR** (as mesmas nos providers `edge` e `azure`):
- `pt-BR-ThalitaNeural` (feminina, jovem — o `TTS_VOICE` padrão)
- `pt-BR-FranciscaNeural` (feminina — voz do narrador `female`)
- `pt-BR-AntonioNeural` (masculino — voz do narrador `male`)

Qual delas narra é decidido por request, pelo gênero do narrador (ver abaixo).

### Voz do narrador (`src/tts_service/tts/voices.py`)

As histórias são contadas em primeira pessoa, então o narrador tem gênero e a voz precisa concordar com ele: história de homem lida por voz feminina é a primeira coisa que o espectador nota, e nenhum trabalho de ritmo ou loudness recupera isso. Antes, toda narração saía em `TTS_VOICE`.

**API pública (pura — as três vozes são injetadas, porque `settings.env` é frozen e o teste não alcança o env):**
- `normalize_gender(value) -> str` — qualquer entrada para `male` / `female` / `unknown`
- `resolve_voice(gender, *, default, male, female) -> str` — a voz do narrador; `default` quando o gênero não é conhecido
- `GENDERS`, `MALE`, `FEMALE`, `UNKNOWN`

| Var | Padrão | Descrição |
|---|---|---|
| `TTS_VOICE` | `pt-BR-ThalitaNeural` | Narrador `unknown` ou request sem o campo |
| `TTS_VOICE_MALE` | `pt-BR-AntonioNeural` | Narrador `male` |
| `TTS_VOICE_FEMALE` | `pt-BR-FranciscaNeural` | Narrador `female` |

Defaults em `DEFAULT_MALE_VOICE` / `DEFAULT_FEMALE_VOICE` (`src/core/config.py`, junto dos outros defaults de env). O `/health` reporta as três.

⚠️ **O mapa gênero→voz mora aqui, não no orchestrador.** O que chega é um fato sobre o roteiro (`narrator_gender`), nunca um nome de voz: `edge` e `azure` servem as mesmas vozes neurais, então `male` é uma string só para os dois, e um provider novo mexe neste arquivo sozinho.

⚠️ **Normaliza em vez de rejeitar.** O valor nasce numa classificação de LLM dois serviços acima; o pior caso de errar é a voz que todo vídeo usava antes disso existir. Um `422` custaria o run inteiro por um campo cosmético. A voz resolvida e o gênero recebido vão no log da rota, então um modelo que comece a responder `"masculino"` fica visível sem ser fatal.

`get_tts_client(rate=..., voice=...)` repassa ao provider ativo — `EdgeTTSClient` e `AzureTTSClient` já aceitavam `voice=` e caem em `settings.env.tts_voice` sem argumento. **Um provider novo precisa aceitar `voice` no construtor**, senão a voz do narrador é silenciosamente ignorada ao trocar de provider (mesma armadilha do `rate`; o stub `elevenlabs` está nessa situação — o equivalente lá é `ELEVENLABS_VOICE_ID`).

### Provider Azure (`src/tts_service/tts/azure.py`)

Existe porque o `edge-tts` tem o formato de saída **hardcoded** em `audio-24khz-48kbitrate-mono-mp3` — é constante na lib, não parâmetro. A 24 kHz, nada acima de ~12 kHz existe no sinal, e era isso que deixava a narração abafada. O Azure serve as mesmas vozes neurais com o formato escolhido pelo cliente.

`POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1` com corpo SSML.

| Var | Padrão | Descrição |
|---|---|---|
| `AZURE_SPEECH_KEY` | — | **Obrigatória** quando `TTS_PROVIDER=azure` |
| `AZURE_SPEECH_REGION` | — | **Obrigatória**; slug do recurso (`brazilsouth`, `eastus`, ...) |
| `AZURE_OUTPUT_FORMAT` | `audio-48khz-192kbitrate-mono-mp3` | Qualquer formato aceito pelo endpoint |

Key/region ausentes **derrubam o boot** em `TTSEnvSettings._check_provider_key` — mesma razão do `TTS_RATE`.

**API pública do módulo:**
- `voice_locale(voice) -> str` — `'pt-BR-ThalitaNeural'` → `'pt-BR'`; fallback `pt-BR` para formatos inesperados
- `build_ssml(text, voice, rate) -> str` — passa o texto por `xml.sax.saxutils.escape`. Roteiro com `&` ou `<` geraria XML malformado e `400` do Azure
- `AzureTTSClient(key, region, voice, rate, output_format, timeout)` — todos os args opcionais, caem no `settings.env`

Reaproveita `TTS_VOICE` e `TTS_RATE`, então trocar `edge` ↔ `azure` não muda voz nem ritmo. Erro HTTP ou corpo vazio viram `RuntimeError` → `502` na rota.

### Velocidade da narração (`TTS_RATE`)

Acelera (ou desacelera) a narração na **própria síntese** — `rate` do `edge_tts.Communicate` no provider `edge`, `<prosody rate='...'>` no `azure`. É o `prosody rate` do SSML nos dois casos. O pitch fica intacto, diferente de acelerar o MP3 depois (resample deixa a voz aguda).

| Var | Padrão | Descrição |
|---|---|---|
| `TTS_RATE` | `+50%` | Percentual **com sinal** sobre o ritmo natural da voz. Fallback do `narration.rate` do template — os dois ficam no mesmo valor |

**Precedência:** `rate` da request (vem do `narration.rate` do `template.json`) → `TTS_RATE` → `+30%`. O env var é o fallback de quem chama o serviço direto ou de templates sem o bloco `narration`, e é mantido igual ao rate do template publicado — divergir os dois faz o fallback narrar num ritmo diferente do resto do canal.

Formato obrigatório: `^[+-]\d+%$` (`+15%`, `-10%`, `+0%` para desligar), validado por `validate_rate()` em `src/core/config.py` — mesma função para o env var (derruba o **boot**) e para o campo da request (devolve **422**). Em ambos os casos o erro aparece antes de qualquer síntese; o edge-tts só rejeitaria o formato na hora de gerar o áudio.

`get_tts_client(rate=...)` repassa ao provider ativo — `EdgeTTSClient` e `AzureTTSClient` aceitam `rate=` e caem em `settings.env.tts_rate` sem argumento. Um provider novo precisa aceitar `rate` no construtor, senão o `narration.rate` do template é silenciosamente ignorado ao trocar de provider.

Como o rate age antes de tudo, a remoção de silêncio e a transcrição já rodam sobre o áudio acelerado — **o SRT sai sincronizado sem nenhum ajuste** e o `blender_worker` não muda.

O `elevenlabs.py` (stub) ainda não implementa rate; quando for implementado, o equivalente é o parâmetro `speed` do voice settings.

### Pós-processamento de áudio (`src/tts_service/audio/postprocess.py`)

Substituiu `audio/silence.py`. Corta silêncios **e** normaliza loudness numa **única passada de ffmpeg**, antes do upload ao MinIO. Uma passada só porque cada round-trip MP3→MP3 é outra geração lossy.

**Funções públicas:**
- `build_filter_chain(*, trim_silence, max_pause_ms, silence_thresh_db, normalize, loudness_target_lufs, sample_rate) -> list[str]` — os filtros em ordem; lista vazia = nada a fazer
- `probe_source(path) -> tuple[int, str]` — `(sample_rate, bitrate)` da entrada via `ffprobe`. Entrada ilegível cai no fallback `(48000, "192k")` em vez de derrubar o pipeline
- `process_audio(audio_bytes, *, trim_silence, max_pause_ms, silence_thresh_db, normalize, loudness_target_lufs, bitrate, sample_rate) -> bytes` — MP3 em bytes → MP3 em bytes. Puro. **Devolve o input intacto quando não há filtro**, em vez de re-encodar por nada. `bitrate` e `sample_rate` em `None` (padrão) = **casar com a fonte**.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `REMOVE_SILENCE` | `true` | Habilita o corte de silêncio |
| `SILENCE_THRESH_DB` | `-40` | Nível abaixo do qual é considerado silêncio |
| `MAX_PAUSE_MS` | `200` | **Teto** de cada pausa; pausas menores passam intactas (alias depreciado: `MIN_SILENCE_MS`) |
| `NORMALIZE_AUDIO` | `true` | Habilita highpass + loudnorm |
| `LOUDNESS_TARGET_LUFS` | `-16` | Alvo de loudness integrada (referência das plataformas de vídeo) |
| `AUDIO_BITRATE` | *(vazio = casa com a fonte)* | Força o bitrate do MP3 de saída |
| `AUDIO_SAMPLE_RATE` | *(vazio = casa com a fonte)* | Força o sample rate do MP3 de saída |

⚠️ **Não force esses dois para cima do que o provider entrega.** Reamostrar não adiciona banda. Medido no provider `edge` (fonte 24 kHz / 48 kbps): forçar 48 kHz / 192 kbps gerou um arquivo **4× maior** (270 KB vs 68 KB) com a mesma loudness (−16,5 vs −16,6 LUFS) e o mesmo espectro vazio acima de 13 kHz. O `/health` reporta `"source"` quando não há override.

`SILENCE_PADDING_MS` foi removido — nunca foi consumido pela implementação.

⚠️ **`MAX_PAUSE_MS` é um teto, não um gatilho.** O `silenceremove` copia o áudio até que `stop_duration` de silêncio já tenha passado e só então para, então o número é ao mesmo tempo o limiar de detecção **e o silêncio que fica para trás**. Pausa menor que o teto passa intacta; toda pausa maior — 600ms ou 6s — sai em exatamente `MAX_PAUSE_MS`. Medido: com o antigo default de 500, pausas de 1,5s e 3,0s terminavam ambas em 0,52s, e era isso que deixava a narração esburacada. `stop_silence` **não** é usado: ele soma ao que é preservado (medido, `0.1` deixou 0,62s), então só alonga pausas. Seis testes em `test_postprocess.py` fixam isso.

**Três armadilhas que a implementação evita (todas cobertas por teste):**
1. **`aresample` depois do `loudnorm` é obrigatório** — em single-pass o `loudnorm` emite 192 kHz independentemente da entrada. Sem o resample explícito o arquivo sai gigante sem ganho.
2. **Trim antes de loudnorm** — o `loudnorm` mede o stream inteiro; silêncio de borda puxa a medição para baixo e o filtro compensa deixando a voz alta demais.
3. **`-b:a` explícito** — sem ele o ffmpeg escolhe um default do `libmp3lame` a partir do sample rate. Medido: **64 kbps** para 48 kHz mono e **32 kbps** para 24 kHz mono. Um source de 192 kbps seria esmagado a um terço a cada passada.

Falhas no pós-processamento são logadas como warning e o áudio original é usado (sem interromper o pipeline).

**Dependências:** `ffmpeg` (no Dockerfile) — filtros `silenceremove`, `highpass`, `loudnorm`, `aresample` via subprocess.

### CLI de calibragem (`scripts/cut_silence.py`)

Roda `process_audio` num arquivo local, fora do serviço — feito para calibrar os limiares contra um áudio real sem subir o container nem reprocessar um pipeline run. Chama a **mesma** função da rota; não reimplementa a cadeia de filtros.

```bash
poetry run python scripts/cut_silence.py narracao.mp3
poetry run python scripts/cut_silence.py narracao.mp3 --max-pause-ms 150 --thresh-db -35
poetry run python scripts/cut_silence.py parte_1.mp3 parte_2.mp3 --dry-run
```

| Flag | Padrão | Descrição |
|---|---|---|
| `-o/--output` | `<nome>.trimmed.mp3` | Só vale com um único input |
| `--max-pause-ms` | `200` | Teto de cada pausa interna |
| `--thresh-db` | `-40` | Nível considerado silêncio |
| `--no-normalize` | — | Desliga o loudnorm; isola o efeito do corte |
| `-n/--dry-run` | — | Processa e reporta, sem gravar |
| `-f/--force` | — | Permite sobrescrever a saída |

A normalização fica **ligada por padrão**, igual à produção — o que sai do CLI é o que o pipeline produziria.

Reporta `origem: 4.00s -> 2.42s (-1.58s, 39.5%) destino` por arquivo.

**API pública (testável):** `cut_file(source, output, max_pause_ms, silence_thresh_db, normalize=True) -> CutResult` (`output=None` é dry-run), `probe_duration_ms(bytes) -> int` (0 quando o ffprobe não lê), `default_output_for(path)`, `main(argv) -> int`.

Saída sempre MP3, qualquer que seja o formato de entrada — o encoder vem da extensão que `process_audio` usa no temp interno.

Códigos de saída: `0` ok, `1` falha em pelo menos um arquivo (ou ffmpeg ausente, que aborta imediatamente), `2` erro de uso (input inexistente, `-o` com múltiplos inputs).

⚠️ **O relatório é ASCII de propósito** (`->`, não `→`). O console do Windows é cp1252 e `print` com seta Unicode levanta `UnicodeEncodeError` em execução real — os testes não pegam isso sozinhos porque o `capsys` captura em UTF-8; `test_main_reports_durations` faz um `.encode("cp1252")` na saída justamente para cobrir o buraco.

**Dependências:** `ffmpeg` e `ffprobe` no PATH.

### Transcrição / legenda word-level (`src/tts_service/audio/transcribe.py`)

Transcreve o áudio final (já sem silêncios) e gera o SRT que o `blender_worker` usa como legenda. Substituiu o SRT com timing estimado que o orchestrador gerava a partir do roteiro.

**Função pública:**
- `transcribe_to_srt(audio_bytes, language="pt", model_name="base") -> bytes` — recebe MP3 em bytes, devolve SRT em bytes com **uma entrada por palavra**.

Usa `faster-whisper` com `word_timestamps=True` (device `cpu`, `compute_type="int8"`). O modelo é carregado uma vez e reaproveitado num cache **por nome** (`_models`) — a primeira request paga o download/load.

⚠️ **O cache era um slot só, e passou a não poder ser.** Com `POST /transcribe` usando um modelo diferente do da legenda, um cache de uma variável devolveria silenciosamente o modelo do primeiro chamador ao segundo: legenda de render rodando no modelo grande, ou transcrição de vídeo rodando no `base`. Nenhum dos dois falha de forma visível — só fica pior.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `WHISPER_MODEL` | `base` | Tamanho do modelo (`tiny`, `base`, `small`, `medium`, ...) |
| `WHISPER_LANGUAGE` | `pt` | Idioma da transcrição |

Modelos maiores alinham as palavras melhor, ao custo de CPU. Falha na transcrição **derruba a request** com `502` (diferente da remoção de silêncio, que degrada silenciosamente).

O consumo dessa legenda (offset de sincronia, hold entre palavras, fades) é responsabilidade do `blender_worker` — ver `docs/vision.md` na raiz do monorepo.

**Sem testes** — `transcribe.py` não tem cobertura (exigiria mockar `WhisperModel` ou fixture de áudio real).

### Transcrição de vídeo de terceiro (`POST /transcribe`)

`audio/download.py` + `api/routes/transcribe.py`. URL de vídeo → texto corrido, para o roteiro
de um vídeo que já viralizou entrar no pipeline **como matéria-prima do refino**, nunca como
roteiro final (a regra e o porquê estão em `docs/vision.md` → "Roteiro viral entra como
matéria-prima"). Consumido pelo `content_scout`; ver a caixa de entrada lá.

**Por que mora aqui.** O serviço já tem as duas peças caras na imagem — ffmpeg e faster-whisper.
Um serviço novo duplicaria as duas, e o `content_scout`, que é quem consome, não tem nenhuma.

**API pública:**
- `POST /transcribe` `{url}` → `{text, char_count, audio_duration, source{...}}`
- `download_audio(url, dest, *, impersonate, api_hostname, sleep_requests, retries, max_duration_seconds) -> DownloadedMedia`
- `transcribe_file_to_text(audio_path, language, model_name) -> (texto, duração)`

**O `source` não é enfeite.** Ele carrega `view_count` e `like_count`, que são o único sinal de
**retenção real** que o pipeline recebe: o upvote do Reddit diz quantos votaram, a visualização
diz que a história prendeu. É a razão de existir deste caminho, e viaja até o `metadata` do run.

⚠️ **O TikTok se defende, e são três obstáculos distintos** (medidos em 27/08/2026):

| Obstáculo | Sintoma | Solução |
|---|---|---|
| Fingerprint de TLS | HTTP **200** com casca de ~1,4 KB; o yt-dlp reporta "Unexpected response from webpage request" | `impersonate` + `curl_cffi` |
| Desafio JS | — | o yt-dlp resolve sozinho, sem navegador |
| Parser da página falhando | "Unable to extract universal data for rehydration" | `api_hostname` (API mobile), **só como 2ª tentativa** |
| Rate limit por IP | URLs seguidas derrubam até a que funcionou | `sleep_requests` |

⚠️ **`curl_cffi` é dependência funcional, não opcional.** Sem ela o yt-dlp aceita `impersonate` e a
**ignora**, e o sintoma reaparece como erro de extractor. Por isso `_impersonate_target()` levanta
`DownloadError` explicando, em vez de deixar falhar mais tarde e mais longe da causa.

⚠️ **`api_hostname` nunca é a primeira tentativa.** Resolve o que a página web recusa, mas é o
caminho mais sujeito a mudar sem aviso.

⚠️ **O teto de duração corta antes de transcrever**, que é onde o custo está (~0.4x tempo real de
CPU). Um vídeo longo mandado por engano queimaria minutos antes de alguém notar.

⚠️ **`502` e não `400` quando o download falha**: a URL está bem formada, quem recusou foi a
plataforma. A distinção é lida pelo `content_scout` para decidir se avisa "tente de novo mais
tarde" — a resposta ao rate limit é reenviar, a resposta a URL inválida não é.

⚠️ **Transcrição vazia é `422`, não `200`.** Áudio sem fala é um resultado, mas devolver texto
vazio faria o chamador criar um run de roteiro em branco, que só morreria depois de TTS e render.

**Comportamento em `config.ini [download]`** (`impersonate`, `api_hostname`, `sleep_requests`,
`retries`, `max_duration_seconds`); modelo em `WHISPER_TRANSCRIBE_MODEL` (padrão `small`).

## Testes

`tests/test_generate.py` — 12 testes; edge-tts e MinIO sempre mockados; `REMOVE_SILENCE=false` **e `NORMALIZE_AUDIO=false`** no conftest, para que nenhum teste de endpoint chame ffmpeg.

`tests/test_transcribe.py` — 16 testes: a rota (texto + `source`, view count sobrevivendo ao round-trip, 502 do download, 422 de transcrição vazia), o download (impersonation presente, `api_hostname` só na 2ª tentativa, teto de duração, sucesso silencioso sem arquivo, `.part` não confundido com áudio, metadata ausente) e o cache de modelos por nome. Nada toca rede nem whisper: `_extract` é mockado, que é o ponto mais fundo que ainda exercita a lógica.

`tests/test_label.py` — 10 testes do `label`: as duas keys, precedência sobre `part_number`, `part_number` opcional, e a rejeição de `/`, `..`, maiúscula e string vazia. Mesmos mocks de `test_generate.py`.

`tests/test_postprocess.py` — 31 testes (era `test_silence.py`); áudio gerado por `wave` + ffmpeg (`_make_mp3`, com `amplitude` para material quiet/hot; `_make_24khz_mp3` para imitar a saída do `edge`). Cobre a montagem da filter chain, o corte de silêncio, o **teto de pausa** (6 testes, via `_pause_durations_ms` com `silencedetect`), sample rate e bitrate de saída via `ffprobe`, a loudness medida via filtro `ebur128`, o casamento com a fonte (não faz upsample) e 4 testes de integração com o endpoint. Requer `ffmpeg` **e `ffprobe`** instalados.

`tests/test_cut_silence.py` — 22 testes do CLI. O script é carregado por caminho (`scripts/` não é pacote), mesma convenção de `blender_worker/tests/test_subtitles.py`, **mas com `sys.modules[spec.name] = module` antes do `exec_module`** — sem isso o `@dataclass` do `CutResult` levanta `AttributeError` ao resolver o módulo dono. Reaproveita `_make_mp3` de `test_postprocess.py`. Requer `ffmpeg` e `ffprobe`.

`tests/test_azure.py` — 17 testes; `httpx.AsyncClient.post` sempre mockado, **nenhum acessa a rede**. Cobre derivação de locale, escape de XML no SSML, headers/URL/corpo da request, erro HTTP e corpo vazio, e a validação de config no boot (via `TTSEnvSettings()` direto, que relê o env).

⚠️ **Todo teste do endpoint que espera 201 precisa mockar `transcribe_to_srt` e `upload_bytes`** — use `_mock_transcription()` de `test_generate.py`. O `FAKE_MP3` do conftest é header + zeros; o Whisper real não decodifica isso e a rota devolve `502` na etapa de transcrição. Os testes ficaram quebrados exatamente assim quando a transcrição entrou na rota sem que os mocks fossem atualizados.

⚠️ **`settings.env` é um pydantic model frozen construído uma vez no bootstrap.** `monkeypatch.setenv` não alcança o código sob teste, e `setattr` no campo levanta `ValidationError` — para exercitar um branch que depende de env, troque o `settings` do módulo (ver `test_factory_unknown_raises`).

Rodar com Python 3.11 (o do Dockerfile) — anotações são avaliadas no import, então um nome não importado numa assinatura quebra a coleção do arquivo inteiro, coisa que o Python 3.14 local não acusa.

`tests/test_voice.py` — 33 testes da voz por gênero: a normalização, a resolução pura, os defaults de env (via `TTSEnvSettings()` direto), o repasse do `voice` na rota e na factory (edge **e** azure), o SSML do Azure carregando o nome da voz, o gênero irreconhecível que **não** vira `422`, e o `/health`.

`tests/test_rate.py` — 31 testes; `edge_tts.Communicate` mockado. Cobre o repasse do `rate` na síntese, o override no construtor e na factory, a precedência request → env no endpoint, a validação de formato (env via `TTSEnvSettings()` direto, que relê o env; request via `422`) e o campo `rate` no `/health`.

```bash
poetry run pytest
```
