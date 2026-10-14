# Quais informações ficam no banco

- **Período:** 15/08/2026
- **Sessão:** `ccd43def-d388-47e0-b706-2b8d9b02619d`
- **Branch:** `main`
- **Mensagens do usuário:** 1

## Pedido
Quais informações são guardadas no banco de dados.

## Resposta
Três bancos no mesmo Postgres (na época): `orchestrator` (`pipeline_runs`, `pipeline_parts` — roteiro cru/refinado, gancho, keys de storage, status, ids de post), `blender_worker` (`videos`, `templates`, `jobs`) e `content_scout` (itens vistos). `llm_service`, `tts_service` e `tiktok_poster` eram stateless.
