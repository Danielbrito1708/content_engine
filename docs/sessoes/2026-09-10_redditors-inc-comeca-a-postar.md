# redditors-inc não postava: deploy do round-robin (PR #33) e primeiro post

- **Período:** 10/09/2026
- **Sessão:** `761f1b75-b047-4edd-bba1-c90e71747d7d`
- **Branch:** `feat/fundo-sob-demanda`, `feat/scout-account-round-robin`
- **Mensagens do usuário:** 10

## Pedido
Por que a `redditors-inc` não postava; fazer tudo e mandar para produção; publicar o vídeo agora.

## O que foi feito
- Causa: o round-robin estava só na branch, não no `main`, e o `config.ini` do servidor não tinha as chaves novas.
- **PR #33** rebaseado, suíte do scout em banco descartável (189/191, falhas pré-existentes), mergeado e deployado. O primeiro run pós-deploy foi para a `redditors-inc`, agendado para o dia seguinte por causa da rampa.
- Tentativa de antecipar via `editPost` (script `.ops/reschedule_now.py`): o Buffer aceitava mas ignorava o `dueAt`.
- Com o **Buffer MCP** conectado, `shareNow` publicou na hora.
- Ficou pendente um `stash` com WIP de ElevenLabs/tiktok_poster.
