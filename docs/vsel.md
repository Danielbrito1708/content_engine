# VSEL — referência do formato

Isto é **referência**: o quê existe no formato e o que cada peça significa. Não é o
racional de design — para "por que YAML e não JSON", "por que não MLT/OTIO", e a história de
cada decisão, ver `docs/edicao_declarativa.md`. Os dois documentos não se repetem de propósito:
este aqui não reexplica o "por quê", aquele não é o lugar de procurar "qual é o campo X".

**Vocabulário atual de clipe: `video`, `audio`, `image`, `subtitles` — quatro tipos, não
cinco.** `edicao_declarativa.md` ainda lista `text` como parte do vocabulário fechado e como
decisão em aberto; `text` não existe no schema implementado. Se você leu aquele documento
primeiro, é o ponto mais provável de carregar informação errada pra cá.

---

## 0. Escopo e status

Este documento descreve o formato como o código em `blender_worker/src/blender_worker/timeline/`
o implementa hoje — `schema.py` (shape), `expr.py` (mini-linguagem de expressões),
`resolver.py` (resolução de tempo), `payload.py` (serialização pro executor Blender).

**Status: Fase 2 escrita e verificada contra Blender 4.2.20 real — não ligada a `worker.py`.**
`template.json` no bucket é o que ainda renderiza de verdade em produção; nenhum job real
aponta pra VSEL ainda (`blender_worker/CLAUDE.md` § "VSEL — Declarative timeline resolver").
As rotas de validação e preview (§12 abaixo) existem e funcionam contra Blender real — o que
não existe é o pipeline de produção consumindo isso em vez do `template.json` legado.

Ver também `docs/edicao_declarativa.md` (design) e `blender_worker/CLAUDE.md` § VSEL
(implementação, testes, histórico de verificação).

---

## 1. Forma do documento

Seis chaves de topo, sempre nesta função:

```yaml
version: 2       # só 2 é aceito
canvas: {}       # dimensão, fps, e quando a timeline termina
inputs: {}       # o que o pipeline injeta (assets), por nome
flags: {}        # booleanos vindos do pipeline (hoje só hook_muted)
anchors: {}      # posições de tempo nomeadas, calculadas uma vez
tracks: []       # a timeline propriamente dita
```

| Bloco | O que é |
|---|---|
| `canvas` | `width`, `height`, `fps`, `tail`, `fallback_end` — ver §2 |
| `inputs` | o que o pipeline injeta, com `required` — referenciados como `$nome` — ver §3 |
| `flags` | booleanos vindos do pipeline (hoje só `hook_muted`) — ver §4 |
| `anchors` | expressões de tempo nomeadas, calculadas uma vez, em ordem de declaração — ver §5 |
| `tracks` | lista; cada uma com `channel`, `role` e `clips` — ver §6 |

`TimelineDoc` usa `extra="forbid"` neste nível — uma chave de topo que não é uma destas seis é
erro de validação, não passa em silêncio.

---

## 2. `canvas`

| Campo | Tipo | Default | Observação |
|---|---|---|---|
| `width` | int | obrigatório | |
| `height` | int | obrigatório | |
| `fps` | int | obrigatório | é o `frame_rate` usado em toda conversão `s` → frames |
| `tail` | str | `"0s"` | duração após o fim do conteúdo — ver §6. Só aceita literal de duração (`"0.5s"`, `"4f"`) ou aritmética entre literais; não pode referenciar `$anchor` nem `after($input)`, porque é resolvido com `anchors={}` e `inputs={}` |
| `fallback_end` | int | obrigatório | ⚠️ ver abaixo |

```yaml
canvas:
  width: 1080
  height: 1920
  fps: 30
  tail: 0.5s          # o último som precisa de espaço para terminar
  fallback_end: 900   # só usado se nada de `content` existir na timeline
```

⚠️ **`fallback_end` é um frame absoluto legado, e o único campo do formato que não passa pelo
deslocamento de origem (`+1`, ver §10).** Só é usado quando a timeline não tem nenhum clipe
`content` — nunca acontece no template real, existe por paridade com o parâmetro de fallback
que o código legado (`content_end_frame`) já tinha. Na prática, caminho morto.

