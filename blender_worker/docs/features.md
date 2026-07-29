# Features — blender_worker

Descrição detalhada de cada funcionalidade do sistema: comportamento esperado, contratos de API, fluxo de dados, casos de erro e dependências.

---

## 1. Health Check

**Rota:** `GET /health`  
**Arquivo:** `src/blender_worker/api/routes/health.py`  
**Status:** Implementado

### Descrição
Verifica se os serviços de infraestrutura estão acessíveis. Usado para monitoramento e para confirmar que o container está pronto para receber jobs.

### Comportamento
- Executa `SELECT 1` no Postgres via engine async
- Chama `list_buckets()` no MinIO via boto3
- Cada check é independente — falha em um não impede o outro de rodar
- Retorna `status: "ok"` se todos os checks passaram, `status: "degraded"` caso contrário

### Resposta
```json
// 200 OK — todos os serviços saudáveis
{
  "status": "ok",
  "checks": {
    "db": "ok",
    "minio": "ok"
  }
}

// 200 OK — com falha parcial
{
  "status": "degraded",
  "checks": {
    "db": "ok",
    "minio": "error: Connection refused"
  }
}
```

> Sempre retorna HTTP 200 — o status de degradação está no corpo, não no código HTTP.

---

## 2. Gerenciamento de Vídeos

**Rotas:** `POST /videos`, `GET /videos/{id}`  
**Arquivo:** `src/blender_worker/api/routes/videos.py`  
**Status:** Implementado

### Descrição
Registra um vídeo no sistema. Um "vídeo" não é o arquivo em si — é um registro que aponta para todos os assets necessários para montar o vídeo (já devem estar no MinIO antes do registro).

### POST /videos — Registrar vídeo

**Request:**
```json
{
  "video_file_key": "videos/ep01/background.mp4",
  "music_key": "videos/ep01/music.mp3",
  "voice_key": "videos/ep01/voice.mp3",
  "subtitle_key": "videos/ep01/subtitles.srt",
  "video_metadata": {
    "title": "Episódio 01",
    "duration_seconds": 30,
    "fps": 30,
    "resolution": "1920x1080"
  }
}
```

**Resposta:**
```json
// 201 Created
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "video_file_key": "videos/ep01/background.mp4",
  "music_key": "videos/ep01/music.mp3",
  "voice_key": "videos/ep01/voice.mp3",
  "subtitle_key": "videos/ep01/subtitles.srt",
  "video_metadata": { "title": "Episódio 01", ... },
  "created_at": "2026-05-04T00:00:00Z"
}
```

**Validações:**
- Todos os `*_key` são obrigatórios
- `metadata` é opcional
- O sistema **não** verifica se os arquivos existem no MinIO no momento do registro — isso é responsabilidade de quem chama

**Casos de erro:**
- `422` — campos obrigatórios ausentes ou tipo inválido

### GET /videos/{id} — Buscar vídeo

**Resposta:**
```json
// 200 OK
{ /* mesmo formato do POST */ }

// 404 Not Found
{ "detail": "Video not found" }
```

---

## 3. Gerenciamento de Templates

**Rotas:** `POST /templates`, `GET /templates/{id}`  
**Arquivo:** `src/blender_worker/api/routes/templates.py`  
**Status:** Implementado

### Descrição
Registra um template de edição. Um template é composto por:
- Um arquivo `.blend` base — estrutura visual, posição de canais, configurações de cena
- Um arquivo `.json` de timing — define os frames de cada seção do vídeo

Templates são reutilizados em múltiplos jobs. Criar um template bem calibrado é o trabalho manual que o sistema elimina em escala.

### POST /templates — Registrar template

**Request:**
```json
{
  "name": "Reels 30s v2",
  "blend_key": "templates/reels-30s-v2.blend",
  "json_key": "templates/reels-30s-v2.json"
}
```

