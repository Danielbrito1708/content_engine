# Relatório do content_scout; upvotes via OAuth; fim do teto de tamanho

- **Período:** 07/09/2026
- **Sessão:** `eaa700f9-aab7-460a-aa0c-f04af667484f`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 11

## Pedido
Relatório completo do `content_scout` (regras, prompts, amostra); ver upvote/downvote no julgamento; acabar com o teto de tamanho.

## O que foi feito
- Relatório publicado como artifact ("Content Scout — Relatório"), com as 10 etapas, os prompts, amostra real de `/scout/seen` e config.
- **Teto de tamanho removido** (`max_chars`/`exceeds_length`).
- **Reddit via OAuth `client_credentials`**: `score`/`upvote_ratio` entram no `/story-quality` como sinal secundário e são gravados em `seen_items` (migration `006`). llm 169/169, scout no_db 102/102.
- O usuário tentou registrar o app "script" no Reddit; o reCAPTCHA resetava. Devvit descartado (não-comercial, não serve para isso).
- Recusado ajudar com ferramentas de evasão de detecção (nodriver, Camoufox, Patchright, Redlib). O `.json` sem auth estava morto desde maio/2026.
