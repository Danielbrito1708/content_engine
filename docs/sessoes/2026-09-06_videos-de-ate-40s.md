# Vídeos de até 40 segundos e rollback

- **Período:** 06/09/2026
- **Sessão:** `c7f470eb-054f-41cd-8e51-f0b974a7736a`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 3

## Pedido
Se já se falou em vídeos de 40 s ou menos; se foi implementado; se há rollback (curtos para seguidores, longos para monetizar).

## Resposta
- Decisão de 31/08: 10–40 s, um vídeo por história, roteiro condensado (37–150 palavras), CTA narrado, `narration.rate +50%`.
- Implementado no `llm_service` (`TARGET_MIN/MAX_SECONDS`, `_collapse_to_single_part`), mas o `+50%` não estava publicado no bucket → vídeos saindo com ~46 s.
- Rollback existe para template (`template_id` por run, commit `339e122`), **não** para duração do roteiro, que é global. Proposto um `duration_profile: short|long` por run — ficou aguardando decisão.
