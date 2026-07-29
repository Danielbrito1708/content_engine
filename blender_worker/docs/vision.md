# Vision — blender_worker

## Visão Geral

`blender_worker` é um gerador automático de vídeos pessoal. Ele recebe comandos via API REST, monta vídeos no Blender a partir de templates pré-configurados, e salva o resultado no MinIO com metadados no Postgres. O sistema foi feito para eliminar edição manual repetitiva — dado um conjunto de assets (vídeo, áudio, legendas), o worker produz um arquivo `.blend` editado e pronto para renderização sem intervenção humana.

## Problema

Criar vídeos com estrutura recorrente (intro, fala, música de fundo, legendas animadas) exige edição manual no Blender a cada vez. O objetivo é automatizar completamente esse pipeline: descrever o vídeo como dados e deixar o sistema montar tudo.

## Fluxos Principais

### Fase 1 — Montagem (em escopo)

1. Cliente envia `POST /jobs` com `video_id` e `template_id`
2. Sistema busca os metadados do vídeo no Postgres e os assets no MinIO:
   - Arquivo de vídeo de fundo
   - Áudio de música
   - Áudio de voz
   - Legendas (`.srt`)
3. Sistema baixa o template do MinIO:
   - `template.blend` — arquivo base do Blender
   - `template.json` — configuração de timing (frames de intro, fala, música, etc.)
4. Script Python roda dentro do Blender e monta as trilhas no VSE:
   - Vídeo de fundo no canal 1
   - Música no canal 2
   - Voz no canal 3
   - Legendas animadas no canal 4 (geradas a partir do `.srt`)
5. Arquivo `.blend` resultante é salvo no MinIO em `outputs/{job_id}.blend`
6. Metadados do job são atualizados no Postgres
7. Cliente faz polling em `GET /jobs/{id}` até `status = completed`

### Fase 2 — Renderização (fora do escopo por ora)

- Fila separada que baixa o `.blend`, roda o render do Blender e sobe o vídeo final no MinIO.
- O `.blend` é preservado para permitir edição manual posterior se necessário.

## Entidades

### Video
Representa o conteúdo de um vídeo específico. Contém os ponteiros para todos os assets no MinIO e metadados estruturados.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | Identificador principal — referenciado pelo job |
| `video_file_key` | string | MinIO key do arquivo de vídeo de fundo |
| `music_key` | string | MinIO key do áudio de música |
| `voice_key` | string | MinIO key do áudio de voz |
| `subtitle_key` | string | MinIO key do arquivo `.srt` |
| `metadata` | JSONB | Título, duração, fps, resolução, etc. |
| `created_at` | timestamp | — |

### Template
Define a estrutura de montagem. Reutilizado em múltiplos jobs.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | — |
| `name` | string | Nome descritivo |
| `blend_key` | string | MinIO key do arquivo `.blend` base |
| `json_key` | string | MinIO key do arquivo de timing (`.json`) |
| `created_at` | timestamp | — |

**Formato do `template.json`:**
```json
{
  "frame_rate": 30,
  "frame_end": 900,
  "narration": {
    "rate": "+15%"
  },
  "channels": {
    "video": 1,
    "music": 2,
    "voice": 3,
    "subtitles": 4
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

O bloco `music` é opcional (padrão 1,5s no código) e define o fade da trilha, contado **a partir do último frame** — não há seção de outro, o vídeo termina na última palavra da narração. As chaves `outro_start`, `outro_end` e `music_fade_out` foram removidas: nenhuma era lida, e a última fazia a trilha decair pelo vídeo inteiro. Ver a seção "O vídeo não tem finalização" no `docs/vision.md` da raiz.

O bloco `narration` é opcional e **não é consumido pelo blender_worker** — ele existe no `template.json` porque a velocidade da narração é uma decisão de design do template, junto com tipografia e timing. Quem lê é o orchestrador, via `GET /templates/{id}/config`, antes de chamar o `tts_service`. Templates sem o bloco continuam válidos.

### Job
Representa uma tarefa de montagem. Criado por requisição e processado em background.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | — |
| `video_id` | UUID FK | Referência ao Video |
| `template_id` | UUID FK | Referência ao Template |
| `status` | enum | `pending` → `running` → `completed` / `failed` |
| `output_key` | string | MinIO key do `.blend` gerado (preenchido ao completar) |
| `params` | JSONB | Overrides opcionais de parâmetros |
| `error` | text | Mensagem de erro se `failed` |
| `created_at` / `updated_at` | timestamp | — |

## Legendas Animadas

O Blender não tem suporte nativo para legendas animadas. A solução é uma função customizada `import_subtitles(srt_path, timing)` que:

1. Parseia o arquivo `.srt` (índice, timestamps, texto)
2. Para cada entrada, cria um text strip no VSE (canal 4)
3. Aplica keyframes de opacidade para animação de entrada/saída
4. Posiciona cada texto no frame correto com base no timestamp do `.srt`

## Gerador de Imagens (Comment Card)

Pipeline independente do Blender para gerar imagens estáticas no estilo "card de comentário". Não usa DB — é uma operação síncrona e rápida.

**Fluxo:**
1. Cliente envia `POST /images/render` com nome do template, texto e MinIO keys dos assets
2. Sistema carrega o guide JSON do disco (`templates/`)
3. Baixa os assets do MinIO, compõe a imagem com Pillow (rounded rect + assets + texto)
4. Sobe o PNG para o MinIO e retorna a key

**Componentes:**
- `templates/*.json` — guides de layout versionados no repositório
- `src/blender_worker/image/text.py` — word wrap e cálculo de altura
- `src/blender_worker/image/composer.py` — compositor Pillow

## Integrações Externas

- **MinIO** — armazenamento de todos os arquivos (assets, templates, outputs, PNGs gerados)
- **PostgreSQL** — metadados e estado dos jobs
- **Blender CLI** — montagem via subprocess (`blender -b template.blend -P script.py -- config.json`)
- **Pillow** — geração de imagens estáticas (comment cards)
- **API REST** — interface principal; futuramente um CLI pode ser adicionado

## Fora do Escopo

- Autenticação / autorização (projeto pessoal)
- Interface visual / dashboard
- Multi-tenancy
- Múltiplas versões do Blender (versão única definida no Dockerfile)
- Fila de renderização — Fase 2, separada
- Cancelamento de jobs em andamento
- Retry automático
- Webhook/callback (polling é suficiente)