---

## 3. `inputs`

`dict[str, InputSpec]`. Cada entrada:

| Campo | Tipo | Default |
|---|---|---|
| `type` | `video \| audio \| image \| srt` | obrigatório |
| `required` | bool | `true` |

```yaml
inputs:
  background: { type: video, required: true }
  music:      { type: audio, required: true }
  voice:      { type: audio, required: true }
  subtitles:  { type: srt,   required: true }
  hook:       { type: audio, required: false }
  card:       { type: image, required: false }
```

Um nome declarado aqui é referenciado em outros lugares como `$nome` — em `source:` de um
clipe, em `when_present:` de uma trilha, dentro de `after($nome)` numa expressão. **Mas nem
todo `$nome` significa a mesma coisa em todo lugar** — ver a tabela de desambiguação em §8,
é a fonte mais comum de confusão do formato inteiro.

Um input `required: true` ausente faz a resolução falhar com um erro claro
(`required input 'voice' was not supplied`) antes de tocar em qualquer clipe. Um `required:
false` ausente simplesmente não aparece — clipes que dependem dele (via `when_present` na
trilha) são pulados, sem erro.

---

## 4. `flags`

`dict[str, bool]`, default `{}`. Hoje só `hook_muted` é real — sobrescrito pelo pipeline por
parte, o resto do template só declara o default:

```yaml
flags:
  hook_muted: false   # o pipeline sobrescreve por parte
```

Duas formas de uso, e nenhuma delas passa por `expr.py`:

- **`when:` de um `Conditional`** (§5) — nome **sem** `$`: `when: hook_muted`.
- **`AudioClip.muted`** (§7) — nome **com** `$`: `muted: $hook_muted`. Aqui é resolvido por
  `resolve_flag_ref`, uma função separada que olha só em `flags`, nunca em `anchors` ou
  `inputs` — mesmo símbolo `$`, tabela de busca diferente (§8).

---

## 5. `anchors`

`dict[str, AnchorValue]`, default `{}`. `AnchorValue` é `str | Conditional` — uma expressão
solta, ou um `{when, then, else}`. Resolvidos **em ordem de declaração**, um de cada vez,
antes de qualquer trilha.

⚠️ **Anchors nunca veem `timeline_end`, sem exceção alguma** — não é "anchor usado só por
trilha `bed` pode". `resolve_timeline` resolve todos os anchors numa passada só, sempre com
`timeline_end=None`, antes de tocar em `content` ou em `bed`. Só uma expressão escrita direto
num campo de clipe de uma trilha `bed` (§6) vê `timeline_end` de verdade. Um anchor que
tentasse fatorar `timeline_end - 1s` pra reusar entre duas trilhas `bed` bate em `ExprError`
("timeline_end is only available while resolving bed tracks") sem essa distinção ser óbvia
de onde veio.

`Conditional`:

```yaml
card_end:
  when: hook_muted   # nome de flag, sem $
  then: after($hook)
  else: max($intro_floor, after($hook) + 0.3s)
```
(A chave YAML é `else:`; o atributo Python correspondente é `else_`, porque `else` é palavra
reservada — só importa se você for ler `schema.py` diretamente.)

Os três anchors reais do template de produção:

```yaml
anchors:
  # piso do template: a narração nunca começa antes disto quando o hook é tocado
  intro_floor: 3.0s

  # o card sai da tela — sem cauda quando o hook é mudo, porque não há dois
  # arquivos de áudio para separar
  card_end:
    when: hook_muted
    then: after($hook)
    else: max($intro_floor, after($hook) + 0.3s)

  # a narração entra — com o vídeo quando o hook é mudo, depois dele quando não é
  narration_start:
    when: hook_muted
    then: 0s
    else: max($intro_floor, after($hook) + 0.3s)
```

---

## 6. `tracks` e as três passadas

`Track`:

| Campo | Tipo | Default |
|---|---|---|
| `name` | str | obrigatório |
| `channel` | int | obrigatório — canal de strip do VSE, responsabilidade do autor não colidir |
| `role` | `content \| bed` | obrigatório |
| `when_present` | str (nome de input, sem `$`) | opcional |
| `clips` | lista de clipes (§7) | obrigatório |

