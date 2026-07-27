# Fontes

Duas fontes são versionadas aqui, para dois consumidores diferentes:

| Arquivo | Usada por | Onde é apontada |
|---|---|---|
| `Futura-Bold.ttf` | legendas do vídeo (`scripts/edit_video.py`) | `subtitles.font_path` do `template.json` |
| `Arial-Black.ttf` | texto do comment card (`image/composer.py`) | `text.font_path` do guide em `templates/` |

**Arial Black não existe na imagem Docker.** O Dockerfile instala só `fonts-dejavu-core`;
a família Arial vem do pacote `ttf-mscorefonts-installer`, que exige aceite de EULA e baixa
de fora no build. Versionar o `.ttf` aqui é o que faz o card renderizar igual em qualquer
máquina. É uma fonte proprietária da Microsoft — a mesma consideração de licença que já
vale para a Futura.

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
