# API de análise das contas e testes A/B por variante

- **Período:** 06/09/2026
- **Sessão:** `de250760-2613-4e5d-91e3-b1661c3551b8`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 5

## Pedido
API para analisar as várias contas do TikTok e fazer testes A/B.

## O que foi feito
- Implementado de ponta a ponta (`tts_service` → `orchestrator` → `tiktok_poster`): `/metrics/sync`, `/analytics/variants` (hashtag/horário/template/voz) e A/B de voz do narrador.
- Verificado com Docker de pé: tts 46/46, poster 121/121, orchestrator 230/234 (falhas conhecidas de `test_resilience` por causa do `.env` apontando para o R2 real). Bug de import `PartStatus` corrigido.
- Achado: suítes de sessões paralelas no mesmo `orchestrator_test` se apagam (`clean_db` autouse).
- O commit ficou aguardando o Docker e a outra sessão (aquecimento); entrou depois no commit `893bd85`.
