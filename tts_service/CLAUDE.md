# CLAUDE.md — tts_service

Serviço de geração de áudio a partir de texto. Expõe `POST /generate` que o orchestrador chama após o refinamento do roteiro. Gera o áudio e faz upload direto ao MinIO.

## Arquitetura

- `api/routes/generate.py` — endpoint principal
- `api/routes/health.py` — `GET /health` com provider, voice, rate e parâmetros de áudio ativos
- `tts/base.py` — `BaseTTSClient` com método abstrato `generate(text) -> bytes`
- `tts/azure.py` — `AzureTTSClient` (Azure Speech REST, **padrão de produção**)
- `tts/edge.py` — `EdgeTTSClient` (edge-tts, Microsoft Neural TTS, gratuito)
- `tts/elevenlabs.py` — stub para implementação futura
- `tts/factory.py` — `get_tts_client()` seleciona o provider via `TTS_PROVIDER` env var
- `schemas/generate.py` — `GenerateRequest`, `GenerateResponse`
- `storage/client.py` — `upload_audio(bucket, key, data)` via boto3 + MinIO

## Features

### Endpoint de geração (`src/tts_service/api/routes/generate.py`)

`POST /generate` — gera áudio MP3 e salva no MinIO.

**Request** (`GenerateRequest`):
- `text` (str) — texto a ser narrado
- `run_id` (str) — UUID do pipeline run (usado na key MinIO)
- `part_number` (int) — número da parte (1, 2, ...)

**Response** (`GenerateResponse`):
- `audio_key` (str) — key MinIO do áudio gerado: `audio/{run_id}/part_{part_number}.mp3`
- `srt_key` (str) — key MinIO da legenda word-level: `subs/{run_id}/part_{part_number}.srt`

**Ordem das etapas:** TTS → pós-processamento (silêncio + loudness) → upload do áudio → transcrição → upload do SRT. A transcrição roda **depois** do pós-processamento, sobre o mesmo áudio que vai ao vídeo — é o que mantém a legenda em sincronia.

Erros: `502` se o TTS, o upload ao MinIO ou a transcrição falharem.

### Providers TTS (`src/tts_service/tts/`)

Selecionado por `TTS_PROVIDER` env var:

| Provider | Env var | Qualidade | Status |
|---|---|---|---|
| `azure` (recomendado) | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | 48 kHz / 192 kbps | Implementado |
| `edge` (padrão do código) | `TTS_VOICE` | 24 kHz / 48 kbps (fixo) | Implementado |
| `elevenlabs` | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | 44.1 kHz | Stub (NotImplementedError) |

**Vozes PT-BR** (as mesmas nos providers `edge` e `azure`):
- `pt-BR-ThalitaNeural` (padrão — feminina, jovem)
- `pt-BR-FranciscaNeural` (feminina)
- `pt-BR-AntonioNeural` (masculino)

Voz configurada por `TTS_VOICE` env var.

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

Acelera (ou desacelera) a narração na **própria síntese**, via `rate` do `edge_tts.Communicate` — que é `prosody rate` do SSML. O pitch fica intacto, diferente de acelerar o MP3 depois (resample deixa a voz aguda).

| Var | Padrão | Descrição |
|---|---|---|
| `TTS_RATE` | `+15%` | Percentual **com sinal** sobre o ritmo natural da voz |

Formato obrigatório: `^[+-]\d+%$` (`+15%`, `-10%`, `+0%` para desligar). Formato inválido **derruba o boot** em `TTSEnvSettings._check_rate_format` — falhar no start é melhor do que o edge-tts rejeitar na hora de sintetizar, no meio de uma request.

`EdgeTTSClient(voice=..., rate=...)` aceita override explícito; sem argumento, usa `settings.env.tts_rate`.

Como o rate age antes de tudo, a remoção de silêncio e a transcrição já rodam sobre o áudio acelerado — **o SRT sai sincronizado sem nenhum ajuste** e o `blender_worker` não muda.

O `elevenlabs.py` (stub) ainda não implementa rate; quando for implementado, o equivalente é o parâmetro `speed` do voice settings.

### Pós-processamento de áudio (`src/tts_service/audio/postprocess.py`)

Substituiu `audio/silence.py`. Corta silêncios **e** normaliza loudness numa **única passada de ffmpeg**, antes do upload ao MinIO. Uma passada só porque cada round-trip MP3→MP3 é outra geração lossy.

**Funções públicas:**
- `build_filter_chain(*, trim_silence, min_silence_ms, silence_thresh_db, normalize, loudness_target_lufs, sample_rate) -> list[str]` — os filtros em ordem; lista vazia = nada a fazer
- `process_audio(audio_bytes, *, trim_silence, min_silence_ms, silence_thresh_db, normalize, loudness_target_lufs, bitrate, sample_rate) -> bytes` — MP3 em bytes → MP3 em bytes. Puro. **Devolve o input intacto quando não há filtro**, em vez de re-encodar por nada.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `REMOVE_SILENCE` | `true` | Habilita o corte de silêncio |
| `SILENCE_THRESH_DB` | `-40` | Nível abaixo do qual é considerado silêncio |
| `MIN_SILENCE_MS` | `500` | Duração mínima para um silêncio ser removido |
| `NORMALIZE_AUDIO` | `true` | Habilita highpass + loudnorm |
| `LOUDNESS_TARGET_LUFS` | `-16` | Alvo de loudness integrada (referência das plataformas de vídeo) |
| `AUDIO_BITRATE` | `192k` | Bitrate do MP3 de saída |
| `AUDIO_SAMPLE_RATE` | `48000` | Sample rate do MP3 de saída |

