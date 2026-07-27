# Providers TTS — tts_service

Comparação entre os providers disponíveis, critérios de escolha e instruções de configuração.

**Arquivo de factory:** `src/tts_service/tts/factory.py`  
**Seleção:** variável de ambiente `TTS_PROVIDER`

---

## Visão geral

| Provider | Status | Custo | Latência | Formato de saída | Requer API key |
|---|---|---|---|---|---|
| `azure` (**recomendado**) | Implementado | Free tier 500k chars/mês | ~1–3s | 48 kHz / 192 kbps | Sim |
| `edge` (Microsoft Neural) | Implementado | Gratuito | ~1–3s | **24 kHz / 48 kbps (fixo)** | Não |
| `elevenlabs` | Stub | Pago por caractere | ~2–5s | 44.1 kHz | Sim |

`azure` e `edge` servem **as mesmas vozes neurais**. A diferença é só o formato de saída — e é uma diferença grande: 24 kHz significa que nada acima de ~12 kHz existe no sinal, o que é o que faz a narração do `edge` soar abafada. Nenhum pós-processamento recupera isso.

---

## Provider `azure` — Azure Speech (Cognitive Services)

**Serviço:** Azure AI Speech, REST API v1
**Arquivo:** `src/tts_service/tts/azure.py`
**Dependência:** `httpx`

Mesmas vozes do `edge`, mesmo `TTS_RATE`, mesma interface. Trocar de provider **não muda a voz nem o ritmo** da narração — só a qualidade do arquivo.

### Configuração

```env
TTS_PROVIDER=azure
AZURE_SPEECH_KEY=<key do recurso Speech>
AZURE_SPEECH_REGION=brazilsouth
TTS_VOICE=pt-BR-ThalitaNeural                          # opcional
AZURE_OUTPUT_FORMAT=audio-48khz-192kbitrate-mono-mp3   # opcional — esse é o padrão
```

Para obter a key: portal do Azure → criar recurso **Speech** → *Keys and Endpoint*. O free tier (F0) cobre 500.000 caracteres/mês de vozes neurais, o que é bastante para o volume do pipeline.

`AZURE_SPEECH_KEY` ou `AZURE_SPEECH_REGION` ausentes **derrubam o boot do serviço** — config errada falha no start, não no meio de um pipeline run.

### Formatos de saída úteis

| Formato | Uso |
|---|---|
| `audio-48khz-192kbitrate-mono-mp3` | **Padrão.** Melhor relação qualidade/tamanho para narração |
| `audio-24khz-48kbitrate-mono-mp3` | Equivalente ao `edge` — só para comparar A/B |
| `riff-48khz-16bit-mono-pcm` | WAV sem perda. Exige mudar o pipeline (a key MinIO é `.mp3`) |

### Limitações

- **Custo acima do free tier**: cobrança por caractere depois de 500k/mês.
- **Dependência de conectividade e de região**: a região da key tem que bater com `AZURE_SPEECH_REGION`, senão o endpoint responde `403`.
- **SSML**: o corpo da request é SSML, então o texto é escapado (`xml.sax.saxutils.escape`) antes de entrar. Roteiro com `&` ou `<` geraria XML malformado e `400`.

---

## Provider `edge` — Microsoft Neural TTS

**Pacote:** `edge-tts` (Python, open source)  
**Serviço subjacente:** Microsoft Azure Cognitive Services (acesso gratuito via edge browser API)  
**Arquivo:** `src/tts_service/tts/edge.py`

### Vozes PT-BR disponíveis

| Voz | Gênero | Estilo | Indicação |
|---|---|---|---|
| `pt-BR-ThalitaNeural` | Feminina | Jovem, animada | **Padrão** — melhor para TikTok |
| `pt-BR-FranciscaNeural` | Feminina | Neutra, clara | Conteúdo educativo formal |
| `pt-BR-AntonioNeural` | Masculino | Neutro | Conteúdo mais sério |

Para listar todas as vozes disponíveis (inclui dialetos regionais):

```bash
python -m edge_tts --list-voices | grep pt-BR
```

### Configuração

```env
TTS_PROVIDER=edge
TTS_VOICE=pt-BR-ThalitaNeural  # opcional — esse é o padrão
```

### Limitações

