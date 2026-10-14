# Integração de novas contas TikTok/YouTube (multi-conta)

- **Período:** 15/08 → 29/08/2026
- **Sessão:** `81cbb49a-224a-412c-a030-b95a4c879b0b`
- **Branch:** `fix/scout-migration-no-boot`, `main`
- **Mensagens do usuário:** 4

## Pedido
Como integrar uma conta nova — e, depois, a visão de gerenciar centenas de contas (vários Buffers/e-mails), reaproveitando roteiros.

## O que foi feito
- Passo a passo para **trocar** de conta só via env vars (ordem importa: parar o orchestrator antes, esvaziar a fila com o token velho, `--force-recreate`).
- Análise de capacidade: o banco não é o gargalo. Render (~12min42s/parte → ~113 partes/dia), disco (~30 GB/dia), oferta de roteiro e notificações travam antes. O Buffer escala de graça (cota por token).
- Tensão central registrada: reaproveitar roteiro economiza roteiro, não render — MP4 idêntico em várias contas é fingerprint que derruba a rede inteira.
- Criado **`docs/multi_account.md`** (proposta, 310 linhas: modelo de dados `stories/accounts/renders/publications/jobs`, credenciais, planner, fases 1→3), com entrada no `vision.md` e no `CLAUDE.md`. Nada commitado.
