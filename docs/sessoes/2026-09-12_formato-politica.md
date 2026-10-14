# Formato de vídeo sobre política

- **Período:** 12/09/2026
- **Sessão:** `dd3d4320-47c3-45d3-8ab3-3f28fb287e2e`, `9251583b-c2ec-4025-a31e-6561f0087f55`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 10

## Pedido
Avaliar um formato no estilo do @plantaopolitico; baixar e analisar o vídeo; o VSEL faria esse vídeo? Como dar ao VSEL acesso aos keyframes do Blender? (A sessão `9251583b` é o ramo original desta conversa; esta é o fork.)

## O que foi feito
- Riscos: notícia morre em horas (fila do Buffer dias à frente), b-roll por pessoa citada, conteúdo sobre pessoas reais e eleição em 04/10 (regras do TSE para IA).
- Análise do vídeo baixado: 64 s, ~27 cortes, b-roll puxado pelo nome falado, legenda de uma palavra, texto sempre atribuído ("aliados dizem"), 0,05% de comentários.
- VSEL dá conta da timeline; o que falta é decidir qual clipe entra em cada segundo (LLM) e resolver assets por pessoa. A manchete entraria como PNG.
- Keyframes já existem em `edit_video.py`, mas hardcoded. O planejamento de um primitivo genérico foi delegado a um agente de design.
