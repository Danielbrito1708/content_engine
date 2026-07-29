# TODO — calibrar a classificação de storytelling

> **Status:** decisão 1 RESOLVIDA (28/07/2026), decisões 2–4 ainda abertas.
> **Ação esperada do Daniel:** responder as perguntas 2, 3 e 4 da seção
> "Decisões pendentes". Enquanto isso a feature roda com `min_story_score = 6`.

---

## ✅ Decisão 1 — resolvida: rota (a), trocar as fontes

O Daniel apontou o `r/story`. Medição ao vivo dos candidatos (28/07/2026, feed
real, `parse_feed` do pipeline, filtros de produção 600–6000 chars):

| Sub | passam | mediana | idioma |
|---|---|---|---|
| `EuSouOBabaca` | **15/15** | 1831 | pt-BR |
| `story` | 13/15 | 1704 | inglês |
| `stories` | 12/15 | 1587 | inglês |
| `desabafosdavida` | 15/15 | 1278 | pt-BR, mas é o gênero antigo |
| `opiniaoimpopular` | 7/15 | 551 | opinião, não história |
| `HistoriasDeReddit`, `HistoriasdeTerror` | — | — | **espanhol**, descartados |
| `Quem_Foi_O_Babaca`, `EuSouOBabacaButAdult`, `contosdevidareal`, `HistoriasBrasil`, `contosdamadrugada`, `Creepypastas_Brasil`, `DesabafosAbsurdos`, `assombracao` | 0 | — | subs mortos |

Não existe sub de vingança em pt-BR (a busca voltou vazia).

`config.ini` passou a `EuSouOBabaca,story,stories`. Duas mudanças de prompt
vieram junto, porque sem elas o corpus novo seria mal servido:

1. **`refine.py`** — roteiro final sempre em pt-BR, traduzindo quando a fonte é
   inglesa. Antes o prompt só dizia "preserve a essência", e `r/story` sairia em
   inglês direto para um TTS pt-BR.
2. **`story.py`** — parou de descontar por "pergunta direta ao fórum" sem
   qualificar. *Todo* post do `EuSouOBabaca` é "Sou babaca por…?"; a régua antiga
   puniria o melhor corpus pelo motivo errado. Agora só desconta a pergunta que
   substitui a cena, não a que emoldura o conflito.

⚠️ **Consequência para os dados abaixo:** a medição dos 30 posts foi feita em
`r/desabafos` + `r/relacionamentos`, que **não são mais fontes configuradas**. A
taxa de 40% `weak_storytelling` descreve um corpus aposentado. Os números seguem
aqui como registro histórico e como base de comparação — não como a régua atual.
Refazer com `--subreddits EuSouOBabaca,story,stories` antes de mexer no corte.

Por origem, aliás, os dois subs antigos empatam: `r/desabafos` média 5.7 (4
fracos de 15), `r/relacionamentos` média 5.3 (8 fracos de 15). **Nunca houve dado
que singularizasse o `desabafos` como pior** — o problema era o gênero.

---

## O problema que invalida a validação atual (levantado pelo Daniel, 28/07/2026)

A validação foi feita pontuando 30 posts reais de `r/desabafos` e
`r/relacionamentos`. **Isso compara maçã com laranja.**

Os subreddits configurados hoje —
`desabafos`, `relacionamentos`, `conselhos` (`config.ini`) — são **relatos
reais**: gente desabafando ou pedindo conselho. Ninguém escreve ali com intenção
de entreter. Existem subreddits **especializados em história escrita pra
entretenimento**, e é esse o material que o pipeline quer.

Isso contamina a conclusão de duas formas:

1. **A taxa de 40% "fraco" pode não medir a régua.** Pode estar medindo apenas
   que essas fontes não produzem o gênero que a gente quer. Mover o corte não
   resolveria — a fonte é que está errada.
2. **O prompt foi calibrado com um exemplo do gênero errado para o corpus.** O
   exemplo forte que está escrito dentro de
   `llm_service/src/llm_service/prompts/story.py` é o do Daniel: *"minha mãe foi
   intimidada por outras mães, então ela se vingou de forma doce"*. Essa é a
   forma canônica de `pettyrevenge` / `MaliciousCompliance` — setup, virada,
   desfecho satisfatório. Nenhum desabafo tem essa forma, porque desabafo não
   tem terceiro ato. **O modelo está sendo pedido para achar num corpus uma
   estrutura que aquele corpus não produz.**

Ou seja: a nota baixa em muitos casos pode estar **certa sobre o post e errada
sobre a pergunta**.

---

## O que os dados mostraram (30 posts reais, top/week, uma chamada em lote)

Modelo `claude-haiku-4-5`, via o endpoint real `/story-quality`, com o mesmo
recorte de 700 caracteres que o scout manda em produção.

| Nota | Qtd | Tag com corte em 6 |
|---|---|---|
| 9–10 | 0 | — |
| 8 | 2 | `strong` |
| 7 | 8 | `strong` |
| 6 | 8 | `strong` |
| 5 | 5 | `weak_storytelling` |
| 4 | 1 | `weak_storytelling` |
| 3 | 4 | `weak_storytelling` |
| 2 | 2 | `weak_storytelling` |
| 0–1 | 0 | — |

23 de 30 com `hook = true`. **12 de 30 (40%)** cairiam como
`weak_storytelling`.

