# Agendamento — tiktok_poster

Documentação do algoritmo de agendamento de slots no Buffer, composição de captions com hashtags, e configuração de storage para produção.

---

## Algoritmo de slots (`buffer/scheduler.py`)

**Função:** `next_available_slot(pending_posts, posts_per_day, preferred_times, queue_limit) -> datetime | None`

### Entrada

| Parâmetro | Tipo | Origem |
|---|---|---|
| `pending_posts` | `list[dict]` | `BufferClient.get_pending_posts()` — posts com `due_at` em Unix timestamp |
| `posts_per_day` | `int` | `config.ini [posting] posts_per_day` (padrão: 2) |
| `preferred_times` | `list[str]` | `config.ini [posting] preferred_times` (padrão: `["08:00", "20:00"]`) em UTC |
| `queue_limit` | `int` | `config.ini [posting] buffer_queue_limit` (padrão: 10) |

### Lógica passo a passo

1. **Verificação de fila cheia:** se `len(pending_posts) >= queue_limit`, retorna `None` imediatamente.
2. **Mapeamento de slots ocupados:** agrupa os posts pendentes por data (`dict[date, set[time]]`).
3. **Varredura de dias:** itera dia por dia a partir de hoje (até 60 dias), verificando cada `preferred_time`.
4. **Candidato válido:** um slot é aceito se estiver no futuro E o horário não estiver ocupado naquele dia.
5. **Retorno:** o primeiro candidato válido encontrado como `datetime UTC`.

### Exemplo numérico

Configuração padrão: `posts_per_day=2`, `preferred_times=["08:00", "20:00"]`, `queue_limit=10`.

**Cenário:** hoje é quinta-feira 07/05/2026 às 15:00 UTC. Posts pendentes:
```
07/05 08:00 ← já passou
07/05 20:00 ← futuro, mas já ocupado
08/05 08:00 ← futuro, mas já ocupado
```

**Varredura:**
```
07/05 08:00 → no passado, descartar
07/05 20:00 → futuro mas ocupado, descartar
08/05 08:00 → futuro mas ocupado, descartar
08/05 20:00 → futuro e livre → RETORNAR 2026-05-08T20:00:00Z
```

**Cenário de fila cheia:** se já houver 10 posts pendentes, `next_available_slot` retorna `None` e o endpoint responde `429 buffer_queue_full`.

---

## Composição de captions (`hashtags/selector.py`)

### Seleção de hashtags

**Função:** `select_hashtags(hints, mandatory, pool, max_total) -> list[str]`

**Prioridade (ordem de inclusão):**
1. Obrigatórias (`config.ini [hashtags] mandatory`) — sempre incluídas primeiro
2. Hints do LLM (`classification.hashtag_hints`) — relevantes ao conteúdo
3. Pool de fallback (`hashtags.json`) — preenchem até `max_total`

Deduplicação preserva a ordem. Total limitado por `max_total` (padrão: 8).

**Exemplo:**
```
mandatory   = ["#tiktokbrasil", "#fyp"]
hints       = ["#ciencia", "#curiosidades", "#efeitompemba"]
pool        = ["#viral", "#aprenda", "#sabia", "#foryou"]
max_total   = 8

resultado   = ["#tiktokbrasil", "#fyp", "#ciencia", "#curiosidades", "#efeitompemba", "#viral", "#aprenda", "#aprenda"]
             (limitado a 8: "#tiktokbrasil", "#fyp", "#ciencia", "#curiosidades", "#efeitompemba", "#viral", "#aprenda", "#sabia")
```

### Composição da caption

**Função:** `compose_caption(cta, hashtags, part_number, total_parts) -> str`

Formato gerado:

```
# Se total_parts == 1:
{cta}

{hashtags separados por espaço}

# Se total_parts > 1:
Parte {part_number}/{total_parts} | {cta}

{hashtags separados por espaço}
```

