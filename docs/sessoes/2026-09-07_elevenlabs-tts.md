# Provider de TTS ElevenLabs

- **Período:** 07/09/2026
- **Sessão:** `68c85ffc-9d8f-4863-848e-06f4d0ac18b5`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 3

## Pedido
Implementar TTS via ElevenLabs; ativar localmente e testar de verdade.

## O que foi feito
- `ElevenLabsClient` (`POST /v1/text-to-speech/{voice_id}`), `rate_to_speed()` com clamp em `[0.7, 1.2]`, voice ids próprios por gênero, 19 testes. Corrigido bug do factory que ignorava `rate`/`voice` ao trocar de provider. Produção seguiu em `edge`.
- Teste real local: o plano gratuito recusa vozes de biblioteca via API (`402`), inclusive as pt-BR. Funcionaram só as premade em inglês (George, Sarah) falando pt-BR com sotaque. Voz nativa exige upgrade do plano.

⚠️ Uma API key foi colada no chat nesta sessão — tratar como exposta e revogar se ainda não foi.