**Resposta:**
```json
// 201 Created
{
  "id": "a1b2c3d4-...",
  "name": "Reels 30s v2",
  "blend_key": "templates/reels-30s-v2.blend",
  "json_key": "templates/reels-30s-v2.json",
  "created_at": "2026-05-04T00:00:00Z"
}
```

### Formato do template.json

```json
{
  "frame_rate": 30,
  "frame_end": 900,
  "channels": {
    "video": 1,
    "music": 2,
    "voice": 3,
    "subtitles": 4
  },
  "subtitles": {
    "fade_frames": 3,
    "max_hold_seconds": 0.4
  },
  "music": {
    "fade_out_seconds": 1.5
  },
  "timing": {
    "intro_start": 0,
    "intro_end": 90,
    "speech_start": 90,
    "speech_end": 750
  }
}
```

| Campo | Descrição |
|---|---|
| `frame_rate` | FPS da cena Blender |
| `frame_end` | Fallback do frame final; na prática quem decide é a narração |
| `channels.*` | Canal VSE de cada trilha |
| `subtitles` | Opcional. `fade_frames` (padrão 3, `0` desliga) e `max_hold_seconds` (padrão 0.4) — ver seção 7 |
| `music` | Opcional. `fade_out_seconds` (padrão 1.5, `0` desliga) — fade contado a partir do último frame |
| `timing.*` | Frames de início/fim de cada seção |

Não há seção de outro: o vídeo termina na última palavra da narração. `outro_start`, `outro_end` e `music_fade_out` foram removidos — nenhum era lido pelo código, e o fade em frame fixo fazia a trilha decair pelo vídeo inteiro.

**Casos de erro:**
- `422` — campos obrigatórios ausentes
- `404` — template não encontrado no GET

---

## 4. Criação e Polling de Jobs

**Rotas:** `POST /jobs`, `GET /jobs/{id}`  
**Arquivo:** `src/blender_worker/api/routes/jobs.py`  
**Status:** Implementado

### Descrição
Cria uma tarefa de montagem de vídeo e permite acompanhar seu progresso. O processamento acontece em background — a rota retorna imediatamente com status `pending`.

### POST /jobs — Criar job

**Request:**
```json
{
  "video_id": "550e8400-e29b-41d4-a716-446655440000",
  "template_id": "a1b2c3d4-e5f6-...",
  "params": {
    "output_key": "outputs/custom-path.blend"
  }
}
```

| Campo | Obrigatório | Descrição |
|---|---|---|
| `video_id` | Sim | UUID de um Video registrado |
| `template_id` | Sim | UUID de um Template registrado |
| `params` | Não | Overrides opcionais (ex: output_key customizado) |

**Resposta:**
```json
// 201 Created
{
  "id": "job-uuid",
  "video_id": "...",
  "template_id": "...",
  "status": "pending",
  "output_key": null,
  "params": null,
  "error": null,
  "created_at": "2026-05-04T00:00:00Z",
  "updated_at": "2026-05-04T00:00:00Z"
}
```

**Comportamento:**
1. Job é persistido no DB com `status = pending`
2. `render_job(job.id)` é enfileirado via `BackgroundTasks`
3. Resposta retorna imediatamente — o cliente deve fazer polling

**Casos de erro:**
- `422` — `video_id` ou `template_id` ausentes ou formato inválido
- `404` — `video_id` ou `template_id` não existem no DB

### GET /jobs/{id} — Consultar status

**Resposta:**
```json
// 200 OK — job em andamento
{
  "id": "job-uuid",
  "status": "running",
  "output_key": null,
  "error": null,
  ...
}

// 200 OK — job concluído
{
  "id": "job-uuid",
  "status": "completed",
  "output_key": "outputs/job-uuid.blend",
  "error": null,
  ...
}

// 200 OK — job falhou
{
  "id": "job-uuid",
  "status": "failed",
  "output_key": null,
  "error": "blender: command not found",
  ...
}

// 404 Not Found
{ "detail": "Job not found" }
```