A resolução acontece em três passadas, sempre nesta ordem:

1. **Anchors** resolvem (§5), sem `timeline_end`.
2. **Trilhas `content`** resolvem. O fim mais tardio entre todos os clipes `content` decide
   `timeline_end`: `timeline_end = ORIGIN + max(fim de cada clipe content) + tail`. Se não
   houver clipe `content` nenhum, `timeline_end = fallback_end + tail` (sem o `+ORIGIN` — ver
   ⚠️ em §2).
3. **Trilhas `bed`** resolvem — agora com `timeline_end` disponível pros seus próprios campos
   de clipe.

Ou seja: **`role: bed` é a declaração de "esta trilha não decide onde o vídeo termina"** — o
fundo e a música são `bed`; voz, legenda, hook e card são `content`.

`when_present: $nome` derruba a trilha inteira se aquele input não foi suprido
(`inputs.get(nome) is None`) — usado pelas trilhas opcionais `hook` e `card` do template real.
Sem erro, sem aviso: a trilha simplesmente não existe nessa resolução.

---

## 7. Tipos de clipe

Campos comuns a todo clipe (`ClipBase`):

| Campo | Tipo | Default | Observação |
|---|---|---|---|
| `type` | str | obrigatório | discrimina qual dos quatro tipos abaixo |
| `source` | str (`$nome`) | `null` | obrigatório em todo tipo exceto `subtitles`, que usa `sync_to` |
| `start` | expressão ou `Conditional` | `null` | |
| `duration` | `"source"` ou expressão | `"source"` | `"source"` é sentinela: "tão longo quanto o asset do `source`" — não é parseado por `expr.py` |
| `until` | expressão ou `Conditional` | `null` | se presente, tem prioridade sobre `duration` |
| `filters` | lista (§9) | `[]` | |

⚠️ **`ClipBase` usa `extra="allow"`, diferente de todo o resto do schema.** Um campo digitado
errado num clipe (`sytle:` em vez de `style:`, `voulme:` em vez de `volume:`) é **aceito em
silêncio** — a validação não pega, `POST /timelines/validate` (§12) devolve `ok: true`, e o
campo real simplesmente nunca é lido. Todo outro bloco do formato (`canvas`, `Track`,
`TimelineDoc`, o próprio topo do documento) usa `extra="forbid"` e rejeitaria o mesmo erro na
hora. Conferir a grafia dos campos de clipe é sua responsabilidade, não da validação.

### `video`

Campos além de `ClipBase`:

| Campo | Tipo | Default |
|---|---|---|
| `loop` | `"until_end"` ou `null` | `null` |
| `min_frames` | int | `2` |

`loop: until_end` repete o clipe até cobrir a timeline inteira — só faz sentido numa trilha
`bed`. `min_frames` é o piso de frames abaixo do qual o asset é rejeitado como "carregou sem
trilha de vídeo de verdade" (um arquivo assim carrega como 1 frame) — **configurável por
clipe**, não só a constante do módulo Python.

```yaml
- type: video
  source: $background
  start: 0s
  loop: until_end     # repete o clipe até cobrir a timeline
  min_frames: 2       # um arquivo sem trilha de vídeo carrega como 1 frame
```

### `audio`

| Campo | Tipo | Default |
|---|---|---|
| `volume` | float | `1.0` |
| `muted` | `str (\`$flag\`) \| bool \| null` | `null` |

`muted` **não é uma expressão** — bypassa `expr.py` inteiro. `$nome` aqui espelha
`flags[nome]` diretamente; um bool literal (`true`/`false`) também é aceito, pra um clipe
incondicionalmente mudo.

```yaml
- type: audio
  source: $hook
  start: 0s
  volume: 1.0
  muted: $hook_muted   # mudo, não removido: é o que mede o card
```

### `image`

| Campo | Tipo | Default |
|---|---|---|
| `y_position` | float | `0.5` |
| `fit` | `Literal["original"]` | `"original"` |
| `blend` | `Literal["alpha_over"]` | `"alpha_over"` |

