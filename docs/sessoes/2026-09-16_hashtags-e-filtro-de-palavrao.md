# Hashtags, restrição do TikTok e filtro de palavrão

- **Período:** 16/09/2026
- **Sessão:** `3674a626-0e50-416d-8915-cbc6ada11257`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 4

## Pedido
Quais hashtags podem ser usadas; a conta tomou restrição de diretrizes por causa de uma hashtag; filtrar palavrão; anotar a ideia de motion design educativo.

## O que foi feito
- Mapa das hashtags: `mandatory` vazio, pool em `hashtags.json`, hints do LLM. `#amitheasshole` veio do LLM, que via o nome do subreddit.
- **Filtro de palavrão** em `tiktok_poster/hashtags/profanity.py` (substring sobre a tag normalizada, pt-BR + inglês), aplicado em `select_hashtags`. 9 testes novos, 28/28. Não commitado.
- Recomendada, não feita: blocklist de tags que denunciam origem Reddit (`#reddit` é obrigatória no YouTube).
- Ideia registrada em `docs/motion_design_educativo.md` (depois movida para `docs/ideias/`).
