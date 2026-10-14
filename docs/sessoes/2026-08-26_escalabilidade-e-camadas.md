# Escalabilidade e camadas: infra × formato × serviço

- **Período:** 26/08/2026
- **Sessão:** `1b41033d-53da-4faf-b423-13053984095b`
- **Branch:** `fix/scout-migration-no-boot`
- **Mensagens do usuário:** 4

## Pedido
Confirmar que a escala está documentada; depois, como suportar vários formatos (Reddit, leitura da Bíblia…) de forma gerenciável, separando infra, formato, serviço específico e serviço geral.

## O que foi discutido
- A escala está em `docs/multi_account.md`, ainda não commitado.
- Mapa de onde "Reddit" está colado no código (scout, prompt de refino, sequência do worker, card de comentário, template, fundos, hashtags).
- Distinção config × estágios: quase tudo vira dado (uma linha `formats`); só "de onde vem o texto" e a sequência de etapas são comportamento.
- Grep mostrou vazamento pequeno: léxico de Reddit só no `llm_service` (prompts/schemas de story) e em `config.ini`.
- Proposta de camadas: **0 infra**, **1 motor genérico**, **2 formato**, **3 serviço de formato** (ex.: `content_scout`), com **conta** como eixo transversal, e um teste de três perguntas para classificar cada peça.
