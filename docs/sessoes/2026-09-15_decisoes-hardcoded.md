# Decisões hardcoded na criação dos vídeos

- **Período:** 15/09 → 16/09/2026
- **Sessão:** `8e326981-39d3-4cff-855f-39a8fc4273f7`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 2

## Pedido
Levantamento minucioso do que é hardcoded; depois, poder mudar um canal específico ou todos, sem deploy.

## O que foi feito
- Inventário por serviço em três categorias: [CÓDIGO], [CONFIG] e [DRIFT] (ex.: `temperature=0.7` só no client OpenAI-compat, `NARRATION_WPM=225` × `+30%` publicado).
- Proposta: estender o `ProductionProfile` em vez de arquivos — `Account.profile_id` como default do canal, com precedência `run > perfil do run > perfil da conta > perfil global > env`.
- `narration.rate` por canal reabriria a decisão fechada de 31/08 (duração).
