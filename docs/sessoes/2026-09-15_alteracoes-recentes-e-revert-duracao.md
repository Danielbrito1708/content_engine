# Alterações dos últimos dias; duração fora das alavancas de experimento

- **Período:** 15/09/2026
- **Sessão:** `0789c0ec-c8ba-44df-84e3-84ccf60f1bf9`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 9

## Pedido
Resumo das mudanças recentes e do que afeta o roteiro; reverter qualquer coisa que permitisse mudar a duração dos vídeos; abrir PR.

## O que foi feito
- Linha do tempo de 06/09 a 10/09 (template por vídeo, contas/aquecimento/A-B, novos subs, round-robin, VSEL em produção + faixa CTA, Metricool), mais o WIP do motor de experimentos.
- **Revert**: `target_min/max_seconds` saíram de `TUNABLE_CONSTANTS` e `narration_rate` de `KNOWN_OVERRIDES`. A duração 10–40 s voltou a ser decisão fechada, não alavanca de experimento.
- 9 commits locais; o push foi rejeitado porque `origin/feat/fundo-sob-demanda` estava parado num commit já mergeado. Ficou a escolha entre force-push e branch nova.
- Achado: `stash@{0}` órfão de 10/09 com WIP de ElevenLabs e outros arquivos.
