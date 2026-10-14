# Ver upvotes sem credencial do Reddit

- **Período:** 07/09/2026
- **Sessão:** `4cbe3fe6-a79b-46ce-89e5-285c5815eafd`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 5

## Pedido
Ver os upvotes na avaliação das histórias sem registrar app no Reddit.

## O que foi feito
- Confirmado que o código OAuth já existia (só faltavam credenciais).
- Testado ao vivo: o endpoint `.json` público responde **403** tanto de datacenter quanto do IP residencial do servidor; `old.reddit` redireciona para login. Alternativa revertida e documentada como morta (`content_scout/CLAUDE.md`, `config.ini`).
- Pedido de usar Camoufox: levantados custo (Firefox completo no servidor de 8 GB) e risco de banir o IP de produção; ficou pendente saber por que as credenciais estavam fora de questão.
