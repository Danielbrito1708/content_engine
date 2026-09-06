# Volume de comentários — proposta

**Status: Frente 1 implementada em 02/09/2026. As outras cinco continuam proposta.** O
documento existe para registrar as ideias com as tensões na mesa, no mesmo espírito do
`multi_account.md` e do `aquecimento.md`.

Data: 31/08/2026. Escrito contra o estado do repo nessa data — um canal de TikTok
(`vozes.do.reddit7`) e um de YouTube publicando 3×/dia pelo Buffer, seleção de história por
`2 × outrage + story_score`.

---

## Por que comentário, e o que ele não resolve

Comentário é a interação mais cara de produzir para o espectador e a que mais move a
distribuição: thread longa (resposta a resposta) é sinal que as duas plataformas pontuam
alto. Mas **isto não é o remédio da queda de agosto**. A `vozes.do.reddit7` é conta
suprimida, não conta fria — a distinção está na tabela do `aquecimento.md`, e vídeo que não
é entregue não junta comentário nenhum. As frentes abaixo aumentam a taxa de comentário por
espectador alcançado; elas não aumentam o alcance.

⚠️ **Nada disto é medível hoje.** Não há coleta de métrica dos vídeos publicados em nenhum
serviço — o Buffer agenda e o assunto morre ali. Sem anotar comentários por vídeo antes e
depois, qualquer frente daqui vira crença. O passo zero, e o mais barato, é uma planilha
manual com data, vídeo e contagem.

---

## O que já existe, e o que seria novo

| Frente | Estado real no repo |
|---|---|
| CTA pedindo veredito | **Existia até 31/08/2026** — saiu da legenda junto com o resto de `cta_per_part`. Ver `docs/vision.md` |
| CTA fora do texto narrado | **Já existe, e é regra dura.** O narrado termina no último evento da história |
| CTA binário na frente da legenda | **Implementado em 02/09/2026** — campo novo (`classification.binary_cta`), não o `cta_per_part` antigo. Ver Frente 1 |
| Card na tela | **Existe só na abertura** (`add_card`, canal `card` do template) |
| Seleção por revolta | **Já existe.** `rank_by_story` com `outrage_weight = 2` |
| Comentário fixado | Não existe |
| Métrica de comentário | Não existe |

⚠️ **A linha "CTA na frente da legenda" da versão anterior deste documento estava
desatualizada no dia em que foi escrita** — descrevia `compose_caption` montando
`{cta}{parts_label}` + tags, mas o `cta` (a pergunta narrada) já tinha saído da legenda no
mesmo commit do dia 31/08. A Frente 1, abaixo, corrige a premissa: não era ajustar algo que já
rodava, era reintroduzir texto na legenda com um campo novo.

---

## Frente 1 — CTA de votação binária (`llm_service`) — IMPLEMENTADO 02/09/2026

Trocar a pergunta aberta pela escolha de duas opções: "quem errou mais: o marido ou a
sogra?", "comenta 1 se perdoaria, 2 se terminava na hora". Escolher entre duas coisas custa
menos que formular uma opinião, e o atrito é o que decide quem comenta.

**Decisão tomada ao implementar**: campo **novo e separado**, não substituição do
`cta_per_part`/REGRA DO FECHAMENTO. `cta_per_part` continua sendo a pergunta que fecha a
*narração*, cópia literal, e continua nunca indo para a legenda. `classification.binary_cta`
é texto à parte, só para quem lê a legenda antes de assistir — nasce vazio quando a história
não tem dois lados claros, e tem teto de 100 caracteres (`MAX_BINARY_CTA_CHARS`) para caber
antes do corte de "...mais" do TikTok.

**Onde**: `llm_service/src/llm_service/schemas/refine.py` (campo + truncamento),
`llm_service/src/llm_service/prompts/refine.py` (regra + exemplo de JSON),
`tiktok_poster/src/tiktok_poster/hashtags/selector.py` (`compose_caption` ganhou o parâmetro
`binary_cta`), `tiktok_poster/src/tiktok_poster/api/routes/schedule.py` (lê o campo do dict de
classificação e repassa para os dois destinos). Sem migration: `classification` trafega como
dict opaco do `llm_service` até o `tiktok_poster`, sem remapeamento no orchestrador.