⚠️ `fit` e `blend` têm default e podem ser omitidos, mas hoje **não aceitam nenhum outro
valor** — existem sintaticamente pra extensão futura, não como escolha real. Escrever
qualquer coisa diferente de `original`/`alpha_over` é erro de validação.

```yaml
- type: image
  source: $card
  start: 0s
  until: $card_end
  y_position: 0.5
  fit: original        # o PNG é autorado na largura exata do frame
  blend: alpha_over
  filters:
    - { type: fade_out, duration: 4f }   # sem fade in: ver nota abaixo
```

### `subtitles`

| Campo | Tipo | Default | Observação |
|---|---|---|---|
| `sync_to` | expressão ou `Conditional` | `null` | alias de `start` — lê melhor num clipe que segue outro |
| `hide_before` | expressão ou `Conditional` | `null` | |
| `max_hold` | str | `"0.4s"` | |
| `fade` | str | `"3f"` | |
| `rise` | `{frames: int, offset: float}` | `null` | não é o mesmo que `filters: [{type: rise}]` — ver ⚠️ abaixo |
| `style` | dict | `{}` | `font_size`, `y_position`, `color: [r,g,b,a]`, `outline: {color, width}` |

`style` aceita `font_size`, `y_position`, `color: [r,g,b,a]`, `outline: {color, width}` — lido
por `payload.py`, achatado pro shape que o executor Blender já esperava
(`resolve_subtitle_style`).

⚠️ **O `rise` deste clipe não tem relação com `filters: [{type: rise}]`.** São dois "rise"
sem parentesco — o campo aqui é o que de fato anima a entrada de cada palavra; o filtro de
mesmo nome é vocabulário morto (§9). Escrever a animação de subida como filtro em vez de como
este campo valida, não faz nada, e não avisa.

```yaml
- type: subtitles
  source: $subtitles
  sync_to: $narration_start
  hide_before:
    when: hook_muted
    then: $card_end     # a narração roda sob o card; não imprimir duas vezes
    else: 0s
  max_hold: 0.4s
  fade: 3f
  rise: { frames: 4, offset: 0.025 }
  style:
    font_size: 100      # teto, não valor fixo — palavra longa é reduzida
    y_position: 0.474
    color: [1.0, 1.0, 1.0, 1.0]
    outline: { color: [0.0, 0.0, 0.0, 1.0], width: 0.24 }
```

---

## 8. Mini-linguagem de expressões ("tempo simbólico")

Gramática informal (de `expr.py`):

```
expr := term (('+' | '-') term)*
term := NUMERO UNIDADE                     # "0.3s", "4f"
      | 'timeline_end'
      | '$' NOME                           # anchor já resolvido
      | NOME '(' expr (',' expr)* ')'      # after(...), max(...), min(...)
      | '(' expr ')'
```

| Construção | Significa | Exemplo |
|---|---|---|
| `Ns` | N segundos, convertido pra frames via `canvas.fps` | `"0.3s"` |
| `Nf` | N frames, literal | `"4f"` |
| `$nome` | valor de um **anchor** já resolvido | `$intro_floor` |
| `after($input)` | duração do **input**, em frames — caso especial, só válido como `after($ref)` direto | `after($hook)` |
| `max(a, b, ...)` | maior valor, qualquer número de argumentos | `max($intro_floor, after($hook) + 0.3s)` |
| `min(a, b, ...)` | menor valor, mesma aridade livre | |
| `timeline_end` | palavra-chave nua — só em escopo dentro de campo de clipe de trilha `bed` (nunca em anchor, §5) | |
| `a + b`, `a - b` | soma/subtração, encadeável, esquerda pra direita | `after($hook) + 0.3s` |
| `(...)` | agrupamento | |

Erros (`ExprError`, sempre em tempo de resolução, nunca no meio do render):

| Situação | Exemplo de mensagem |
|---|---|
| `$ref` desconhecido | `unknown reference $typo` |
| `after($input)` com input não suprido | `after($hook) — input 'hook' was not supplied` |
| `timeline_end` fora de escopo (anchor, ou clipe de trilha `content`) | `timeline_end is only available while resolving bed tracks — ...` |
| função desconhecida | `unknown function foo(...)` |
| erro de sintaxe (parêntese não fechado, token ilegível, sobra depois do fim) | varia, sempre cita o texto da expressão |

