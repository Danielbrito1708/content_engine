# Origem da LLM, custo de escala, R2 e PR #26

- **Período:** 29/08 → 30/08/2026
- **Sessão:** `6b28c9b6-de7d-4bf4-92d4-725c5af86f1d`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 17

## Pedido
De onde vem a LLM; se trocar por um modelo barato (Llama 3.1 8B, DeepSeek) afetaria; plano para escalar de 3 para 30 vídeos/dia com o limite de 10 GB do R2.

## O que foi feito
- Produção usava OpenRouter com `anthropic/claude-haiku-4-5` nos três endpoints. `LLM_API_KEY` não é lido por nenhum código (resquício).
- Análise: `/refine` não deve ir para modelo 8B (tendência a resumir, português fraco); `/moderate` é o candidato. Em 30/dia, Haiku custaria ~$100/mês — render, disco e oferta de roteiro travam antes.
- Aplicado direto em produção: **DeepSeek V3.1** nos três endpoints (só env var), **retenção de 7 dias no R2** e subreddits `AmItheAsshole`/`antiwork`.
- O classificador do auto mode ficou fora do ar por várias tentativas; depois disso saiu o **PR #26**: caixa de entrada de links (ntfy → `/transcribe`), pin do Poetry 2.3.3 nos Dockerfiles, docs de `aquecimento.md`/`multi_account.md`, retenção do R2 e subreddits. `.ops/` ficou de fora.
