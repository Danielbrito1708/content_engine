# Segunda conta de Reddit no TikTok: multi-conta Fase 1 e rampa de aquecimento

- **Período:** 06/09/2026
- **Sessão:** `5b69d807-6373-4dd3-8602-1d55584ad8db`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 6

## Pedido
Adicionar mais uma conta de histórias do Reddit no TikTok; depois, implementar o aquecimento de conta.

## O que foi feito
- **Multi-conta Fase 1**: tabela de contas no orchestrator, `account_id` em `pipeline_runs`, credenciais por conta no `tiktok_poster` (banco próprio, token cifrado com Fernet). Verificado com Postgres real: poster 121/121, orchestrator 225/234 (falhas pré-existentes/de outra sessão).
- **Rampa de aquecimento** implementada: `parse_warmup_steps`/`warmup_cap_for_day`, teto por dia (`1x7,2x7,3`), horários rotacionados por dia, data de início em `account_credentials` (migration `003`) ou `config.ini [warmup]`.
- Decisões fechadas: unidade do teto = história (continuação de série não é limitada), TikTok e YouTube podem estar em fases diferentes.
- Bug corrigido: `upsert_credentials` sobrescrevia campos omitidos com `null`.
