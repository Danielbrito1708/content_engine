# Extrair roteiro de vídeo do TikTok; bug da faixa de áudio

- **Período:** 07/09/2026
- **Sessão:** `a4cab054-ecad-47c7-927b-4cc11da42287`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 5

## Pedido
Baixar um vídeo do TikTok e tirar o roteiro.

## O que foi feito
- Primeiro saiu via OCR, porque o Whisper não achou voz. O usuário apontou que havia narração.
- Causa real: o `yt-dlp` com `format: "ba/b"` baixou uma faixa de 189 s só com música; o vídeo tem 371 s com a narração. Transcrito corretamente a partir do vídeo completo.
- O mesmo código roda no `POST /transcribe` de produção → registrado em memória e como task para correção.
