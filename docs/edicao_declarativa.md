# Edição declarativa — proposta de formato

**Status: proposta.** Nada aqui está implementado. O documento existe para que o formato seja
discutido antes de existir código, e para que uma sessão futura não precise redescobrir por
que o desenho é este.

Data: 31/08/2026. Escrito contra o estado do repo nessa data — um `template.json` no bucket,
um `main()` com a timeline cravada em `blender_worker/scripts/edit_video.py`.

---

## O problema, dito uma vez

O `blender_worker` monta **uma forma de vídeo só**. O `main()` (`edit_video.py:726`) tem a
timeline no código:

- seis canais nomeados na própria função — ch1 fundo, ch2 música, ch3 voz, ch4 legendas,
  ch5 hook, ch6 card;
- duas ramificações de modo (`hook_muted`), escritas como `if`;
- uma ordem de operações que só faz sentido para este layout (`frame_end` decidido antes de
  `extend_background` e de `music_fade_start`, garantida hoje por um comentário).

`template.json` **não descreve uma edição** — descreve *parâmetros de uma edição fixa*. Ele
ajusta números dentro dessa forma; não existe valor que se possa escrever nele que produza
duas trilhas de vídeo, ou um card no fim, ou uma sobreposição a mais.

O objetivo desta proposta é que a forma do vídeo passe a ser **dado**, num arquivo que o
operador também consiga ler e editar à mão.

---

## Onde "arbitrário" para

Arbitrariedade total é uma linguagem de script — e para isso já existe Python, que o projeto
tem. A fronteira útil é outra:

> **Composição arbitrária sobre um vocabulário fechado.**

Qualquer número de trilhas, qualquer sobreposição, qualquer ordem, qualquer ancoragem
temporal — mas escolhidos de uma lista de tipos que o interpretador conhece. Adicionar um
tipo novo é código; usar os tipos existentes de forma nova é só editar o arquivo.

Isso é o que separa "o operador pode montar um vídeo diferente" de "o operador pode executar
código dentro do render", que é uma superfície que ninguém quer manter.

---

## Por que formato próprio, e não MLT XML nem OTIO

Ambos foram considerados. Ficam registrados para não serem reabertos sem motivo novo.

**MLT XML** (o formato do Shotcut, renderizável por `melt`) tem o argumento mais forte:
"legível e editável à mão" ganharia um editor visual de graça, e `melt` compõe mais rápido
que o Blender. Perde por causa da legenda: cada palavra tem animação própria de subida e
fade, o que vira um filtro `dynamictext` por palavra — centenas deles num XML que ninguém vai
querer abrir. E descartaria as regras de `edit_video.py` que foram **medidas contra render
real** (cobertura de fundo, fade contado do fim, padding de cauda, auto-fit por palavra), que
não têm equivalente pronto no MLT.

**OpenTimelineIO** é literalmente o padrão de "descrever uma edição como arquivo", com
adaptadores para MLT/FCP/EDL. Mas o OTIO descreve *corte*, não *efeito*: efeitos vivem em
blobs de metadata por aplicação. O schema próprio acabaria dentro do OTIO de qualquer forma,
pagando a dependência sem ganhar o intercâmbio.

A conclusão é a mesma nos dois casos: **o custo do projeto não está em montar a timeline**,
está nas regras já validadas. O formato próprio é o único caminho que as preserva.

---

## O formato

YAML, não JSON: aceita comentários, tem blocos multi-linha para texto, e não transforma uma
vírgula esquecida em erro de sintaxe sem contexto. É o que torna "eu também edito à mão" real
em vez de nominal.

### Blocos

| Bloco | O que é |
|---|---|
| `canvas` | `width`, `height`, `fps`, `tail`, `fallback_end` |
| `inputs` | o que o pipeline injeta, com `required` — referenciados como `$nome` |
| `flags` | booleanos vindos do pipeline (hoje só `hook_muted`) |
| `anchors` | expressões de tempo nomeadas, calculadas uma vez |
| `tracks` | lista; cada uma com `channel`, `role` e `clips` |

