# Como redesenhar a estrutura/arquitetura (só análise)

- **Período:** 16/09/2026
- **Sessão:** `2e140206-419e-4a1c-a223-7e3f688e4b29`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 2

## Pedido
Sem mexer em nada: como reorganizar a estrutura do projeto; depois, como redesenhar a arquitetura pensando em facilidade de desenvolvimento.

## Resposta
- Estrutura: `src/core` copiado em 7 serviços, todos os pacotes chamados `src`, contratos entre serviços sem código, `vision.md` (2145 linhas) e `product.md` grandes demais, raiz do `blender_worker` misturada, scripts importantes em `.ops/`.
- Arquitetura (opinião que contraria a decisão de 16/09): **monólito modular** separado em processos por perfil de recurso (Blender e Whisper/GPU à parte), com `core/`, `engine/` e `formats/` num código só.