**Ciclo de vida do status:**
```
pending → running → completed
                 ↘ failed
```

---

## 5. Worker de Montagem

**Função:** `render_job(job_id: UUID)`  
**Arquivo:** `src/blender_worker/worker.py`  
**Status:** Implementado

### Descrição
Função async chamada em background após a criação do job. Executa o pipeline completo de montagem: download de assets, execução do Blender, upload do resultado.

### Pipeline detalhado

```
1. Busca Job no DB pelo job_id
   → Se não encontrado: loga erro e retorna (sem exception)

2. Atualiza status → running, commit

3. Busca Video e Template no DB pelo job.video_id e job.template_id

4. Cria diretório temporário: /tmp/blender_worker/{job_id}/

5. Download do MinIO para o tempdir:
   ├── template.blend   ← Template.blend_key
   ├── template.json    ← Template.json_key
   ├── video.mp4        ← Video.video_file_key  (extensão preservada)
   ├── music.mp3        ← Video.music_key       (extensão preservada)
   ├── voice.mp3        ← Video.voice_key       (extensão preservada)
   └── subtitles.srt    ← Video.subtitle_key

6. Serializa job_config.json no tempdir:
   {
     "job_id": "...",
     "output_path": "/tmp/blender_worker/{job_id}/output.blend",
     "assets": {
       "video": "/tmp/.../video.mp4",
       "music": "/tmp/.../music.mp3",
       "voice": "/tmp/.../voice.mp3",
       "subtitles": "/tmp/.../subtitles.srt"
     },
     "timing": { ...conteúdo do template.json... }
   }

7. Executa Blender como subprocess:
   blender -b template.blend -P scripts/edit_video.py -- /tmp/.../job_config.json
   → check=True: qualquer exit code != 0 levanta CalledProcessError → status = failed

8. Upload do output.blend → MinIO: "outputs/{job_id}.blend"

9. Atualiza Job: output_key = "outputs/{job_id}.blend", status = completed, commit

10. Limpa tempdir
```

**Tratamento de erros:**
- Qualquer exception em qualquer etapa define `status = failed` e `error = str(exc)`
- O `finally` garante o commit do status mesmo em caso de erro
- Tempdir é limpo mesmo em caso de erro

---

## 6. Script de Edição Blender

**Arquivo:** `scripts/edit_video.py`  
**Status:** Implementado

### Descrição
Script Python executado dentro do interpretador do Blender. Recebe o caminho do `job_config.json` como argumento após `--`. Não pode importar de `src/` — deve ser self-contained usando apenas stdlib e a API `bpy`.

### ⚠️ `fps_base` precisa ser resetado

O FPS efetivo do Blender é `render.fps / render.fps_base`, e o `fps_base` vem do `.blend`. O `template.blend` atual está gravado como `fps=6, fps_base=0.1` (ou seja, 60 fps). Definir só `render.fps = frame_rate` deixa o `fps_base` intacto e a cena roda a `frame_rate / 0.1` — **10× o pretendido**.

O `main()` define `render.fps_base = 1.0` junto com `render.fps`. Sem isso: strips de áudio ficam 10× mais longas (o `frame_end` calculado a partir delas estoura), o MP4 sai com o fps errado, e todo timing em frames (legendas, `speech_start`, o fade da trilha) fica fora de sincronia com o áudio.

### Estrutura

