# Fontes das legendas

As legendas do vídeo (`scripts/edit_video.py`) usam **Futura Bold** como fonte padrão.

Futura é uma fonte licenciada e **não é redistribuída neste repositório**. Para usá-la,
coloque o arquivo aqui com um destes nomes:

```
assets/fonts/Futura-Bold.ttf
assets/fonts/Futura-Bold.otf
```

O `COPY . .` do `Dockerfile` leva o arquivo para dentro da imagem automaticamente —
basta rebuildar (`docker compose build blender_worker`).

## Fallback

`resolve_font_path()` testa os caminhos em ordem e usa o primeiro que existir:

1. `subtitles.font_path` do `template.json`, se definido
2. `assets/fonts/Futura-Bold.ttf`
3. `assets/fonts/Futura-Bold.otf`
4. `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`)

Se nenhum existir, as strips ficam com a fonte embutida do Blender. Fonte ausente
degrada o visual — nunca falha o render.

## Alternativa livre

Se não tiver licença da Futura, [Jost*](https://github.com/indestructible-type/Jost)
(SIL OFL) é o clone geométrico mais próximo. Baixe o `Jost-Bold.ttf`, coloque nesta
pasta e aponte no `template.json`:

```json
"subtitles": { "font_path": "assets/fonts/Jost-Bold.ttf" }
```