Observação importante sobre esses 12: eles **não são um grupo só**. Dividem-se
em dois tipos que merecem tratamento diferente:

- **Notas 2–4 (7 posts) — não é história.** Planilha de custos de Uber, opinião
  genérica sobre mulheres, reclamação abstrata sem personagem. Aqui a nota está
  claramente certa.
- **Notas 5 (5 posts) — é história, só é pequena.** *"Fui na casa dela e achei um
  nojo"*, *"minha ficante usa chupeta"*, *"Mina furou o date porque não tenho
  carro"*. Tem conflito, tem gente, tem cena. São só curtas ou banais. Chamar
  isso de `weak_storytelling` é defensável, mas é uma decisão de produto
  diferente de rejeitar a planilha da Uber.

### Exemplo classificado como BOM (nota 8, `hook=true`)

**"Descobri que a minha namorada é casada. Conto ou não para o marido dela?"**
(r/relacionamentos, 588 chars)

> Nos conhecemos em janeiro e, em abril, começamos a namorar. Eu nunca suspeitei
> de nada. [...] Descobri no último domingo, por acaso, quando notei que havia um
> **segundo celular** dentro da bolsa dela. Bom, foi só acionar a tela do
> aparelho que lá estava: o protetor de tela era uma foto dela com o marido em
> uma viagem que eles fizeram.

`reason`: *"Revelação chocante, dilema moral, desfecho em aberto."*

O outro 8 foi *"Meu chefe chorou por que eu lembrava uma funcionária falecida"*.

### Exemplo classificado como RUIM (nota 2, `hook=false`)

**"MATEMÁTICAMENTE a UBER ficou inviável depois do novo passe e eu vou te
MOSTRAR o porquê"** (r/desabafos, 1963 chars)

> Gasolina: 140,00 de gasolina/dia (200km por dia)(1L=10km+-)
> Manutenção: 25,00/dia (650,00 = 26 dias trabalhados)
> Seguro do veiculo: 350,00/mês (13,45/dia)
> IPVA e licenciamento: 3.200,00 / ano (10,25/dia)

`reason`: *"Análise técnica, sem história ou enredo narrativo."* — `hook_line`
veio `None`. Título tem energia, mas não há personagem nem conflito. Narrar isso
seria uma voz lendo números.

### O erro real que apareceu

**"Acho q fui abusada"** tirou **2**, com `reason` *"Abertura confusa, contexto
sem conflito claro, não promete desfecho."*

Como história isso é errado — o conflito é gravíssimo. Mas os 700 caracteres que
o classificador viu são **só** contexto sobre o divórcio dos pais; o abuso nunca
aparece neles. O modelo acertou sobre a **abertura** e errou sobre a
**história**.

Essa é a limitação documentada no PR: julga o começo, não o post inteiro.
Defensável pro TikTok (o espectador também só vê o começo), mas significa que
**post bom com lide enterrado leva nota baixa**. Esse específico nem publicaria —
a moderação barra por abuso — mas serve de aviso sobre o que a nota mede.

---

## Decisões pendentes (é aqui que eu preciso da tua resposta)

### 1. Qual é o corpus certo? — ✅ RESOLVIDA, ver o topo do arquivo

Rota (a): fontes trocadas para `EuSouOBabaca,story,stories`, com tradução no
`/refine` e a régua de "pergunta ao fórum" corrigida no `story.py`.

### 2. Nota 5 é fraco ou é aceitável?

Se história curta-mas-real conta como publicável, o corte desce pra 5 e a taxa
de "fraco" cai de 40% pra 23%. Se não, fica em 6.

### 3. Quanto do post o classificador deve ver?

Hoje 700 caracteres (`story_excerpt_chars`). Opções:
- subir pra ~1500 — pega mais lide enterrado, custa mais token, **ainda é uma
  chamada só**;
- mandar abertura **+ final** — encontra o desfecho sem pagar pelo meio;
- manter 700 e aceitar que "abre mal" = nota baixa, por design.

### 4. O teto está sendo usado?

Nenhum post tirou 9–10. Isso é o prompt reservando o topo para o excepcional
(intencional), mas se o topo nunca é usado a nota efetivamente vira uma escala
de 2 a 8. Vale relaxar, ou o aperto é útil?

---

## Como refazer a medição depois de decidir

O script está em **`content_scout/scripts/score_real_posts.py`**. Reusa o
`RedditSource` real e o mesmo recorte, então o que chega no endpoint é byte a byte
o que o pipeline manda.

```bash
# com o llm_service do worktree de pé na 8010:
cd content_scout
PYTHONPATH="$PWD" poetry run python scripts/score_real_posts.py \
    --subreddits desabafos,relacionamentos --excerpt 700 --out novo.json
```

Ele imprime a distribuição de notas e a lista ordenada no stderr. A medição
original está congelada em **`docs/story_quality_baseline.json`** (os 30 posts
com nota, `hook`, `hook_line`, `reason` e a abertura exata que foi julgada) — dá
para comparar qualquer rodada nova contra ela.

Para inspecionar o que já foi etiquetado em produção sem re-pontuar:

```
GET /scout/seen?story_tag=weak_storytelling
```

`story_score` fica gravado cru no banco, então **mover o corte permite
re-derivar as linhas antigas** sem chamar o modelo de novo.
