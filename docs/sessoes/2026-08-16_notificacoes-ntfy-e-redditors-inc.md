# Notificações do WhatsApp → ntfy; cadastro da redditors-inc em produção

- **Período:** 16/08 → 06/09/2026
- **Sessão:** `3bb95651-7787-41b0-a86d-cbf8f2212ea8`
- **Branch:** `feat/fundo-sob-demanda`, `feat/revolta-e-notificacao-ntfy`, `fix/scout-migration-no-boot`, `main`
- **Mensagens do usuário:** 13

## Pedido
"Não estou recebendo as notificações no WhatsApp." A sessão continuou até 06/09 com o cadastro da segunda conta.

## O que foi feito
- Causa: a cota grátis do CallMeBot zerou; ele responde `200` mesmo recusando, então o log marcava sucesso por dias. Correção: `_deliver` lê o corpo e loga `notify_rejected`. Destino trocado para **ntfy** (`webhook_format = text`).
- Junto foi a feature de "revolta/outrage" no scout. **PR #20** mergeado e deployado; notificações voltaram.
- Armadilha achada: o `Dockerfile` do `content_scout` não rodava `alembic upgrade head` no boot (migration `005` aplicada à mão). Corrigido no **PR #21** (`alembic upgrade head && uvicorn`, migration que falha derruba o boot).
- Bug de teste de lado: `test_schedule_includes_mandatory_hashtags` falhava no `main` desde `a3a5263` — corrigido.
- 06/09: Parte A do deploy multi-conta em produção (chave Fernet, banco `tiktok_poster`, rebuild) e cadastro da **`redditors-inc`** (`account_id eacc6334…`, rampa a partir de 06/09). Stash do `main` velho do servidor guardado em patch.
- Recomendação: não postar na conta nova na primeira semana (aquecimento manual do perfil). Criado lembrete agendado para 10/09.
