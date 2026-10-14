# Coleta pública de dados do TikTok → serviço tiktok_scout

- **Período:** 10/09 → 11/09/2026
- **Sessão:** `30e00e3b-1728-4274-bc3c-ad1c522197b8`, `91a1c280-201d-446c-849d-582c59c419b0`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 3

## Pedido
Coletar de graça dados públicos de contas do TikTok (seguidores, vídeos, curva de performance) e decidir se isso merece serviço próprio. (A sessão `91a1c280` foi uma tentativa interrompida.)

## O que foi feito
- Criado o **`tiktok_scout`** (7º serviço, porta 8006): coleta por HTTP com fallback para Playwright, loop a cada 3 h, API `accounts`/`snapshots`/`videos`, modelo append-only, 46 testes.
- Removido do `tiktok_poster` o endpoint `/competitors` (Metricool sem dado de TikTok); migration `005` re-apontada para `003`. Poster 148/148.
- Pendência registrada: o parsing de `__UNIVERSAL_DATA_FOR_REHYDRATION__` não foi validado contra página real.