```python
import bpy, json, sys, os, re

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:]
    return argv[0]  # caminho do job_config.json

def setup_vse(scene):
    if not scene.sequence_editor:
        scene.sequence_editor_create()
    return scene.sequence_editor

def add_video_strip(vse, path, channel, frame_start):
    # bpy.ops.sequencer.movie_strip_add(...)

def add_audio_strip(vse, path, channel, frame_start):
    # bpy.ops.sequencer.sound_strip_add(...)

def build_subtitle_timeline(entries, frame_rate, frame_offset, fade_frames, max_hold_seconds):
    # Puro: SRT → specs de strip sem overlap. Ver seção 7.
    ...

def import_subtitles(scene, vse, srt_path, channel, frame_rate, frame_offset, **subtitle_opts):
    # Consome os specs e cria as text strips (scene: os keyframes vivem na action da cena)
    ...

def main():
    config = json.load(open(parse_args()))
    timing = config["timing"]
    assets = config["assets"]

    scene = bpy.context.scene
    scene.frame_end = timing["frame_end"]
    scene.render.fps = timing["frame_rate"]

    vse = setup_vse(scene)
    speech_start = timing["timing"]["speech_start"] + 1

    add_video_strip(vse, assets["video"], timing["channels"]["video"],  timing["timing"]["intro_start"])
    add_audio_strip(vse, assets["music"], timing["channels"]["music"],  timing["timing"]["intro_start"])
    add_audio_strip(vse, assets["voice"], timing["channels"]["voice"],  speech_start)

    # frame_offset = speech_start: o SRT é relativo ao início da narração
    import_subtitles(scene, vse, assets["subtitles"], timing["channels"]["subtitles"],
                     timing["frame_rate"], frame_offset=speech_start, **timing.get("subtitles", {}))

    bpy.ops.wm.save_as_mainfile(filepath=config["output_path"])

if __name__ == "__main__":   # Blender roda o script como __main__; o guard deixa os
    main()                   # helpers puros importáveis pelos testes
```

---

## 7. Legendas palavra por palavra (import_subtitles)

**Funções:** `parse_srt()`, `ts_to_frame()`, `build_subtitle_timeline()`, `import_subtitles()`  
**Localização:** `scripts/edit_video.py`  
**Status:** Implementado

### Descrição
O Blender não tem suporte nativo para legendas. Estas funções parseiam um `.srt` e criam text strips no VSE, uma por entrada, com keyframes de opacidade nas bordas.

O `.srt` vem do `tts_service`, que transcreve a narração com Whisper (`word_timestamps=True`) e emite **uma entrada por palavra**. O timeline é construído para esse formato: entradas curtíssimas, muitas por segundo, quase sempre encostadas umas nas outras.

### Formato SRT suportado
```
1
00:00:00,000 --> 00:00:00,320
Bem-vindo

2
00:00:00,320 --> 00:00:00,540
ao
```

Entradas de frase (várias palavras, múltiplas linhas) continuam funcionando — o timeline não assume tamanho de entrada.

### API

```python
parse_srt(path) -> list[(start_ts, end_ts, text)]
ts_to_frame(timestamp, frame_rate) -> int          # arredonda para o frame mais próximo

build_subtitle_timeline(
    entries, frame_rate,
    frame_offset=0, fade_frames=3, max_hold_seconds=0.4, rise_frames=4,
) -> list[dict]   # {start, end, text, fade_in, fade_out, rise} — tudo em frames

import_subtitles(
    scene, vse, srt_path, channel, frame_rate,
    frame_offset=0, fade_frames=3, max_hold_seconds=0.4,
    rise_frames=4, rise_offset=0.025,
) -> int          # nº de strips criadas
```

`scene` é necessário porque os keyframes de uma strip ficam na action da *cena*, não na strip (`TextSequence` não tem `animation_data`).

`build_subtitle_timeline` é puro (não toca `bpy`) — é onde vive toda a lógica e é o que os testes exercitam.