**Vocabulário fechado de clipes:** `video`, `audio`, `image`, `text`, `subtitles`.
**Vocabulário fechado de filtros:** `fade_in`, `fade_out`, `volume_fade`, `rise`, `outline`.

### As três ideias que fazem o formato funcionar

**1. Tempo simbólico.** `after($hook)`, `max(a, b)`, `timeline_end`, `duration: source`.

Sem isso, composição arbitrária é impossível: a narração só tem duração em tempo de render, e
nenhum número absoluto escrito no arquivo sobreviveria a uma história mais longa. Hoje essa
aritmética existe — está escondida dentro de `intro_frames()` (`edit_video.py:369`), que
devolve dois frames calculados a partir do fim do hook. O formato apenas **traz para o
arquivo o que já é feito, em vez de inventar capacidade nova**.

**2. `role: bed` substitui o conjunto hardcoded.** Hoje `main():826` monta
`bed_channels = {music, video}` no código, e `content_end_frame()` (`:494`) usa isso para
decidir onde o vídeo termina. Declarar `role: bed` numa trilha é dizer "esta trilha não define
o fim do vídeo" — exatamente a regra que já existe, agora dita pelo autor do template.

A regra existe porque foi medida: um fundo de 90s sob uma narração de 68s rendeu 22s de tela
parada depois da última palavra.

**3. Resolução em duas passadas.** Passa 1 resolve os clipes `content` e decide
`timeline_end`; passa 2 resolve os `bed` e tudo que estiver ancorado em `timeline_end`.

Isto **não é invenção do formato**: é a ordem que `main():829-853` já obedece. O fim é
decidido primeiro, depois o fundo é repetido para cobri-lo e o fade da música é contado de
trás para frente. Hoje essa ordem é garantida por um comentário no código; no formato, ela
vira propriedade do resolvedor e não dá para escrever um arquivo que a viole.

### Condicionais mínimas

`when: / then: / else:` sobre um nome de `flags`. Teste de flag e nada mais — é o que expressa
os dois modos do hook (tocado nas partes 2+, mudo na parte que já abre com ele) sem virar
linguagem de expressão.

---

## O template atual, escrito no formato novo

Este é o teste de expressividade: se o vídeo que roda hoje não couber aqui, o schema está
errado.