⚠️ **A REGRA DO FECHAMENTO não afrouxou.** A pergunta que fecha o texto narrado continua
proibida de aparecer na legenda — é `binary_cta` que aparece, e é texto diferente.

⚠️ **A segunda ideia da frente original não foi implementada.** "Terminar no clímax do
dilema moral" é escolha de *história*, não de refino: o prompt manda preservar o roteiro
original e terminar no último evento. Pedir ao modelo para reposicionar o final é pedir para
ele cortar a história — exatamente o que a regra atual proíbe, e por bom motivo. Se isso valer
a pena, o lugar é a seleção (Frente 5), não o `refine`.

Ver `llm_service/CLAUDE.md` → "CTA de votação binária na legenda" e `tiktok_poster/CLAUDE.md`
→ "CTA de votação binária na legenda" para a documentação completa.

---

## Frente 2 — os primeiros 60 caracteres da legenda (`tiktok_poster`) — parcialmente coberta pela Frente 1

No TikTok só os primeiros ~50–80 caracteres aparecem antes do "...mais". A implementação da
Frente 1 já cobre metade disto de graça: `binary_cta` sai do `llm_service` com teto de 100
caracteres (`MAX_BINARY_CTA_CHARS`, cortado sem partir palavra) e `compose_caption` o põe
**primeiro** na legenda, antes do rótulo de parte e das hashtags — então o texto que abre a
legenda nunca chega sem limite ao `tiktok_poster`.

**O que ainda não foi feito**: 100 caracteres é folga, não os ~50–80 exatos que o TikTok
mostra antes do corte — não há verificação contra o número real da plataforma, que varia por
tamanho de tela. Se isso importar na prática, é ajustar `MAX_BINARY_CTA_CHARS` para baixo (é
constante isolada, sem migration) ou medir o corte real e recalibrar.

---

## Frente 3 — comentário fixado, sugerido pela notificação

O comentário fixado do criador define o tom da conversa e é o multiplicador mais direto que
existe nas duas plataformas. Fixar é manual — ninguém automatiza isso pelo Buffer —, mas o
texto pode chegar pronto.

**Como**: campo novo no refino (`pinned_comment_suggestion`), carregado até o agendamento e
incluído na notificação de publicação (`orchestrator/src/core/notify.py`). Quando o ntfy
apitar, é copiar e colar.

⚠️ **Campo novo no refino é migration, não só prompt.** Foi exatamente o caminho do
`youtube_title`: schema, coluna, migration — e a `006` faltando derrubava todo run no refino
com `UndefinedColumn`. Se o texto não precisar sobreviver a restart, mandá-lo direto na
notificação sem persistir evita a migration inteira.

⚠️ **`format_message` corta em 800 caracteres** e junta os campos numa linha só. Sugestão
longa é sugestão truncada.

---

## Frente 4 — card de pergunta nos últimos segundos (`blender_worker`)

Quem assiste em tela cheia não lê a legenda. Um card com a pergunta nos últimos 3–5
segundos entrega o convite onde os olhos estão.

A máquina existe: canal `card` no template, `add_card`, `image/text.py`, e o `card` já é
configurável (`y_position`, `fade_frames`, `tail_seconds`).

⚠️ **Isto contradiz uma decisão explícita no código.** `edit_video.py` diz, no fim da
montagem: *"The only ending the video has: no outro card, no closing beat — the last
narrated word is the last frame"*. A ausência de outro é escolha, não esquecimento. Reabrir
exige argumento novo — e o argumento existe (comentário), mas a decisão é de produto.

⚠️ **O template que roda não é o do repo.** Ele é baixado do R2 no momento do render, e o
bucket é compartilhado entre ambientes. Pela regra de reversibilidade já registrada: **criar
um `BLENDER_TEMPLATE_ID` novo** em vez de sobrescrever o publicado, para o rollback ser
trocar uma variável.

Custo: é a frente mais cara das seis — mexe no render, no template publicado e no banco do
`blender_worker`. Fica por último.

---

## Frente 5 — dilema divisivo na seleção (`content_scout`)

