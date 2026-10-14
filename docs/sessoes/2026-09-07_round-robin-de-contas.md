# Postar na redditors-inc: round-robin de contas no scout (PR #31)

- **Período:** 07/09 → 08/09/2026
- **Sessão:** `51fd8005-906c-449e-9556-c106ed79fbd7`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 7

## Pedido
Adicionar a nova conta e fazê-la postar automaticamente.

## O que foi feito
- A conta já existia (`redditors-inc`, desde 06/09). Ela não postava porque o scout nunca escolhia conta — aquecimento controla ritmo, não origem.
- Implementado **round-robin** no `content_scout`: `pick_account()`, contagem na janela de 24 h (evita monopólio pela conta nova), teto por conta, `SeenItem.account_id` (migration `007`), filtro em `/scout/seen`, 10 testes.
- **PR #31** aberto; merge bloqueado pelo classificador.
- Incidente: ao isolar o commit, `test_concurrency.py` e `test_filters.py` não commitados foram descartados. `test_filters.py` foi reconstruído e a remoção do teto de tamanho, que estava misturada nos mesmos arquivos, também.

⚠️ Um token do Buffer foi colado no chat nesta sessão — tratar como exposto.
