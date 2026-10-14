# Atraso entre mudança no repo e vídeo publicado

- **Período:** 06/09/2026
- **Sessão:** `6b5da5fa-3a0a-409a-9fcc-04d2f1c4cdf0`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 2

## Pedido
Mudanças feitas no repo (ex.: música) devem aparecer com no máximo um post de atraso, não 10.

## O que foi feito
- `tiktok_poster/config.ini`: `buffer_queue_limit` de 10 → **1** (troca resiliência por frescor). Pior caso teórico registrado: 2 posts, por causa do `max_pending_runs=2` do scout.
- Testes ajustados para ler o limite do config; docs atualizadas (product, vision, CLAUDE.md, scheduling.md). 10 testes falharam por `ConnectionRefusedError` do Postgres local, não por lógica. Nada commitado na sessão.