### Comportamento
1. Parseia o `.srt` com regex: índice, timestamps (`HH:MM:SS,mmm`) e texto
2. Converte para frames com `round()` e soma `frame_offset` (o `main()` passa `speech_start + 1`, pois os timestamps do SRT são relativos ao início da narração)
3. Monta o timeline:
   - **Hold** — o fim de cada entrada é estendido até o início da próxima, limitado a `max_hold_seconds` além do seu próprio fim
   - **Sem overlap** — o fim é clampado ao início da próxima entrada
   - **Duração mínima** de 1 frame
   - **Merge** — entradas que caem no mesmo frame são concatenadas numa strip só
   - **Fade** — `fade_frames` só nas bordas de vão real (e na primeira/última strip), limitado a ⅓ da duração da strip
   - **Rise** — `rise_frames` para *toda* palavra, limitado a `duração - 1`
4. Cria uma text strip por spec, `blend_alpha = 1.0`, keyframes de opacidade só onde há fade
5. Anima a entrada: cada palavra nasce em `SUBTITLE_Y - rise_offset` e sobe até `SUBTITLE_Y` em `rise` frames, com interpolação `SINE`/`EASE_OUT` fixada explicitamente por `_set_easing` (keyframes novos herdariam a preferência do Blender de quem rodar)
6. Posicionamento de repouso: centralizado, `SUBTITLE_Y = 0.05` da altura

### Fade × Rise

São animações ortogonais e propositalmente têm alcances diferentes:

| | Fade (`blend_alpha`) | Rise (`location[1]`) |
|---|---|---|
| Onde aplica | só nas bordas de vão real + primeira/última | **toda** palavra |
| Por quê | fade entre palavras adjacentes lê como piscada | dá o "pop" por palavra sem tocar na opacidade |
| Cap | ⅓ da duração | `duração - 1` |

Num SRT de 12 palavras com uma pausa no meio: 4 curvas de `blend_alpha`, 12 de `location`.

### Config no template.json
```json
"subtitles": {
  "fade_frames": 3,
  "max_hold_seconds": 0.4,
  "rise_frames": 4,
  "rise_offset": 0.025
}
```
`fade_frames: 0` desliga o fade (corte seco); `rise_frames: 0` desliga a subida. `rise_offset` é fração da altura do frame (0.025 ≈ 48px em 1080×1920).

### Testes
`tests/test_subtitles.py` — 16 testes, marcados `no_db`. Carrega `edit_video.py` por path via `importlib`; não precisa de Blender nem de docker compose.

### Limitações conhecidas

- **Estilização não é configurável.** As text strips nascem do código via `new_effect()` e o script nunca define `strip.font`, `font_size`, `color`, `use_shadow` ou `use_outline`. Resultado: `font = None` (o Blender cai na fonte embutida no binário) e `font_size = 60`, o default — **3,1% da altura num frame de 1920px**, bem abaixo dos 7–10% usuais de vídeo vertical. Nada no `template.blend` influencia isso: o template não tem text strips nem datablock de fonte. Para tornar configurável, o caminho de menor atrito é `.ttf` na imagem (o Dockerfile já instala `fonts-dejavu-core` para o compositor de imagem) + `font_size`/`color`/contorno no bloco `subtitles` do `template.json`.
- Karaokê (frase fixa com a palavra atual destacada) exigiria strips sobrepostas ou material por palavra — não suportado

---

## 8. Storage — Download e Upload

**Funções:** `download_file()`, `upload_file()`  
**Arquivo:** `src/blender_worker/storage/client.py`  
**Status:** Implementado

### download_file
```python
def download_file(bucket: str, key: str, dest_path: str) -> None
```
- Usa `get_s3_client().download_file(bucket, key, dest_path)`
- Cria diretórios intermediários se necessário
- Levanta exception se o objeto não existe no MinIO

### upload_file
```python
def upload_file(bucket: str, key: str, src_path: str) -> None
```
- Usa `get_s3_client().upload_file(src_path, bucket, key)`
- Levanta exception se o arquivo local não existe

**Bucket padrão:** lido de `settings.CONFIG.storage.bucket` (configurado em `config.ini`)

---

## 9. Bootstrap e Configuração (`src/core`)

**Arquivos:** `src/core/config.py`, `src/core/logger.py`, `src/core/bootstrap.py`  
**Status:** Implementado

