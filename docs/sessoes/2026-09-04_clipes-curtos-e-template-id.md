# Clipes de 3 s no fundo e template por vídeo (PR #28)

- **Período:** 04/09 → 06/09/2026
- **Sessão:** `d794ca2b-92a7-4817-87c0-fa25d8768e7f`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 10

## Pedido
Usar cortes de 3 s nos fundos de comida/ASMR (não em gameplay); depois, poder voltar à versão antiga do vídeo e misturar formatos (um vídeo no formato atual + dois de até 40 s).

## O que foi feito
- Esboço: categoria de fundo pedida ao LLM (`background_category`), pool por categoria e sequência de clipes curtos via VSEL — não implementado.
- Implementado **`template_id` por vídeo** no orchestrator (`PipelineCreate.template_id`, migration `009`, fallback para `BLENDER_TEMPLATE_ID`): permite rollback e mistura manual. Scout continua sem mandar template.
- Testes: 30/30 focados; suíte 233/234 (falha de outra sessão). Commit `339e122` (índice resetado porque outra sessão tinha feito `git add` amplo).
- **PR #28** (VSEL + `template_id`) aberto e mergeado; um segundo pedido de PR achou tudo já mergeado (PR #29 da outra sessão).

## Ficou em aberto
Registrar o template de comida/ASMR com cortes curtos.