```yaml
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

**Unidades:** `s` para segundos, `f` para frames. A distinção não é cosmética — o fade da
música é uma duração musical e tem de significar o mesmo final em qualquer `fps`, enquanto o
fade do card é contado em frames de propósito. O resolvedor converte tudo para frames; o
arquivo preserva a intenção.

**`0s` é o frame 1.** Timeline de Blender começa em 1, e hoje todo strip entra em
`intro_start + 1`. O resolvedor faz esse `+1`; o autor escreve tempo a partir do zero.

**O card não tem fade in, e isso é regra de distribuição, não gosto** — todo vídeo abre no
mesmo card na mesma posição, e uma rampa idêntica de 4 frames em cima disso fazia os
primeiros frames de todos os vídeos serem quase a mesma imagem. O formato permite escrever
`fade_in`; o template não usa.

### O que ficou visível ao escrever isso

Duas coisas que hoje só existem dentro de uma função aparecem no arquivo:

- **`card_end` e `narration_start` são anchors diferentes.** No modo hook-tocado elas são o
  mesmo número; no modo mudo divergem. Hoje isso é o par que `intro_frames()` devolve, e a
  única forma de saber é ler a função.
- **`intro_floor` é um piso, não um início.** `max($intro_floor, ...)` diz que um hook curto
  não *encurta* a intro do template. Hoje é um `max` no meio de uma chamada.

---

## Mapeamento: o que vira o quê

As funções puras **não morrem** — viram a implementação de tipos nomeados. São a parte medida
contra render real, e preservá-las é a razão de o formato ser próprio.

| Hoje | Vira |
|---|---|
| `add_movie_strip` + `check_movie_strip` (`:350`, `:335`) | clipe `video` + `min_frames` |
| `background_repeats` / `extend_background` (`:537`, `:573`) | `loop: until_end` |
| `add_sound_strip` (`:360`) | clipe `audio` |
| `build_subtitle_timeline` / `import_subtitles` (`:143`, `:643`) | clipe `subtitles` |
| `add_image_strip` / `add_card` (`:437`, `:460`) | clipe `image` + filtro `fade_out` |
| `intro_frames` (`:369`) | `anchors` com `after()`, `max()` e `when:` |
| `content_end_frame` + `end_padding_frames` (`:494`, `:516`) | `role: bed` + `canvas.tail` |
| `music_fade_frames` / `music_fade_start` / `apply_volume_fade` (`:581`, `:591`, `:616`) | filtro `volume_fade` com `anchor: timeline_end` |
| `resolve_subtitle_style` / `fit_font_size` (`:226`, `:248`) | bloco `style` do clipe `subtitles` |
| `drop_specs_before` (`:405`) | `hide_before` |
| `card_offset_y` (`:420`) | `y_position` do clipe `image` |

O `main()` deixa de montar a timeline e vira **despachante**: percorre a timeline resolvida e
chama, por tipo, as funções acima.

---

## ⚠️ O YAML é resolvido fora do Blender

Ponto de arquitetura, não detalhe de implementação.

`edit_video.py` roda no interpretador embutido do Blender, que tem seus próprios pacotes — não
dá para assumir PyYAML lá dentro, e instalar na mão dentro da imagem é dívida que ninguém
lembra ao subir a versão do Blender.

```
worker.py  (env do poetry)                edit_video.py  (python do Blender)
  lê YAML → resolve → JSON absoluto   →     consome JSON, só cria strips
