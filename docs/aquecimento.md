# Aquecimento de conta — proposta

**Status: Parte 1 (a rampa de publicação, software) implementada em 06/09/2026** — ver
`docs/vision.md` → "Rampa de publicação — aquecimento de conta" e
`tiktok_poster/CLAUDE.md`. A Parte 2 (a rotina manual, abaixo) continua sendo trabalho
humano por decisão de projeto e não muda com este documento. Este arquivo permanece como
registro das tensões e decisões — as marcadas ✅ abaixo foram resolvidas na implementação.

Data: 29/08/2026. Escrito contra o estado do repo nessa data — uma conta do Buffer
(`vozes.do.reddit7`), TikTok publicando 3×/dia, YouTube `enabled = false` desde 27/08.

---

## O que este documento não cobre

**Bot de engajamento.** Automatizar login, scroll, curtida, follow ou watch time para
"treinar o algoritmo" é automação de engajamento inautêntico: viola o ToS dos dois
plataformas e é o próprio sinal que derruba conta. O `multi_account.md` já registra a
conclusão na Fase 3 — criar e aquecer conta é trabalho humano.

O que sobra é construível e é o que está abaixo: **ritmo** (software) e **uso real**
(humano).

---

## Aquecimento resolve conta nova. Não é o problema do TikTok hoje

Vale dizer antes, porque as duas coisas se confundem e o remédio é diferente:

| Situação | Sintoma | O que resolve |
|---|---|---|
| Conta **fria** | nunca entregou; sem histórico | rampa + uso humano |
| Conta **suprimida** | entregou 500–700 e desabou para <10, sem aviso | mudar o que gerou a classificação |

A `vozes.do.reddit7` é o segundo caso. Rampa lá **reduz a taxa de amostras** que alimentam
um fingerprint de canal, o que ajuda daqui para frente — mas não desfaz classificação já
feita. As correções estruturais são outras e estão rastreadas fora deste documento (PR #24
já em produção; volta do vídeo para <90s ainda pendente).

### A regra: todo canal novo entra em rampa

**Decidido em 29/08/2026.** A rampa é propriedade **do canal**, não do destino nem da conta.
Canal novo — em qualquer plataforma, em qualquer conta, criado hoje ou daqui a um ano —
começa em rampa; canal com histórico segue no ritmo cheio.

A alternativa seria decidir caso a caso, e caso a caso significa lembrar. O canal do
**YouTube** é só a instância viva da regra hoje: parado desde 27/08, religar direto a
3 posts/dia é exatamente o padrão a evitar.

⚠️ **O gatilho é "o `channel_id` mudou", não "o serviço subiu".** A troca de conta do
Buffer em 15/08 (`linguia.memes` → `vozes.do.reddit7`) foi um canal novo de TikTok e teria
entrado em rampa sob esta regra — saiu publicando no ritmo cheio porque nada existia para
notar a troca. Vale considerar um aviso no boot quando o `channel_id` configurado não
corresponde ao que a rampa conhece: sem isso, a regra vale só enquanto alguém lembrar de
preencher a data.

---

## Parte 1 — a rampa de publicação

### Onde plugaria

Um ponto só: `next_available_slot()` em `tiktok_poster/src/tiktok_poster/buffer/scheduler.py`
recebe `posts_per_day: int` e o usa como teto constante enquanto varre os dias para a
frente.

⚠️ **O teto deixa de ser escalar.** A varredura anda dia a dia, e durante a rampa cada dia
tem um teto próprio. O parâmetro precisa virar algo consultável por dia
(`cap_for(day: date) -> int`), não um `int` calculado uma vez na rota. Trocar o valor sem
trocar a forma dá uma rampa que só acerta o primeiro dia da varredura.

### Onde o estado mora: no config, não em banco

⚠️ **Premissa desatualizada desde a Fase 1 do multi-account (06/09/2026): o `tiktok_poster`
ganhou banco próprio** (`account_credentials`, para as credenciais cifradas). Isso muda a
conclusão só para contas extras — elas guardam a data de início da rampa como coluna em
`account_credentials`, junto da credencial, em vez de config. A conta default (env vars, sem
linha em `account_credentials`) continua exatamente como este trecho descreve: o raciocínio
abaixo vale para ela sem alteração.

O `tiktok_poster` não tinha banco quando isto foi escrito, e o `multi_account.md` só
propunha um para credenciais. Criar um só para guardar "dia N do aquecimento" seria
desproporcional — e o dado é derivável:

```ini
[warmup]
# Uma data por canal. Vazio ou ausente = canal com histórico, ritmo cheio.
# O TikTok fica vazio porque a conta não é nova — é o outro caso da tabela acima.
tiktok_started_on =
youtube_started_on = 2026-09-01
# Degraus: "<posts_por_dia>x<dias>", último sem contagem = regime permanente.
steps = 1x7,2x7,3
```

`dia N = hoje − started_on` resolve o resto. Sem migration, sem estado a reconciliar depois
de restart, e o freio manual é apagar uma linha — o mesmo desenho do `[youtube] enabled`,
que já provou ser o tipo de controle que sobrevive a uma emergência às 3h da manhã.

