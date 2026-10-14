# Novo formato com imagem de abertura; limites do VSEL e keyframes

- **Período:** 06/09/2026
- **Sessão:** `faae7957-43da-4a2e-9142-3065c4bf0763`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 4

## Pedido
Criar um formato novo (imagem de pintura religiosa na abertura); o VSEL cria qualquer vídeo? E animação com keyframes?

## Resposta
- VSEL é composição arbitrária sobre vocabulário fechado: 4 tipos de clipe (`video`, `audio`, `image`, `subtitles`), trilhas `content`/`bed`, tempo simbólico, 2 filtros reais (`volume_fade`, `fade_out`).
- Limitações: não estava ligado em produção, sem `text`, sem transições, `fade_in`/`rise`/`outline` como filtro são vocabulário morto.
- Keyframes: só três `data_path` hardcoded (`blend_alpha`, `volume`, `location[1]`), curva fixa.
- Proposta: campo `animate:` por clipe com `property` de vocabulário fechado (`scale`, `position_x/y`, `rotation`, `opacity`, `volume`), `at:` usando o tempo simbólico existente, e os efeitos atuais virando açúcar sintático. Nada implementado.