### Descrição
Módulo de infraestrutura responsável por inicializar o sistema antes de qualquer outro módulo ser carregado. É acionado automaticamente ao importar `from src.core import settings`.

### Fluxo de bootstrap
1. `load_dotenv()` — carrega o `.env` da raiz do projeto
2. `Settings.load()` — lê variáveis de ambiente e o arquivo `config.ini` (ou `config.prod.ini` se `ENV=prod`)
3. `log_setup()` — configura structlog + handlers de stdout e arquivo rotativo
4. Instala `sys.excepthook` para logar exceções não tratadas

### Objeto `settings`
Acessível via `from src.core import settings`. Atributos disponíveis:

| Atributo | Tipo | Origem |
|---|---|---|
| `ROOT_DIR` | `str` | Env var `ROOT_DIR` (obrigatório) |
| `ENV` | `str` | Env var `ENV` (padrão: `dev`) |
| `DEBUG` | `bool` | Env var `DEBUG` (padrão: `false`) |
| `CONFIG.<section>.<key>` | tipado | `config.ini` / `config.prod.ini` |

Exemplos de acesso: `settings.CONFIG.blender.bin`, `settings.CONFIG.storage.bucket`.

### Logger
- `dev`: saída colorida no stdout via `ConsoleRenderer`
- `prod`: JSON no stdout via `JSONRenderer`
- Arquivo rotativo: configurado por `[log]` no `config.ini`

---

---

## 10. Gerador de Imagem — Comment Card

**Rota:** `POST /images/render`  
**Arquivos:** `src/blender_worker/api/routes/images.py`, `src/blender_worker/image/composer.py`, `src/blender_worker/image/text.py`  
**Status:** Implementado

### Descrição

Gera uma imagem PNG no estilo "card de comentário": fundo branco com bordas arredondadas, avatar no topo e texto em Arial Bold com quebra de linha automática. O layout é controlado por um arquivo de guide JSON versionado no repositório. O PNG gerado é salvo no storage.

**O PNG tem sempre 1080 de largura** — a mesma do frame do TikTok — e altura variável conforme o texto. O card é uma caixa mais estreita posicionada dentro desse frame, deslocada para a esquerda; o restante fica transparente. A imagem é feita para ser aplicada sobre o vídeo em largura cheia, sem cálculo de posição do lado de quem consome.

### POST /images/render

**Request:**
```json
{
  "template": "comment_default",
  "text": "Este é o texto do comentário, pode ser longo.",
  "assets": {
    "avatar": "uploads/usuario123/avatar.png"
  },
  "output_key": "renders/custom/resultado.png"
}
```

| Campo | Obrigatório | Descrição |
|---|---|---|
| `template` | Sim | Nome do guide em `templates/` (ex: `"comment_default"` → `templates/comment_default.json`). 404 se não existir. |
| `text` | Sim | Texto do comentário. Quebrado automaticamente em múltiplas linhas com base na largura disponível. |
| `assets` | Não | `{"id": "minio_key"}` — substitui as MinIO keys padrão definidas no guide. Assets ausentes são ignorados silenciosamente pelo compositor. |
| `output_key` | Não | Key de destino no MinIO. Se omitido, gera `renders/<uuid>.png`. |

**Resposta:**
```json
// 201 Created
{
  "output_key": "renders/677b1bef-8c67-4a05-98b2-06b0f42d997d.png"
}

// 404 Not Found — template não existe
{ "detail": "Template 'nome_invalido' not found" }
```

### Guide JSON (template de layout)

Arquivo JSON versionado em `templates/`. Define o layout visual completo da imagem.

