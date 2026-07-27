# Fontes das legendas

As legendas do vídeo (`scripts/edit_video.py`) usam **Futura Bold**, versionada aqui
como `Futura-Bold.ttf`.

Esta pasta fica **dentro do build context do `blender_worker`** (o compose usa
`build: ./blender_worker`), então o `COPY . .` do Dockerfile leva a fonte para dentro
da imagem. Uma fonte na raiz do monorepo *não* chegaria no container.

O nome do arquivo é case-sensitive no Linux: tem que ser exatamente `Futura-Bold.ttf`.

## Fallback

`resolve_font_path()` testa os caminhos em ordem e usa o primeiro que existir:

1. `subtitles.font_path` do `template.json`, se definido
2. `assets/fonts/Futura-Bold.ttf`
3. `assets/fonts/Futura-Bold.otf`
4. `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` (pacote `fonts-dejavu-core`)

Se nenhum existir, as strips ficam com a fonte embutida do Blender. Fonte ausente
degrada o visual — nunca falha o render.

## Trocar a fonte

Coloque o `.ttf` ou `.otf` aqui e aponte no `template.json`:

```json
"subtitles": { "font_path": "assets/fonts/OutraFonte.ttf" }
```

Arquivos de fonte são marcados como `binary` no `.gitattributes` da raiz — sem isso
o `core.autocrlf` do Windows pode corromper o arquivo no commit.