### Desambiguação de `$nome` — o ponto de maior confusão do formato

O símbolo `$` significa três coisas diferentes dependendo de onde aparece, e nada no formato
avisa qual delas é qual:

| Onde aparece | O que `$nome` procura | Passa por `expr.py`? |
|---|---|---|
| Dentro de uma expressão (`start:`, `until:`, dentro de `max()`/`after()`) | um **anchor** já resolvido | sim |
| Como argumento de `after(...)` — `after($nome)` | a **duração do input** `nome`, em frames | sim, caso especial |
| `source:`, `when_present:` de um clipe/trilha | um **input**, lido por um helper que só tira o `$` do texto (`_input_name`) | **não** — nem chega em `expr.py` |
| `muted:` de um clipe `audio` | uma **flag**, lida por `resolve_flag_ref` | **não** |

São quatro tabelas de busca diferentes (`anchors`, `inputs` via `after`, `inputs` via helper
de string, `flags`) atrás do mesmo caractere `$`. Ler o nome errado numa dessas posições não
dá erro de sintaxe — dá erro de resolução (`unknown reference`) ou, pior, passa em silêncio
se o helper de string não valida contra nada (`source`/`when_present`).

---

## 9. Filtros

Vocabulário declarado: `volume_fade`, `fade_in`, `fade_out`, `rise`, `outline`.

**Implementados de ponta a ponta:**

| Filtro | Em qual clipe | Campos |
|---|---|---|
| `volume_fade` | `audio` (trilha `bed`) | `to` (float, default `0.0`), `duration` (str, obrigatório), `anchor` (só `"timeline_end"` é legal — **sem default**, precisa ser escrito mesmo só havendo um valor possível) |
| `fade_out` | `image` | só `duration` é lido |

```yaml
filters:
  - type: volume_fade
    to: 0.0
    duration: 1.5s
    anchor: timeline_end   # contado do fim, nunca de um frame fixo
```

⚠️ **`fade_in`, `rise` e `outline` como entrada de `filters:` são vocabulário morto hoje.**
Declarados no schema (`VisualFilter`, `extra="allow"` — validam com qualquer campo extra),
mas confirmado lendo os quatro handlers de `apply_payload` em `scripts/edit_video.py`
(`_apply_video_clip`, `_apply_audio_clip`, `_apply_image_clip`, `_apply_subtitles_clip`):
nenhum lê `clip["filters"]` pra esses três tipos. `payload.py` só extrai `VolumeFadeFilter` e
o `duration` de um filtro `fade_out` — todo o resto da lista de filtros nunca chega no
executor. Escrever `filters: [{type: fade_in, duration: 4f}]` em qualquer clipe **valida,
não faz nada, e não avisa**.

Isso não é o mesmo que "campo opcional não implementado ainda de forma óbvia": `outline` e
`rise` já existem como **campos de primeira classe** em outro lugar do formato (`style.outline`
do clipe `subtitles`, e `SubtitlesClip.rise` — §7) — só não como filtro. É fácil, lendo o
`Literal["fade_in", "fade_out", "rise", "outline"]` do schema, tentar `filters: [{type: rise}]`
num clipe de legenda em vez do campo `rise:` de verdade. Os dois têm o mesmo nome e nenhuma
relação.

---

## 10. `0s` é o frame 1

O VSE do Blender começa no frame 1, não no 0. Toda posição relativa calculada como zero recebe
o mesmo deslocamento (`ORIGIN = 1`), aplicado **uma vez só**, no fim da resolução — não em
cada soma intermediária. Isso vale pra `start: 0s`, pro branch `else: 0s` de um `Conditional`,
pra qualquer anchor que resolva pra zero.

Na prática: se você escreveu `start: 0s` e o payload devolve `frame_start: 1`, não é bug — é
esse deslocamento. A única exceção é `canvas.fallback_end` (§2), que já é um frame absoluto e
não passa por ele.

---

