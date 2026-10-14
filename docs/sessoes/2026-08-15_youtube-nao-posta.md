# YouTube não posta / aquecimento de contas

- **Período:** 15/08 → 29/08/2026
- **Sessão:** `0f1febeb-55b2-4846-918c-f9c06867097e`
- **Branch:** `fix/scout-migration-no-boot`, `main`
- **Mensagens do usuário:** 11

## Pedido
"O YouTube não tá postando"; depois, 15 vídeos com menos de 2 views no YouTube; por fim, uma feature para aquecer contas novas.

## O que se descobriu / fez
- Nada estava postando (nem TikTok): a cota diária da API do Buffer (250 chamadas) tinha estourado com a varredura de 15 em 15 min. Os runs ficaram em `scheduling` esperando a janela virar — o 429 virou espera, não perda.
- `BUFFER_ORG_ID` confirmado preenchido no servidor (3 chamadas por agendamento em vez de 4).
- YouTube: vídeos públicos, classificados como Shorts, mas **zero impressões** → problema de entrega, não de título/thumbnail. Hipótese levantada: limitação de alcance por conteúdo "produzido em massa". Usuário pediu `isAiGenerated` → `false`.
- Escrito `docs/aquecimento.md` (proposta, sem código): rampa de publicação plugada em `next_available_slot()`, estado no config. Decisão do usuário: **a rampa vale para qualquer canal novo** (aquecimento é do canal, não da conta).

## Ficou em aberto
Unidade do teto (história vs. post), rotação de horário com teto baixo, valores dos degraus.
