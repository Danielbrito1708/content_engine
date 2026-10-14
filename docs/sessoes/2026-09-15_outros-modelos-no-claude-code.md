# DeepSeek / GLM no Claude Code

- **Período:** 15/09/2026
- **Sessão:** `518657b3-5570-46b7-9ba7-1d419d29d0d5`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 11

## Pedido
Usar DeepSeek no Claude Code; limitações do DeepSeek; usar GLM 5.3 Flash via OpenRouter; qual modelo é melhor para este projeto.

## O que foi feito
- Configuração via `ANTHROPIC_BASE_URL` (OpenRouter) ou `claude-code-router`. O slug confirmado foi `z-ai/glm-5.3-flash`.
- Recomendação de roteamento: Claude para schema, migration, contratos e disciplina de docs; modelo barato só para boilerplate.
- Remote Control só funciona apontando para `api.anthropic.com`. Solução: função `claude-or` no profile do PowerShell que ativa o OpenRouter só durante aquela chamada; o `claude` normal continua nativo.
- O Claude recusou gravar a API key em arquivo. ⚠️ A chave foi colada no chat — tratar como exposta.