## 11. Lacunas e comportamentos conhecidos

⚠️ **`duration: source` numa legenda é uma aproximação, não a duração real.** É tratado como
"tão longo quanto o input `voice`", nunca a timing palavra-por-palavra real do `.srt`. Uma
verificação contra Blender real mediu essa aproximação ~10 frames curta num caso — a última
palavra, segurada por `max_hold`, terminou depois do fim medido aqui. O executor Blender
(`apply_payload`) corrige isso re-derivando o fim real a partir das strips, mas o resolvedor
puro (o que roda em `POST /timelines/validate`, milissegundos, sem Blender) não sabe disso.

⚠️ **Não existe eixo pra "este input opcional está totalmente ausente".** O template real
só ramifica seus anchors em `hook_muted` — os dois branches chamam `after($hook)`. Uma
resolução sem o input `hook` suprido de jeito nenhum levanta `TimelineResolutionError`, não
degrada graciosamente. Faltaria um segundo eixo de condicional que o formato não tem hoje.

⚠️ **Um clipe `content` cujo fim resolvido vem antes do início é `warning`, não erro**, em
`POST /timelines/validate` — mensagem no formato `track 'X' clip 'Y': ends before it starts
(frame_start=A, frame_end=B)`. Deliberadamente não-fatal: por causa da aproximação acima, um
template correto pode parecer quebrado sob números de teste digitados à mão. Só dispara em
clipes `content` — um clipe `bed` nunca tem `frame_end` resolvido (é aberto por construção,
loop ou fade), então a checagem não tem o que comparar ali.

⚠️ Ver também §2 (`fallback_end`, caminho essencialmente morto), §7 (`extra="allow"` em
clipe — typo passa em silêncio), §9 (`fade_in`/`rise`/`outline` como filtro — vocabulário
morto).

---

## 12. Como verificar o que você escreveu

**`POST /timelines/validate`** (`blender_worker`, porta 8001) — sem Blender, sem asset real.
Corpo:
```json
{
  "template": "<texto YAML>",
  "inputs": {"background": 10, "music": 120, "voice": 15, "subtitles": 15, "hook": 3, "card": 0},
  "flags": {"hook_muted": false}
}
```
`inputs` é duração em **segundos**, digitada à mão — mesmo um asset sem duração própria (uma
imagem, referenciada só via `until:`) precisa de um valor-placeholder pra contar como
"suprido" (`0` serve). Resposta sempre `200` — erro de conteúdo do template vai em
`ok`/`errors`, nunca em status HTTP, porque a rota é pensada pra ser chamada a cada tecla
digitada. Erros carregam a trilha/clipe/anchor de origem na mensagem (§5, §7 já mostram o
formato).

**`GET /timelines/schema`** — `TimelineDoc.model_json_schema()` cru. Útil pra autocomplete de
estrutura num editor; **todo campo de tempo é tipado como `str` puro** — não dá autocomplete
pra dentro da gramática de §8.

**`POST /timelines/preview/frame`** e **`POST /timelines/preview/clip`** — exigem `video_id`
(assets já no bucket do pipeline) e `template_id` (só o `.blend` é usado). Devolvem PNG/MP4
direto no corpo da resposta, sem upload em bucket. Diferente de `/validate`: erro aqui é
status HTTP de verdade (`404`/`422`/`502`), porque é ação explícita, não checagem a cada
tecla. `preview/clip` aceita `resolution_percentage` (opt-in — o ganho de velocidade não está
medido) e `start_s`/`duration_s` (15s de janela por padrão).

Existe um app web companheiro, **`declarative_editor`** (projeto separado, fora deste
monorepo), que empacota esse loop inteiro numa UI de editor. Não é necessário pra usar nada
disto — este documento vale sozinho pra quem só tem `curl`.

---

## 13. Exemplo completo

`blender_worker/templates_v2/default.yaml` — o template de produção reescrito neste formato,
testado por equivalência contra o pipeline legado (`tests/test_timeline_resolver.py`:
resolve pros mesmos números de frame que `edit_video.intro_frames` /
`content_end_frame` / `background_repeats` / `music_fade_start` produzem, nos dois modos de
hook). Cada bloco já apareceu anotado nas seções acima; aqui está inteiro, sem cortes:

