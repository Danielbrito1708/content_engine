# Passo a passo para conectar outra conta (PR #29)

- **Período:** 06/09/2026
- **Sessão:** `82f44c99-7b1f-40ff-99cb-8dc3622ba9ba`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 3

## Pedido
Passo a passo para conectar outra conta; commitar; mandar para produção.

## O que foi feito
- Passo a passo: `POST /accounts` no orchestrator → `POST /accounts` no `tiktok_poster` (token cifrado, datas de aquecimento) → `GET /health?account_id=…` (só vale com o token dono do canal).
- Commit `893bd85` (contas de publicação, trilha por mood, aquecimento, A/B por variante) e **PR #29**. O merge foi bloqueado pelo classificador e ficou com o usuário.