- **Qualidade travada em 24 kHz / 48 kbps**: o formato de saída é **hardcoded** em `audio-24khz-48kbitrate-mono-mp3` (`edge_tts/communicate.py`) — é constante na lib, não parâmetro, porque o endpoint gratuito do Edge só serve esse formato. É a razão de existir o provider `azure`.
- **Dependência de conectividade**: a geração requer acesso à internet (chamadas ao endpoint da Microsoft). Sem rede, a geração falha.
- **Rate limiting implícito**: o endpoint é da Microsoft e não documenta limites públicos. Para volumes altos, considerar ElevenLabs.
- **Qualidade para texto longo**: frases acima de ~200 palavras por chamada podem soar monótonas. O orchestrador já divide o roteiro em partes de até ~600 palavras, mitigando parcialmente esse problema.

---

## Provider `elevenlabs` — ElevenLabs API

**Status:** stub — `NotImplementedError` ao chamar  
**Arquivo:** `src/tts_service/tts/elevenlabs.py`

O provider está arquitetado mas não implementado. Quando implementado, seguirá a mesma interface `BaseTTSClient.generate(text) -> bytes`.

### Configuração (quando implementado)

```env
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=sk-...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM  # Rachel (default ElevenLabs)
```

### Vantagens sobre o edge

- **Controle granular**: velocidade, estabilidade, similaridade com a voz original e exagero de estilo são parâmetros ajustáveis.
- **Clonagem de voz**: possível treinar uma voz personalizada.
- **Qualidade superior**: especialmente para conteúdo emocional (drama, motivacional).
- **Confiabilidade**: SLA documentado, sem dependência de edge browser API.

### Desvantagens

- **Custo**: cobrança por caractere. Plano gratuito: 10.000 caracteres/mês.
- **Latência**: ligeiramente maior que `edge` para textos curtos.

---

## Comparação de saída para o pipeline

| Critério | `azure` | `edge` | `elevenlabs` (planejado) |
|---|---|---|---|
| Formato de saída | MP3 (bytes) | MP3 (bytes) | MP3 (bytes) |
| Sample rate / bitrate | 48 kHz / 192 kbps | 24 kHz / 48 kbps | 44.1 kHz |
| Respeita `TTS_VOICE` / `TTS_RATE` | Sim | Sim | Não (stub) |
| Key MinIO gerada | `audio/{run_id}/part_{n}.mp3` | idem | idem |
| Compatível com blender_worker | Sim | Sim | Sim (mesma interface) |
| Adequado para produção | **Sim** | Só se não houver key | Sim |

Seja qual for o provider, o áudio passa por `audio/postprocess.py` antes do upload (corte de silêncio + normalização de loudness, numa única passada de ffmpeg). Por padrão o arquivo final **casa com o formato que o provider entregou** — quem define a qualidade é a escolha do provider, não o pós-processamento.

`AUDIO_BITRATE` / `AUDIO_SAMPLE_RATE` existem para forçar outra coisa, mas cuidado nas duas direções: apertar (`AUDIO_BITRATE=64k` com o Azure) joga fora a qualidade que você está pagando; afrouxar (`48000`/`192k` com o `edge`) só infla o arquivo — medido em **4× maior** para áudio audivelmente idêntico.

---

## Como adicionar um novo provider

1. Criar `src/tts_service/tts/meu_provider.py` herdando `BaseTTSClient`:

```python
from src.tts_service.tts.base import BaseTTSClient

class MeuProviderClient(BaseTTSClient):
    async def generate(self, text: str) -> bytes:
        # retornar bytes de um arquivo MP3
        ...
```

2. Registrar em `src/tts_service/tts/factory.py`:

```python
case "meu_provider":
    return MeuProviderClient(...)
```

3. Adicionar um `tests/test_<provider>.py` com o cliente HTTP mockado (ver `tests/test_azure.py` como modelo — nenhum teste acessa a rede).
4. Documentar neste arquivo com variáveis de ambiente, limitações e comparativo.

---

## O que ainda falta implementar

- **ElevenLabs**: implementar `ElevenLabsClient.generate()` com a API REST do ElevenLabs v1. O equivalente ao `TTS_RATE` lá é o parâmetro `speed` do voice settings.
- **Cache de áudio**: roteiros idênticos gerariam o mesmo MP3 — um cache por hash do texto evitaria chamadas redundantes à API.
- **Pipeline sem perda**: pedir `riff-48khz-16bit-mono-pcm` ao Azure e só encodar MP3 no fim eliminaria a última geração lossy antes do Blender. Exige o `tts_service` decidir o container de saída em vez de assumir MP3 na key MinIO.
