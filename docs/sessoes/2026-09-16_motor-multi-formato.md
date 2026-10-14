# Contêiner por formato? → design multi-formato (docs/formatos.md)

- **Período:** 16/09/2026
- **Sessão:** `151b1c52-8dc7-4cbb-b30b-b068e9d96767`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 12

## Pedido
Discutir a tese "cada formato precisa de um contêiner próprio", considerando que a lógica muda muito e cada formato alimenta 4 a 8 perfis; depois, desenhar um system design.

## O que foi feito
- Etapas: coleta → refino → moderação → geração de mídia → edição → **publicação**. Aplicadas a Reddit, motion design, cortes e lutas: coleta e seleção/moderação divergem como disciplinas técnicas diferentes; publicação não diverge.
- **`docs/formatos.md`** e artifact "Motor Multi-formato": lógica do formato isolada em `formats/<formato>/`, capacidades compartilhadas (LLM, TTS, blender, poster), contêiner novo só por dependência pesada, orchestrator como motor de etapas com fila no Postgres (retoma após restart) e worker remoto com GPU. Migração começa pelo Reddit sem mudar comportamento.
- Riscos abertos: cota do Buffer com 16–32 contas e direitos autorais.
- Commit `9b07e4f` só com docs (formatos.md, tabela de 16/09 em decisions.md, README de ideias, linha no CLAUDE.md), sem rebase.
