# Contas com formatos diferentes → edição declarativa (origem do VSEL)

- **Período:** 30/08 → 06/09/2026
- **Sessão:** `8ce4a805-8a7f-411a-8c5d-8511c923e007`, `eab97093-0948-4454-93c1-98c4cf2375b4`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 23

## Pedido
O que reaproveitar para muitas contas com formatos diferentes; depois, um arquivo de texto legível que descreva **edição arbitrária** de vídeo, que tanto o Claude quanto o usuário possam editar. (A sessão `eab97093` foi uma primeira tentativa interrompida da mesma conversa.)

## O que foi feito
- Diagnóstico: `template.json` só parametriza uma montagem fixa em `main()`; o `blender_worker` já tem `templates` 1-para-N e `template_key` por job.
- Avaliados MLT XML, OpenTimelineIO e formato próprio. Escolha: **YAML próprio executado sobre o Blender VSE**.
- Escrito `docs/edicao_declarativa.md` e implementado resolver (`timeline/expr.py`, `schema.py`, `resolver.py`, `payload.py`, `apply_payload`) e `templates_v2/default.yaml` — código "adormecido", sem ligar no pipeline.
- Verificação contra Blender 4.2 real: estrutura de strips idêntica, mas `scene.frame_end` 10 frames adiantado. Corrigido com resolução em duas passagens; reverificado (236=236, 146=146) e com assets reais de produção (3096=3096). Diferença residual de 3 frames no card (medição de MP3 ffprobe × Blender).
- Confirmado na época que o `narration.rate` publicado no bucket ainda era `+30%`.