```json
{
  "version": "2.0",
  "canvas": { "width": 1080, "supersample": 2 },
  "card": {
    "width": 880,
    "offset": { "x": 60, "y": 0 },
    "gap": 18
  },
  "background": {
    "color": [255, 255, 255, 255],
    "radius": 24,
    "padding": { "top": 32, "right": 32, "bottom": 32, "left": 32 },
    "shadow": {
      "enabled": true,
      "color": [0, 0, 0, 110],
      "blur": 16,
      "spread": 0,
      "offset": { "x": 0, "y": 10 }
    }
  },
  "assets": [
    {
      "id": "avatar",
      "minio_key": "assets/perfil-azul.png",
      "size": { "width": 417, "height": 61 },
      "position": { "x": 0, "y": 0 }
    }
  ],
  "text": {
    "font_path": "assets/fonts/Arial-Bold.ttf",
    "size": 36,
    "color": [0, 0, 0, 255],
    "offset": { "x": 0, "y": 0 },
    "line_spacing": 0
  }
}
```

| Campo | Descrição |
|---|---|
| `canvas.width` | Largura **do PNG de saída**, fixa (1080 = frame do TikTok). Não é a largura do card |
| `canvas.supersample` | Renderiza tudo em N× e reduz uma vez com LANCZOS. `1` desliga |
| `card.width` | Largura do card, sempre menor que o canvas |
| `card.offset` | Posição do card dentro do canvas. `x` menor que o centro desloca para a esquerda |
| `card.gap` | Espaço vertical entre a linha de assets e o texto. Só cobrado se houver assets |
| `background.color` | RGBA (0–255 cada canal) |
| `background.radius` | Raio das bordas arredondadas em pixels |
| `background.padding` | Distância entre a borda do card e o conteúdo interno |
| `background.shadow.enabled` | Liga a sombra projetada. Padrão `false` |
| `background.shadow.color` | RGBA da sombra; o alpha controla a intensidade |
| `background.shadow.blur` | Desvio-padrão do desfoque em pixels; `0` dá uma cópia deslocada de borda dura |
| `background.shadow.spread` | Cresce (ou encolhe, se negativo) a sombra além do card antes do desfoque |
| `background.shadow.offset` | Para que lado a sombra cai. `y` positivo = luz vindo de cima |
| `assets[].id` | Identificador; usado para mapear ao `assets` do request |
| `assets[].size` | Tamanho que o asset ocupará. A imagem é redimensionada **para essas dimensões exatas**, sem preservar proporção — um aspecto diferente do arquivo achata a imagem sem erro nenhum |
| `assets[].position` | Posição dentro da linha de assets, no topo do card |
| `text.font_path` | Caminho do `.ttf`, relativo ao `ROOT_DIR` |
| `text.line_spacing` | Espaço **extra** entre linhas, somado à altura natural da linha da fonte (não é o total) |

### Canvas e card são coisas diferentes

O PNG tem **sempre** `canvas.width` de largura; só a altura varia com o texto. O card é uma caixa mais estreita desenhada em `card.offset`, e o resto do frame fica transparente. Assim a imagem é aplicada sobre o vídeo em largura cheia, sem cálculo de posição do lado de quem consome.

> Isto **substitui** o contrato da v1, em que `canvas.width` era a largura do card e o PNG crescia junto com a sombra.

**O card fica à esquerda do centro de propósito.** Com 1080 de canvas e 880 de card, centralizar daria `x = 100`; o template usa `60`, deixando 140px de goteira à direita — livre da barra de ações (curtir/comentar/compartilhar) do TikTok.

**Altura do card:** `padding.top + altura_da_linha_de_assets + gap + altura_do_texto + padding.bottom`. Os assets ficam **acima** do texto, então as duas alturas somam; lado a lado seria `max()`.

### Sombra projetada

Uma sombra desfocada e deslocada ocupa espaço **fora** da caixa do card. Na vertical o canvas cresce para acomodá-la (`margem_topo + altura_do_card + margem_base`). Na horizontal **não dá**: a largura é fixa, então o espaço tem que vir de `card.offset.x` e da goteira direita.

