# Providers TTS — tts_service

Comparação entre os providers disponíveis, critérios de escolha e instruções de configuração.

**Arquivo de factory:** `src/tts_service/tts/factory.py`  
**Seleção:** variável de ambiente `TTS_PROVIDER`

---

## Visão geral

| Provider | Status | Custo | Latência | Qualidade | Requer API key |
|---|---|---|---|---|---|
| `edge` (Microsoft Neural) | Implementado | Gratuito | ~1–3s | Boa | Não |
| `elevenlabs` | Stub | Pago por caractere | ~2–5s | Excelente | Sim |

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

- **Sem controle de velocidade, tom ou ênfase** via API: o serviço não expõe parâmetros SSML via `edge-tts`.
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

| Critério | `edge` | `elevenlabs` (planejado) |
|---|---|---|
| Formato de saída | MP3 (bytes) | MP3 (bytes) |
| Key MinIO gerada | `audio/{run_id}/part_{n}.mp3` | `audio/{run_id}/part_{n}.mp3` |
| Compatível com blender_worker | Sim | Sim (mesma interface) |
| Adequado para produção | Sim, com ressalvas | Sim |

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

3. Adicionar testes mockando o provider em `tests/test_generate.py`.
4. Documentar neste arquivo com variáveis de ambiente, limitações e comparativo.

---

## O que ainda falta implementar

- **ElevenLabs**: implementar `ElevenLabsClient.generate()` com a API REST do ElevenLabs v1.
- **SSML para `edge`**: explorar se `edge-tts` expõe controle de velocidade via SSML tags.
- **Cache de áudio**: roteiros idênticos gerariam o mesmo MP3 — um cache por hash do texto evitaria chamadas redundantes à API.