`SILENCE_PADDING_MS` foi removido — nunca foi consumido pela implementação.

**Três armadilhas que a implementação evita (todas cobertas por teste):**
1. **`aresample` depois do `loudnorm` é obrigatório** — em single-pass o `loudnorm` emite 192 kHz independentemente da entrada. Sem o resample explícito o arquivo sai gigante sem ganho.
2. **Trim antes de loudnorm** — o `loudnorm` mede o stream inteiro; silêncio de borda puxa a medição para baixo e o filtro compensa deixando a voz alta demais.
3. **`-b:a` explícito** — sem ele o `libmp3lame` usa 128 kbps, rebaixando silenciosamente um source de 192 kbps a cada passada.

Falhas no pós-processamento são logadas como warning e o áudio original é usado (sem interromper o pipeline).

**Dependências:** `ffmpeg` (no Dockerfile) — filtros `silenceremove`, `highpass`, `loudnorm`, `aresample` via subprocess.

### Transcrição / legenda word-level (`src/tts_service/audio/transcribe.py`)

Transcreve o áudio final (já sem silêncios) e gera o SRT que o `blender_worker` usa como legenda. Substituiu o SRT com timing estimado que o orchestrador gerava a partir do roteiro.

**Função pública:**
- `transcribe_to_srt(audio_bytes, language="pt", model_name="base") -> bytes` — recebe MP3 em bytes, devolve SRT em bytes com **uma entrada por palavra**.

Usa `faster-whisper` com `word_timestamps=True` (device `cpu`, `compute_type="int8"`). O modelo é carregado uma vez e reaproveitado num cache global (`_model`) — a primeira request paga o download/load.

**Controle via env vars:**

| Var | Padrão | Descrição |
|---|---|---|
| `WHISPER_MODEL` | `base` | Tamanho do modelo (`tiny`, `base`, `small`, `medium`, ...) |
| `WHISPER_LANGUAGE` | `pt` | Idioma da transcrição |

Modelos maiores alinham as palavras melhor, ao custo de CPU. Falha na transcrição **derruba a request** com `502` (diferente da remoção de silêncio, que degrada silenciosamente).

O consumo dessa legenda (offset de sincronia, hold entre palavras, fades) é responsabilidade do `blender_worker` — ver `docs/vision.md` na raiz do monorepo.

**Sem testes** — `transcribe.py` não tem cobertura (exigiria mockar `WhisperModel` ou fixture de áudio real).

## Testes

`tests/test_generate.py` — 12 testes; edge-tts e MinIO sempre mockados; `REMOVE_SILENCE=false` **e `NORMALIZE_AUDIO=false`** no conftest, para que nenhum teste de endpoint chame ffmpeg.

`tests/test_postprocess.py` — 20 testes (era `test_silence.py`); áudio gerado por `wave` + ffmpeg (`_make_mp3`, com `amplitude` para gerar material quiet/hot). Cobre a montagem da filter chain, o corte de silêncio, sample rate e bitrate de saída via `ffprobe`, a loudness medida via filtro `ebur128`, e 4 testes de integração com o endpoint. Requer `ffmpeg` **e `ffprobe`** instalados.

`tests/test_azure.py` — 17 testes; `httpx.AsyncClient.post` sempre mockado, **nenhum acessa a rede**. Cobre derivação de locale, escape de XML no SSML, headers/URL/corpo da request, erro HTTP e corpo vazio, e a validação de config no boot (via `TTSEnvSettings()` direto, que relê o env).

⚠️ **Todo teste do endpoint que espera 201 precisa mockar `transcribe_to_srt` e `upload_bytes`** — use `_mock_transcription()` de `test_generate.py`. O `FAKE_MP3` do conftest é header + zeros; o Whisper real não decodifica isso e a rota devolve `502` na etapa de transcrição. Os testes ficaram quebrados exatamente assim quando a transcrição entrou na rota sem que os mocks fossem atualizados.

⚠️ **`settings.env` é um pydantic model frozen construído uma vez no bootstrap.** `monkeypatch.setenv` não alcança o código sob teste, e `setattr` no campo levanta `ValidationError` — para exercitar um branch que depende de env, troque o `settings` do módulo (ver `test_factory_unknown_raises`).

Rodar com Python 3.11 (o do Dockerfile) — anotações são avaliadas no import, então um nome não importado numa assinatura quebra a coleção do arquivo inteiro, coisa que o Python 3.14 local não acusa.

`tests/test_rate.py` — 16 testes; `edge_tts.Communicate` mockado. Cobre o repasse do `rate` na síntese, o override explícito no construtor, a validação de formato do `TTS_RATE` (via `TTSEnvSettings()` direto, que relê o env) e o campo `rate` no `/health`.

```bash
poetry run pytest
```