`shadow_margins(shadow) -> (left, top, right, bottom)` é a função pura que dá essa margem: `blur * 3 + spread`, ajustada pelo `offset` em cada lado (nunca negativa). O fator 3 vem de o `radius` do `GaussianBlur` do Pillow ser um desvio-padrão — ~3σ concentra >99% do peso do kernel, e o resto fica abaixo de um passo de alpha de 8 bits.

`check_card_fits(guide)` levanta `ValueError` se o card mais a sombra estourar a largura do canvas, em vez de deixar o desfoque cortar numa linha reta. É erro de autoria de template, pego uma vez — e já pegou um estouro real de 14px durante o desenvolvimento deste template. `test_shipped_template_fits_its_canvas` mantém a guarda.

A sombra é clipada pela silhueta do card (`ImageChops.subtract` contra uma máscara do rounded rect), como o `box-shadow` do CSS. Com o card branco opaco o efeito é invisível, mas a regra existe para qualquer `background.color` translucido.

### Antialiasing

Já havia AA antes de `canvas.supersample` existir: o texto é desenhado pelo FreeType, que antialiasa por conta própria, e `_rounded_rect` já desenhava os cantos em 4× antes de reduzir. O `supersample` renderiza o card inteiro em N× (com a fonte re-derivada via `font_variant`) e reduz uma vez com LANCZOS — uma passada de uniformidade em cima disso, não a origem do efeito. `supersample: 1` é uma escolha válida e mais rápida.

Os testes afirmam que o AA **está presente** nos dois ajustes, em vez de afirmar que o knob o cria.

### Pipeline interno

```
1. Carrega guide JSON do disco (templates/{template}.json)
2. Carrega fonte TTF (font_path do guide)
3. Para cada asset no guide:
   - Usa minio_key do request.assets[id] se fornecido, senão usa o padrão do guide
   - Baixa bytes do MinIO (asyncio.to_thread)
   - Falha silenciosa se não encontrar — compositor pula assets ausentes
4. Valida que o card + sombra cabem na largura do canvas (`check_card_fits`)
5. Calcula altura do card: padding + linha de assets + gap + texto quebrado + padding
6. Compõe imagem (Pillow), tudo em N× se `supersample > 1`:
   - Sombra: rounded rect na cor da sombra → GaussianBlur → clip pela silhueta do card
   - Rounded rect com supersampling 4× para bordas suaves
   - Assets redimensionados, na linha do topo do card
   - Texto renderizado linha por linha, abaixo dos assets
   - Redução final única com LANCZOS se `supersample > 1`
7. Upload do PNG para o storage (asyncio.to_thread)
8. Retorna output_key
```

### Observações

- Resposta é **síncrona** — a composição é rápida (Pillow, não Blender), não usa BackgroundTasks.
- O bucket MinIO precisa existir antes da primeira chamada. Não é criado automaticamente.
- Fonte padrão: `DejaVuSans.ttf` (instalada via `fonts-dejavu-core` no Dockerfile). Para desenvolvimento local fora do Docker: `sudo apt install fonts-dejavu-core`.

---

## Resumo de Status

| Feature | Status |
|---|---|
| `GET /health` | ✅ Implementado |
| `POST /jobs` + `GET /jobs/{id}` | ✅ Implementado |
| `POST /videos` + `GET /videos/{id}` | ✅ Implementado |
| `POST /templates` + `GET /templates/{id}` | ✅ Implementado |
| Worker pipeline completo | ✅ Implementado |
| `scripts/edit_video.py` | ✅ Implementado |
| `import_subtitles()` — legendas palavra por palavra | ✅ Implementado |
| `download_file()` / `upload_file()` | ✅ Implementado |
| `src/core` (config, logger, bootstrap) | ✅ Implementado |
| Migração inicial do DB | ✅ Gerada e aplicada |
| `POST /images/render` (comment card) | ✅ Implementado |