```yaml
# Fase 1 do formato descrito em docs/edicao_declarativa.md — reproduz, em
# tempo (não em pixels), o que blender_worker/template.json hoje descreve
# através de seis canais escondidos dentro de scripts/edit_video.py.
#
# tests/test_timeline_resolver.py prova que este arquivo resolve para os
# mesmos números de frame que edit_video.intro_frames / content_end_frame /
# background_repeats / music_fade_start produzem hoje, nos dois modos de
# hook. Enquanto esse teste for verde, este arquivo é equivalente ao
# template.json publicado — não a sua substituição em produção.

version: 2

canvas:
  width: 1080
  height: 1920
  fps: 30
  tail: 0.5s          # o último som precisa de espaço para terminar
  fallback_end: 900   # só usado se nada de `content` existir na timeline

inputs:
  background: { type: video, required: true }
  music:      { type: audio, required: true }
  voice:      { type: audio, required: true }
  subtitles:  { type: srt,   required: true }
  hook:       { type: audio, required: false }
  card:       { type: image, required: false }

flags:
  hook_muted: false   # o pipeline sobrescreve por parte

anchors:
  # piso do template: a narração nunca começa antes disto quando o hook é tocado
  intro_floor: 3.0s

  # o card sai da tela — sem cauda quando o hook é mudo, porque não há dois
  # arquivos de áudio para separar
  card_end:
    when: hook_muted
    then: after($hook)
    else: max($intro_floor, after($hook) + 0.3s)

  # a narração entra — com o vídeo quando o hook é mudo, depois dele quando não é
  narration_start:
    when: hook_muted
    then: 0s
    else: max($intro_floor, after($hook) + 0.3s)

tracks:
  - name: fundo
    channel: 1
    role: bed
    clips:
      - type: video
        source: $background
        start: 0s
        loop: until_end     # repete o clipe até cobrir a timeline
        min_frames: 2       # um arquivo sem trilha de vídeo carrega como 1 frame

  - name: musica
    channel: 2
    role: bed
    clips:
      - type: audio
        source: $music
        start: 0s
        volume: 0.2
        filters:
          - type: volume_fade
            to: 0.0
            duration: 1.5s
            anchor: timeline_end   # contado do fim, nunca de um frame fixo

  - name: hook
    channel: 5
    role: content
    when_present: $hook
    clips:
      - type: audio
        source: $hook
        start: 0s
        volume: 1.0
        muted: $hook_muted   # mudo, não removido: é o que mede o card

  - name: card
    channel: 6
    role: content
    when_present: $card
    clips:
      - type: image
        source: $card
        start: 0s
        until: $card_end
        y_position: 0.5
        fit: original        # o PNG é autorado na largura exata do frame
        blend: alpha_over
        filters:
          - { type: fade_out, duration: 4f }   # sem fade in: ver nota abaixo

  - name: voz
    channel: 3
    role: content
    clips:
      - type: audio
        source: $voice
        start: $narration_start
        volume: 1.0

  - name: legendas
    channel: 4
    role: content
    clips:
      - type: subtitles
        source: $subtitles
        sync_to: $narration_start
        hide_before:
          when: hook_muted
          then: $card_end     # a narração roda sob o card; não imprimir duas vezes
          else: 0s
        max_hold: 0.4s
        fade: 3f
        rise: { frames: 4, offset: 0.025 }
        style:
          font_size: 100      # teto, não valor fixo — palavra longa é reduzida
          y_position: 0.474
          color: [1.0, 1.0, 1.0, 1.0]
          outline: { color: [0.0, 0.0, 0.0, 1.0], width: 0.24 }
```

---

## 14. Ver também

- `docs/edicao_declarativa.md` — racional de design, fases de implementação, decisões em
  aberto.
- `blender_worker/CLAUDE.md` § "VSEL — Declarative timeline resolver" e § "Fase 3" — módulos,
  testes, status de verificação contra Blender real.
- `declarative_editor` (projeto separado, fora deste monorepo) — editor web com validação
  ao vivo e preview de frame/clipe.