⚠️ **A regra por canal contradiz o grão proposto no `multi_account.md`.** Lá, o
aquecimento é `accounts.status = warming` — um estado da *conta*. Mas uma conta ativa que
ganha um canal de YouTube novo precisaria aquecer só esse canal, e `accounts.status` não
sabe expressar isso: ou a conta inteira volta para `warming` e o TikTok maduro perde ritmo,
ou o canal novo nasce a 3×/dia. O estado tem de pendurar no canal
(`channels(account_id, platform, channel_id, warming_started_on, ...)`), não na conta.

Isso não é mudança a fazer agora — o `multi_account.md` é proposta e continua sendo. É
registro de que, quando a Fase 2 for desenhada de verdade, `accounts.status = warming`
precisa ser revisto à luz desta decisão.

### O que a rampa quebra, e precisa de decisão

**1. `preferred_times` sob teto de 1/dia.** São três horários; com teto 1, o primeiro
(14:00 UTC = 11h BRT) seria escolhido todo dia. Uma semana publicando sempre às 11h em
ponto é cauda fixa — a mesma classe de assinatura que as hashtags idênticas em 47 posts,
já corrigida. A escolha do horário precisa **rodar por dia** (`slot_times[dia % len]`),
não pegar sempre o primeiro.

**2. `continuation_slot` ignora o teto de propósito.** Uma história em duas partes publica
dois vídeos no mesmo dia, o que fura um teto de 1/dia. E isso colide com a direção oposta
já desejada: voltar ao formato de duas partes abaixo de 90s. **As duas coisas não cabem
juntas num teto de 1.** As saídas são três, e a escolha é de produto:

   - manter a continuação fora do teto (uma "unidade" = uma história, não um post);
   - não dividir história durante o aquecimento (fere o formato que se quer recuperar);
   - contar a continuação e começar a rampa em 2.

   A primeira é a que preserva as duas intenções, e é a recomendada — mas ela significa
   que "1 post/dia" quer dizer 1 *história*/dia, que pode ser 2 vídeos.

**3. "Um slot, dois canais" deixa de valer — e isso agora é consequência, não exceção.**
Hoje o slot sai da fila do TikTok e vale para os dois destinos; `_schedule_youtube` é
tudo-ou-nada por post. Com a rampa por canal, dois canais em fases diferentes é o **caso
normal**: qualquer canal acrescentado depois nasce em rampa enquanto os existentes rodam
cheios. Na prática o destino em rampa publica num subconjunto determinístico dos slots do
TikTok — barato de fazer, mas a premissa comentada na própria rota precisa ser reescrita
junto, senão o comentário passa a mentir sobre o comportamento.

### Os degraus

`1x7, 2x7, 3` é palpite fundamentado, **não medição** — não há dado próprio sobre o efeito
da rampa, e não vai haver sem rodar. Fica como default explícito e editável, e não como
número com autoridade que não tem.

---

## Parte 2 — a rotina manual

Software não faz esta parte. É o que dá à conta um histórico de uso que não parece de bot,
e é a metade que o `multi_account.md` já previa ser humana.

**Antes do primeiro post de um canal novo ou parado:**

- perfil completo — foto, nome, bio, link; conta vazia publicando 3×/dia é o padrão clássico
- 3 a 5 dias de uso real do app pelo celular, na conta, alguns minutos por dia
- consumir o nicho que se vai publicar: o histórico de consumo é o que a plataforma tem
  para decidir a quem mostrar o primeiro vídeo
- interagir onde faz sentido de verdade, sem meta de quantidade

**Durante a rampa:**

- responder comentário nos primeiros posts, do celular
- não usar VPN nem trocar de aparelho no meio do aquecimento
- checar o painel a cada poucos dias e anotar a data de qualquer inflexão — a falta desse
  registro é o que impediu datar a queda de agosto

⚠️ **Nada disto é agendável.** Se virar tarefa cronometrada executada por script, deixou de
ser uso real e passou a ser o que a Parte "o que este documento não cobre" recusa.

---

## Decisões pendentes antes de implementar

| Decisão | Opções |
|---|---|
| ~~Rampa vale para quais destinos~~ | ✅ **todo canal novo**, em qualquer plataforma (29/08) |
| ~~Unidade do teto~~ | ✅ **história** — continuação livre (06/09) |
| ~~Degraus~~ | ✅ `1x7,2x7,3`, como default configurável, sem medição por trás (06/09) |
| ~~Horário sob teto reduzido~~ | ✅ **roda por dia** (06/09) |
| ~~Divergência de ritmo entre destinos~~ | ✅ resolvida por consequência: é o caso normal |
| Aviso quando o `channel_id` muda sem data de rampa | ✅ **feito para contas extras** (upsert de `POST /accounts` já tem o antes/depois); **adiado para a conta default** — exigiria persistir "qual canal era antes" em estado novo (06/09) |

---

## Como isto entra no `product.md` e no `vision.md`

Não entra — ainda. Pela regra do `CLAUDE.md`, os dois arquivos de produto documentam o que
foi implementado. Enquanto isto for proposta, vive aqui, como o `multi_account.md`. Quando
a rampa for construída, a descrição em linguagem de usuário vai para o `product.md` e as
regras de negócio para o `vision.md`, nessa ordem.