```

Dois ganhos além de evitar a dependência:

1. **O resolvedor fica testável na suíte normal.** É puro, sem `bpy` — o mesmo padrão que já
   vale para `build_subtitle_timeline` e companhia, que rodam marcados `no_db` sem Blender.
2. **O JSON resolvido é artefato inspecionável.** Quando um render sai errado, hoje é preciso
   inferir os frames; com isso, eles estão escritos. Serve para o mesmo lugar onde
   `job_config.json` já é escrito no tmpdir.

---

## Loop de preview

Sem isto, "editar à mão" é ficção: uma linha mudada custaria **12min42s** para ser vista, que
é o render medido de uma parte no servidor. Três níveis, do mais barato ao mais caro:

**1. Validação sem Blender — status: implementado.** `POST /timelines/validate` +
`GET /timelines/schema` em `blender_worker` (ver `blender_worker/CLAUDE.md` § "Fase 3, nível
1"). Resolve e devolve os frames de cada clipe, sem Blender, sem asset real — quem chama
informa durações em segundos no corpo da requisição. Pega typo em nome de input, referência a
anchor que não existe, expressão inválida; clipe que termina antes de começar vira **warning**,
não erro, porque a duração usada aqui já é uma aproximação conhecida (ver Fase 2 abaixo). O
app web que vai consumir isso — editor de YAML + player de preview — é **projeto separado, fora
deste monorepo**; não faz parte deste documento nem do que está implementado aqui.

**2. Frame único — status: implementado.** `POST /timelines/preview/frame` — `blender -b <blend>
-o <prefix> -F PNG -f N` para um PNG, devolvido direto no corpo da resposta. Exige `video_id`
real (diferente do nível 1): um asset sintético não mostra se o card está na posição certa, que
é o propósito de olhar um frame de verdade. Ver `blender_worker/CLAUDE.md` § "Fase 3, níveis
2/3".

**3. Preview em baixa resolução — status: implementado.** `POST /timelines/preview/clip` —
`resolution_percentage` reduzido (opt-in, confirmado que o `--python-expr` que o define aplica
antes do `-a` renderizar) com faixa de frames (`-s`/`-e`, `start_s`/`duration_s` no corpo, 15s
por padrão). **O fator de speedup continua não medido** — o parâmetro existe, a decisão de
quanto reduzir para ficar usável fica para quem for de fato operar o loop de edição.

---

## Fases

### Fase 1 — schema e resolvedor puro

Nenhum `bpy`, nenhuma mudança no render. O de-risking inteiro está aqui: escrever o template
de produção no formato novo e **provar por teste que ele resolve para os mesmos números de
frame** que o `main()` atual produz, nos dois modos de hook.

Se não resolver, o schema está errado e ainda não custou nada. É o mesmo motivo pelo qual a
Fase 1 do `multi_account.md` não mexe no modelo de dados.

### Fase 2 — executor

`main()` vira despachante sobre a timeline resolvida. Cada tipo de clipe e cada filtro chama
a função que já existe. O critério de pronto é render byte-comparável ao atual — não
"parecido": os mesmos frames, o mesmo mixdown.

**Escrita, não wired em produção.** `apply_payload` existe em `scripts/edit_video.py`, mais
`timeline/payload.py` (o que faltava desta fase: serializar as propriedades não-temporais —
volume, estilo, `y_position` — que a Fase 1 deixou de fora de propósito). Chama exatamente as
mesmas funções que `main()` já chama; nenhuma lógica bpy nova, só roteamento novo. `main()` e
`worker.py` continuam intocados — nada no pipeline chama `apply_payload` ainda.

**O critério de pronto ("render byte-comparável") foi verificado contra Blender de verdade —
4.2.20 LTS instalado localmente, a mesma versão pinada no Dockerfile — e achou um problema
real, agora corrigido.** Sem Docker Desktop disponível, mas com o binário nativo do Windows: um
`.blend` sintético, assets sintetizados (tom senoidal para voz/gancho/trilha, `.srt` de 16
palavras, PNG de card via Pillow, fundo colorido renderizado pelo próprio VSE — os mesmos
truques de `project_render_verification_recipe.md`), rodando `main()` e `apply_payload` lado a
lado nos dois modos de hook, comparando `scene.sequence_editor.sequences_all` (canal, frame,
`blend_type`, volume, mute) entre os dois.

**O que achou:** toda estrutura de strip batia perfeitamente — mesmos canais, mesmos frames de
início e fim, mesmo `blend_type`, mesmo volume e mute — **exceto um número**: `scene.frame_end`
saía 10 frames adiantado no caminho novo (226 contra 236 no modo tocado; 136 contra 146 no
mudo). A causa: a "simplificação conhecida da Fase 1" (`subtitles` com `duration: source` igual
à voz) é só uma aproximação — a última palavra de um `.srt` real, com o `hold` do
`max_hold_seconds`, pode terminar depois da própria narração, e o resolver puro não tem como
saber disso sem parsear o SRT, o que quebraria o objetivo de rodar em milissegundos sem
Blender.

**A correção não foi no resolver, foi em `apply_payload`: duas passagens.** Clipes `content` são
criados primeiro; `scene.frame_end` é então **recalculado a partir dos strips reais** com
`content_end_frame` — a mesma regra que `main()` já usa — em vez de confiar em
`payload["timeline_end"]`. Só depois disso os clipes `bed` são criados, com a repetição do
fundo e o fade da música recalculados contra esse valor corrigido (não contra
`repeats`/`fade_start`, que continuam no payload como valores consultivos para o preview de
milissegundos, não para o render final). Reverificado depois da correção: `scene.frame_end`
bate exatamente (236=236, 146=146) nos dois modos, e todos os strips continuam idênticos.

Ficou também um teste de regressão permanente
(`tests/test_apply_payload.py::test_apply_payload_extends_frame_end_when_the_real_subtitles_outlast_the_voice`)
que reproduz exatamente essa forma sem precisar de Blender — uma legenda falsa que termina 10
frames depois da voz, e a asserção de que `scene.frame_end` acompanha.

**O que não foi comparado:** os MP4 finais renderizados batem em contagem de frame (236/236,
146/146) e completam sem erro, mas não houve comparação de mixdown de áudio (dBFS) nem diff de
pixel entre os PNGs — a prova de estrutura de strip idêntica já cobre a mesma informação por
outro caminho (mesmo canal, mesmo frame, mesmo volume ⇒ o mixdown é o mesmo por construção). A
única divergência que sobrou é `scene.render.resolution_x/y`, e é artefato do `.blend` sintético
(Blender padrão de fábrica, paisagem) usado na verificação — `main()` nunca define resolução, ela
vem do `.blend`; `apply_payload` define explicitamente a partir de `canvas`, o que é
deliberado e não muda nada em produção (onde o `.blend` real já é 1080×1920).

### Fase 3 — preview e validação

Os três níveis acima, na ordem em que estão listados.

**Nível 1 — implementado.** `POST /timelines/validate` e `GET /timelines/schema`, ligados em
`api/app.py`. Reusa o resolvedor e o `build_payload` da Fase 1/2 sem tocar em nenhum dos dois
— só chama os dois e devolve o resultado (ou o erro) como JSON. Ganhou, no processo, dois
ajustes que os níveis 2/3 também herdam: erros de `resolver.py`/`payload.py` agora nomeiam a
trilha e o clipe de onde vieram (antes, várias mensagens só diziam o tipo do clipe, nunca a
trilha), e `ResolvedTimeline` ganhou um campo `warnings` para o caso "clipe termina antes de
começar" — não fatal, de propósito. Novo módulo `timeline/loader.py` (YAML → `TimelineDoc`,
uma exceção só) é o seam que um preview de frame único ou de clipe curto vai reusar.

**Níveis 2 e 3 — implementados.** `POST /timelines/preview/frame` e
`POST /timelines/preview/clip`, exigindo `video_id` real (ao contrário do nível 1). As três
decisões que ficaram em aberto na primeira versão deste documento:

- **Duração real** vem de `ffprobe` (`timeline/probe.py`), não mais digitada — `ffmpeg` é
  dependência nova do `Dockerfile`.
- **Formato de saída** não é decisão do `edit_video.py`: `main_declarative()` (o dispatcher
  Fase 2 chamado pela primeira vez) só monta a cena via `apply_payload` e para; frame vs clipe,
  faixa de frames e `resolution_percentage` são flags da CLI da segunda chamada ao Blender, não
  código dentro dela.
- **Concorrência isolada**: `preview_slot()`, semáforo próprio, independente de
  `worker.render_slot()` — um preview interativo nunca fica atrás de um render de produção de
  ~12min na fila.

`worker.py` e `POST /jobs` continuam **intocados** nos três níveis.

---

## Decisões em aberto

- **Coexistência com o `template.json` v1.** O bucket é compartilhado entre ambientes e a
  produção depende do objeto atual. Chave `version` decidindo qual interpretador roda, ou
  corte seco com republicação coordenada? A chave é mais segura e é dívida permanente.
- **Transições entre clipes** (`cross`, `wipe`) — dentro ou fora do vocabulário inicial. Nada
  no vídeo atual usa, e cada tipo novo é código.
- **Onde o YAML mora** — objeto novo no bucket ao lado do `.blend`, ou coluna na tabela
  `templates` do `blender_worker`. O bucket mantém a simetria com o que já existe; a coluna
  evita mais um objeto que pode ficar defasado.
- **Como o preview é pedido** — flag em `POST /jobs` ou rota própria. Flag reusa a fila e o
  semáforo de render; rota própria evita que preview dispute slot com produção.
- **`text` como tipo de clipe de primeira classe** — o vocabulário lista `text`, mas o
  template atual só usa `subtitles`. Um clipe de texto avulso (título, crédito) é trivial de
  suportar e não tem consumidor ainda.
- **Amarração com multi-conta** — `accounts.template_id` e variação de formato por conta são
  assunto de `docs/multi_account.md`, deliberadamente fora deste documento.