Vilão claro gera revolta; dilema ambíguo gera discussão entre espectadores, que é o que
produz thread longa. São eixos diferentes e, em parte, opostos: quanto mais claro o vilão,
menos há o que discutir.

⚠️ **Isto contradiz a decisão de 25/08/2026**, que fixou "revolta com vilão claro" como
critério e `2 × outrage + story_score` como ordem. A decisão está no `CLAUDE.md` para não
ser reaberta sem motivo novo — e "comentário é métrica melhor que revolta" é um motivo novo,
mas precisa ser dito como tal, não implementado por baixo.

**Como seria**: um campo novo em `/story-quality` (`divisiveness`, 0–10 — o quanto a
audiência se dividiria), somando na chave de `rank_by_story` com peso próprio. A estrutura
aguenta: a chave já é uma soma ponderada e os pesos vivem no `config.ini`.

⚠️ **Nota nova exige recalibrar, não só somar.** A régua de `outrage` foi medida contra um
corpus; `divisiveness` nasceria sem régua nenhuma, e um peso chutado num termo novo desloca
a ordem inteira do ciclo. Começar com peso baixo, ou só como rótulo e contador — que foi
exatamente o desenho escolhido para `min_outrage_score`.

---

## Frente 6 — responder os primeiros comentários (humano)

Nos primeiros 30 minutos, curtir e responder com uma contra-pergunta. Cada resposta notifica
a pessoa e a traz de volta ao vídeo.

⚠️ **Isto não vira software, e a linha é a mesma do `aquecimento.md`**: automatizar
curtida, resposta ou qualquer interação é engajamento inautêntico e é o próprio sinal que
derruba conta. Responder do celular é uso real; um script que responde não é. O que o
software pode fazer é o que a Frente 3 já propõe — avisar que o vídeo saiu.

---

## Ordem sugerida

Da menor para a maior superfície tocada:

1. **Planilha de comentários por vídeo** — ainda não existe, e sem ela a Frente 1 (implementada) não é avaliável nem as demais
2. ~~**Frente 1** (prompt de CTA binário)~~ — **implementada em 02/09/2026**, ver acima
3. ~~**Frente 2** (primeiros 60 caracteres)~~ — **parcialmente coberta** pela Frente 1 (teto de 100 chars na fonte); falta calibrar contra o corte real da plataforma, se necessário
4. **Frente 3** (comentário fixado no ntfy) — decidir antes se persiste ou não
5. **Frente 5** (divisividade) — reabre decisão de 25/08 e pede calibragem
6. **Frente 4** (card final) — render, template no R2 e decisão de edição

Uma de cada vez. Duas mudanças juntas num canal sem métrica não deixam saber qual delas fez
efeito — e a queda de agosto já custou esse aprendizado uma vez. **A Frente 1 foi implementada
sem a planilha existir ainda** — é a mudança mais barata e reversível das seis, mas o efeito
dela continua tão inavaliável quanto o das outras até o passo 1 acontecer.

---

## Decisões pendentes

| Decisão | Opções |
|---|---|
| Medir comentário | planilha manual / nada / coleta automática (serviço novo) |
| ~~CTA binário: substitui ou é campo novo~~ | **Decidido 02/09/2026: campo novo** (`binary_cta`), independente do `cta_per_part`/REGRA DO FECHAMENTO |
| Limite da primeira linha | **Parcial**: truncamento na fonte (100 chars, `MAX_BINARY_CTA_CHARS`) implementado; calibragem contra o corte real da plataforma, não |
| `pinned_comment_suggestion` | persistir (migration) / só na notificação |
| Card final no vídeo | reabrir a decisão do "no outro card" / manter |
| Divisividade na seleção | peso na chave / só rótulo e contador / não fazer |

---

## Como isto entra no `product.md` e no `vision.md`

Pela regra do `CLAUDE.md`, os dois arquivos documentam o que foi implementado — enquanto uma
frente for só proposta, ela vive só aqui. A Frente 1 já entrou nos dois: `product.md` →
"A legenda pergunta de que lado você está" (reescrita), `vision.md` → "CTA de votação binária
na legenda" (dentro de "Classificação de Conteúdo"). As outras cinco continuam só aqui até
serem construídas.
