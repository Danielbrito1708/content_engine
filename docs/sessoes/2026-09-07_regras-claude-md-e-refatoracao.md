# Regras dos CLAUDE.md, prompt para projeto novo e refatoração do CLAUDE.md

- **Período:** 07/09 → 11/09/2026
- **Sessão:** `db79ff87-4a7f-49e8-ac0b-ad39903f6761`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 6

## Pedido
Listar as regras dos `CLAUDE.md`; criar um prompt para um projeto novo reaproveitando as regras úteis; como trabalhar com várias branches/sessões em paralelo; refatorar o `CLAUDE.md`.

## O que foi feito
- Inventário das regras (global, raiz e por serviço).
- Prompt para outro projeto: estado volátil separado de regra, `decisions.md` imutável, `docs/features/` com ciclo de vida, aviso com condição de remoção, proibição de `PLAN.md`/`NOTES.md` na raiz.
- Worktrees: não há lock nem detecção de conflito entre sessões. Seção "Regras de trabalho em paralelo" escrita no `CLAUDE.md` e no prompt.
- **Refatoração do `CLAUDE.md`**: 325 → 191 linhas. Criados `docs/decisions.md` (tabelas datadas, ordem reversa) e `docs/operations.md` (servidor, estado, pendências com "como conferir" e "apagar quando"). Teto do `CLAUDE.md` passou a ~200 linhas.