**Exemplo com 2 partes:**
```
Parte 1/2 | Comenta o que você faria no lugar dela 👇

#tiktokbrasil #fyp #drama #relacionamentos #verdade #historia #viral #storytime
```

### Pool de hashtags (`hashtags.json`)

Arquivo versionado no repositório, editável sem deploy. Estrutura:

```json
{
  "pool": [
    "#viral",
    "#foryoupage",
    "#aprenda",
    "#sabia",
    "#incrivel"
  ]
}
```

Para adicionar hashtags sazonais ou de tendência, editar `hashtags.json` e reiniciar o container.

---

## URL pública do vídeo

O Buffer precisa de uma URL HTTP acessível publicamente para baixar o vídeo antes de publicar.

### MinIO local (dev)

URLs pré-assinadas geradas pelo MinIO local **não são acessíveis externamente**. O Buffer não consegue baixar o vídeo.

**Solução para dev:** usar ngrok ou similar para expor o MinIO, ou testar apenas até a geração do vídeo (sem agendamento real).

### Cloudflare R2 (produção)

Configurar `R2_PUBLIC_URL` com a URL do domínio público do bucket:

```env
R2_PUBLIC_URL=https://pub-da0042d11c5c4bf9b71284364582faa2.r2.dev
```

Quando definida, `generate_presigned_url()` retorna `{R2_PUBLIC_URL}/{key}` diretamente, sem gerar URL pré-assinada. O bucket deve ter acesso público habilitado no dashboard da Cloudflare.

**Sem `R2_PUBLIC_URL`:** a função tenta gerar uma URL pré-assinada via boto3. R2 suporta URLs pré-assinadas, mas elas exigem que o bucket permita acesso público para funcionar — caso contrário, retornam 403.

### Comparativo

| Situação | Comportamento | Adequado para produção? |
|---|---|---|
| MinIO local, sem `R2_PUBLIC_URL` | URL pré-assinada inacessível externamente | Não |
| R2 sem `R2_PUBLIC_URL` | URL pré-assinada gerada — pode retornar 403 se bucket privado | Depende da configuração |
| R2 com `R2_PUBLIC_URL` | URL pública direta, sempre acessível | Sim |

---

## Configuração do Buffer

O serviço usa a API GraphQL do Buffer (`https://api.buffer.com`).

### Autenticação

Token Bearer configurado via `BUFFER_ACCESS_TOKEN`. O token pode ser obtido em [buffer.com/app/account/tokens](https://buffer.com/app/account/tokens).

### Channel ID

`BUFFER_PROFILE_ID` é o ID do canal TikTok no Buffer. Visível na URL ao selecionar o canal em [publish.buffer.com/channels](https://publish.buffer.com/channels).

### Organização

`BUFFER_ORG_ID` é opcional — se omitido, é buscado automaticamente em `account { organizations { id } }` na primeira requisição e cacheado em memória.

### Limite de fila no plano gratuito

O Buffer Free permite no máximo 10 posts agendados por canal. Quando a fila atinge esse limite, o endpoint retorna `429`. Para aumentar o limite, fazer upgrade para Buffer Essentials ou superior.

---

## O que ainda falta implementar

- **Confirmação de publicação (`posted_at`)**: o campo `posted_at` existe no modelo do orchestrador mas nunca é preenchido. Seria necessário implementar polling pós-agendamento no Buffer para confirmar que o post foi efetivamente publicado.
- **Validação do `MutationError`**: `BufferClient.create_post()` repassa o retorno do Buffer sem validar se a resposta contém `MutationError` (ex: vídeo inacessível, formato inválido). O orchestrador marca o run como `scheduled` mesmo em caso de falha silenciosa.
- **Retentar em fila cheia**: atualmente `429` é propagado para o orchestrador como erro. Uma fila interna com retry automático após N minutos seria mais resiliente.
- **Suporte a múltiplos canais**: `BUFFER_PROFILE_ID` é uma variável única — não suporta postar em múltiplos perfis TikTok ou em outras redes (Instagram Reels, YouTube Shorts).
