# Histórias com arco de redenção; novos subreddits (PR #30)

- **Período:** 07/09/2026
- **Sessão:** `c2669fde-07dc-495a-a2ba-d671332d2bb9`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 4

## Pedido
Buscar histórias com o arco "protagonista por baixo → humilhado → dá a volta → vilão passa a respeitar"; adicionar os subs pesquisados ao scout e subir para o servidor.

## O que foi feito
- Busca no Reddit; `r/ProRevenge` estava vazio.
- `pettyrevenge`, `MaliciousCompliance` e `EntitledPeople` adicionados ao `content_scout/config.ini` (8 subs no total). **PR #30** mergeado e deployado.
- O primeiro ciclo buscou 102 candidatos e submeteu 0: as duas vagas de `max_pending_runs` estavam presas em `scheduling` (um run desde 05/09). Nada perdido; investigação desses runs ficou oferecida.
