# Deploy — plano para a máquina 24h

Este documento é o plano de deploy do `content_engine` na máquina de casa. Ele existe
para ser lido **na hora de subir**.

> **Estado (14/08/2026):** os ajustes que moram no repo — concorrência de render,
> `mem_limit`, notificação — já estão aplicados e vão junto com o `git pull`. Na máquina, a
> suspensão automática já foi corrigida (item 1). O que resta é instalar o Docker, criar os
> checks e preencher as credenciais de alerta.

A seção "Operação 24h" do `vision.md` descreve o que o `docker-compose.yml` já garante
sozinho. Este documento cobre o que fica **fora** do compose — a máquina, os limites do
sistema operacional e o monitoramento.

---

## O alvo

**Medido na máquina em 14/08/2026**, não estimado — ver `servidor.md` para o acesso.

| | |
|---|---|
| Máquina | Dell OptiPlex 3050, **bare metal** (`systemd-detect-virt` = `none`) |
| CPU | Intel i5-6500T — 4 núcleos, 4 threads, 2,5 GHz, 35 W |
| RAM | **12 GB** (11,6 GiB utilizáveis; ~700 MB em uso com o sistema ocioso) |
| Disco | 224 GB — 212 GB em `/` (**ext4**), 193 GB livres |
| Swap | **11,4 GB**, partição `/dev/sda5`, já ativa |
| Sistema | Debian 13.6 (trixie), kernel 6.12.101 |
| Carga | 3 vídeos/dia (teto de publicação), scout a cada 1h |
| Exposição | nenhuma — todo o tráfego é de saída |

**Três suposições deste plano estavam erradas, e todas para melhor:**

| Assumido | Real | Consequência |
|---|---|---|
| 8 GB RAM | **12 GB** | O orçamento de memória deixa de ser a restrição apertada |
| Proxmox VE | **bare metal** | Somem os três ajustes de hipervisor (LXC, ARC do ZFS, `nesting`/`keyctl`) |
| swap a configurar | **11,4 GB já ativos** | Item resolvido antes de começar |

Uma errou para pior: o processador é um **i5-6500T de 6ª geração**, não 8ª — e o sufixo
`T` é a versão de 35 W, com clock base baixo. O render é CPU-bound (VSE + encode x264,
sem GPU em lugar nenhum), então **a CPU passa a ser o gargalo no lugar da RAM**. Isso não
muda nada no dimensionamento de memória; muda a expectativa de *quanto tempo* leva um
render, que continua sem medição real.

**Bare metal também simplifica o orçamento.** Sem hipervisor, o Docker fala direto com o
kernel do host e este Debian custa ~700 MB em vez de 1–1,5 GB de Proxmox. E `/` em
ext4 encerra de vez a ressalva do ARC do ZFS, que era o consumo invisível mais perigoso
da versão anterior deste documento.

**A máquina não vinha headless** — rodava GNOME sobre Wayland com GDM em
`graphical.target`, e os ~700 MB medidos incluíam isso, na tela de login e sem sessão
aberta. O custo de RAM era tolerável com 12 GB; o problema era outro: **essa instalação
gráfica é o que suspendia a máquina por inatividade**, e um servidor 24h que dorme não é
servidor. Corrigido no item 1 — o boot passou a `multi-user.target`, o que também derruba
esses ~700 MB para algumas centenas.

**Por que a máquina de casa e não um VPS.** O perfil de carga é o pior caso possível
para nuvem: 3 renders/dia deixam a máquina ociosa quase o tempo todo, mas um VPS cobra
as 24h. O render é VSE + encode x264 — não há Cycles, OPTIX ou CUDA em lugar nenhum do
`blender_worker`, então GPU não entra na conta. As imagens somam ~36 GB e os `outputs/`
crescem sem retenção, e disco é justamente o item mais caro por GB em VPS.

**E nada precisa de tráfego de entrada.** O scout puxa do Reddit, o poster empurra para
o TikTok, TTS e LLM são chamadas de saída. Não existe cliente externo batendo na stack,
então o argumento clássico pró-VPS (IP público, DNS, TLS) não se aplica.

⚠️ **Nenhum serviço tem autenticação** — não há middleware de auth em nenhuma rota, e
`ORCHESTRATOR_SECRET` está comentado no `.env.example`. O compose publica todas as portas
no host (8000–8005, 5433, 9000/9001) com credenciais default (`postgres/postgres`,
`minioadmin/minioadmin`). Isso é tolerável numa LAN atrás de NAT e seria uma brecha
imediata num IP público. Enquanto a stack estiver assim, **rede local apenas** — acesso
remoto por Tailscale, nunca por port forwarding.

---

## O orçamento de RAM deixou de ser a restrição apertada

| Item | Consumo |
|---|---|
| Debian 13 ocioso, **com GNOME** (medido) | ~700 MB |
| Postgres + MinIO + 6 FastAPI | ~2–2,5 GB |
| Modelo Whisper residente | incluído acima (`base` em `int8`) |
| **Cada** render Blender 1080×1920 | ~1–2 GB (estimativa — medir no primeiro render) |
| **Sobra** | ~7 GB |

O Whisper é a boa notícia: `transcribe.py:12` instancia `WhisperModel(model_name,
device="cpu", compute_type="int8")` com `base` como default (`config.py:109`) — é o
modelo pequeno, quantizado. Fica residente num global de módulo, então é ocupação
constante e não pico.

Com 12 GB de RAM e 11,4 GB de swap, o cenário de OOM que motivava metade deste documento
fica distante. **Isso não é motivo para soltar o limite de concorrência**, por dois
motivos:

1. **A CPU virou o gargalo.** São 4 núcleos a 2,5 GHz num chip de 35 W, e o render é
   CPU-bound. Dois renders simultâneos não terminam antes de dois em sequência — dividem
   os mesmos núcleos e ainda disputam cache. Com 3 vídeos/dia não há throughput a ganhar.
2. **O custo de errar continua assimétrico.** Quando a memória acaba, quem o OOM killer
   derruba é arbitrário; se for o Postgres, perde-se o estado de *todos* os runs, contra
   um render que é recuperável.

**Os dois freios já estão no repo** (ver `vision.md` → "Operação 24h"): o semáforo do
`blender_worker` (`[blender] max_concurrent_renders = 1`), o `max_pending_runs = 2` do
scout e o `mem_limit: 3g` no compose. Não há nada a ajustar na máquina por causa disso —
a folga medida aqui é o que dá margem para subir esses números **depois**, se um render
real mostrar que a CPU aguenta.

---

## Ajustes antes de subir

A lista encolheu. Quatro dos seis itens da versão anterior deixaram de existir ao medir a
máquina — e não porque foram feitos, mas porque eram sobre uma máquina que não é esta:

| Item antigo | Situação |
|---|---|
| LXC em vez de VM | **Não se aplica** — bare metal, sem hipervisor |
| Limitar o ARC do ZFS | **Não se aplica** — `/` é ext4 |
| `max_pending_runs` 5 → 2 | ✅ **Feito no repo** |
| `mem_limit` no `blender_worker` | ✅ **Feito no repo** |
| Swap de 8–16 GB | ✅ **Já existe** — 11,4 GB em `/dev/sda5` |

Sobrou o que segue.

### 1. Impedir a suspensão automática ✅ feito em 14/08/2026

**A máquina dorme sozinha, e isso foi observado na prática em 14/08/2026**: ela suspendeu
por inatividade e voltou às 15:07:28 dentro do mesmo boot das 14:28:41 — não houve reboot,
foi suspend/resume. Enquanto dormia, ficou inacessível por SSH e sumiu até da tabela ARP.

É o defeito mais grave da lista, porque **invalida a premissa 24h em silêncio**. Dormindo,
o scout não roda, o orchestrador não agenda, e nenhum vídeo sai — sem erro em log nenhum,
porque não há falha: o sistema está fazendo exatamente o que foi configurado para fazer.

Pior, isso envenena o monitoramento desenhado adiante neste documento. Os dead-man's
switches do `alive` (15 min) e do `scout` (1h) disparam alerta no WhatsApp a cada soneca
de madrugada. Alarme falso recorrente é o caminho mais curto para a pessoa passar a ignorar
o alerta — e aí a camada de monitoramento inteira deixa de valer.

**A causa é a instalação gráfica**, e ela é verificável direto na origem — o greeter do
GDM carrega o default de desktop:

```bash
$ sudo -u Debian-gdm dbus-run-session -- gsettings get \
    org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
'suspend'
```

Ou seja, na tomada e parada na tela de login — a condição normal deste servidor — a
política em vigor é suspender. O sistema roda GNOME sobre Wayland em `graphical.target`, e
o GNOME aplica isso por ociosidade (~15 min por padrão) **sem ninguém logado**. O
`/etc/systemd/logind.conf` está intocado e os quatro targets de sleep estavam todos
`static`, não mascarados, então nada barrava o pedido.

O corte definitivo é no systemd, não no GNOME:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Mascarar os targets bloqueia **qualquer** origem de suspensão — GNOME, `logind` por
ociosidade, botão de power, `systemctl suspend` — em vez de corrigir só o gatilho atual.
Ajustar apenas o `gsettings` do GNOME resolveria hoje e voltaria a quebrar no dia em que
alguém reinstalasse o ambiente gráfico ou logasse com outro usuário.

Conferir (as quatro devem responder `masked`):

```bash
systemctl is-enabled sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Reverter, se algum dia a máquina deixar de ser servidor: trocar `mask` por `unmask`.

**A segunda medida foi tirar o ambiente gráfico do boot:**

```bash
sudo systemctl set-default multi-user.target
```

Isso ataca a causa raiz em vez de bloquear o sintoma, devolve algumas centenas de MB e
reduz superfície de atualização numa máquina que ninguém usa com teclado e monitor. **Nada
foi desinstalado** — GNOME e GDM continuam no disco e voltam com um comando; a tabela de
como ligar e desligar está em `servidor.md` → "Interface gráfica".

✅ **As duas aplicadas em 14/08/2026.** Verificado: `systemctl get-default` responde
`multi-user.target`, e as quatro units de sleep respondem `masked`. Elas são
complementares, e é de propósito — o `set-default` remove quem pedia a suspensão, o `mask`
impede que qualquer outro peça. Reativar a interface um dia não traz o problema de volta.

### 2. Instalar o Docker

**Não está instalado** — `docker --version` não responde, e não existe grupo `docker`
(o `server` está em `sudo`, `video`, `netdev` e afins, mas não nele). É o primeiro passo
real, e o único bloqueio de infraestrutura que restou.

Repositório oficial, não o `docker.io` do Debian: o pacote da distro atrasa versões e não
traz o plugin `compose` v2, que o `docker-compose.yml` deste projeto usa (`docker compose`,
sem hífen).

```bash
# https://docs.docker.com/engine/install/debian/
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker server   # exige logout/login para valer
```

Conferir antes de seguir — o segundo comando é o que prova que o plugin v2 entrou:

```bash
docker --version && docker compose version
```

⚠️ **Debian 13 (trixie) é recente.** Se o repositório do Docker ainda não publicar para
`trixie`, apontar o `VERSION_CODENAME` para `bookworm` funciona e é o contorno usual —
verificar antes de assumir que quebrou.

### 3. Buildar antes, não durante

`docker compose build` das 6 imagens (com o download do Blender) é pesado, e nesta CPU de
35 W é mais demorado do que o plano original supunha. Build primeiro, `up` depois.

E atenção ao `up -d` sem `--build`: ele sobe a imagem antiga em silêncio.

### 4. Olho no disco, que agora é o recurso escasso

São 193 GB livres, não os 500 GB que este documento assumia. As imagens somam ~36 GB — um
quinto do disco — e nem o build cache do Docker nem os `outputs/` têm limite ou retenção.

Não bloqueia subir, mas inverte a prioridade: **retenção deixou de ser um "depois"
confortável**. Ver "O que continua em aberto".

---

## Monitoramento

O requisito é ser avisado no WhatsApp quando algo der errado, 24h, sem ninguém olhando.

> **Estado:** camada 3 implementada em código (falta só configurar as credenciais);
> camadas 1, 2 e 4 são configuração desta máquina e continuam pendentes.

**A regra que organiza tudo:** o monitor não pode viver só na máquina que ele monitora. Se
a caixa morre, um Uptime Kuma rodando nela morre junto e não avisa ninguém. Por isso a
divisão em camadas — cada uma pega uma classe de falha que as outras não pegam.

### Camada 1 — dead-man's switch externo (pega a morte total)

A mais importante, e é de graça. Um cron na máquina pinga um serviço externo
(Healthchecks.io) a cada 5 minutos. Se os pings **param**, o serviço externo avisa.

É a única camada que pega queda de luz, internet fora, kernel panic — nada rodando na
máquina consegue reportar a própria morte.

### Camada 2 — saúde dos serviços (pega "um serviço adoeceu")

Uptime Kuma (~100–150 MB) monitorando os seis endpoints `/health`. Dá granularidade: qual
serviço, desde quando, histórico de resposta.

**Sem hipervisor, "separado" muda de significado.** O plano original o punha num LXC
próprio; aqui o equivalente é um `docker compose` **próprio**, fora do projeto do
content_engine — mesma máquina, mas ciclo de vida independente, então um `compose down`
ou um build quebrado da stack não leva o monitor junto. O que essa camada não pega
continua sendo a morte da máquina inteira; isso é trabalho da camada 1, que vive fora.

Com 12 GB, o custo de RAM dele deixou de ser argumento contra.

### Camada 3 — o pipeline parou de produzir (pega o silêncio)

✅ **Implementada** — é a única das quatro que é código, e por isso a única que não
dependia desta máquina existir. Ver `vision.md` → "Notificação de operação".

Esta é a que mais importa aqui e a que as outras duas **não** pegam: todos os serviços
podem responder `200` e mesmo assim nenhum vídeo sair.

O que ficou de pé, em `src/core/notify.py` (copiado entre `orchestrator` e
`content_scout`): notificação por evento no WhatsApp via CallMeBot — incluindo as cinco
degradações que publicavam um vídeo capado sem marcar o run — e **três** dead-man's
switches.

⚠️ **Três, e não o único desenhado aqui.** A proposta original era um check pingado no
sucesso, com alerta em 12h. Isso confunde "o pipeline quebrou" com "o scout não achou
material bom": num fim de semana devagar, 12h sem run concluído é plausível com tudo
funcionando, e alarme falso de madrugada treina a pessoa a ignorar o alerta. Ficou assim:

| Check | Quem pinga | Janela |
|---|---|---|
| `alive` | `maintenance_loop` do orchestrador, 15 min | curta |
| `scout` | fim de cada ciclo do scout, 1h | média |
| `produced` | **só** quando um run termina em `scheduled` | 24–36h |

Com `alive` e `scout` cobrindo "os laços estão de pé", o `produced` pode ter janela larga
sem virar ponto cego.

**Falta configurar** (não é código): criar os três checks no Healthchecks.io e preencher
`HEALTHCHECK_ALIVE_URL`, `HEALTHCHECK_SCOUT_URL` e `HEALTHCHECK_PRODUCED_URL` no `.env`,
mais `CALLMEBOT_PHONE`/`CALLMEBOT_APIKEY`. Sem essas vars tudo é no-op — o código roda
idêntico e não manda nada.

### Camada 4 — host (disco, backup)

O plano original delegava esta camada ao sistema de notificação do Proxmox 8.1+ (targets
SMTP/Gotify/webhook, matchers por evento). **Sem Proxmox isso não existe** — no Debian, é
um cron e o mesmo destino WhatsApp das outras camadas.

O que precisa de olho, porque cresce sozinho e ninguém percebe até parar:

| O quê | Como cresce | Contenção |
|---|---|---|
| Imagens Docker | ~36 GB, salto a cada rebuild | `docker image prune` |
| Build cache | sem limite | `docker builder prune` periódico |
| `outputs/` | ~3 vídeos/dia, monotônico | **nada hoje** — ver "em aberto" |
| Logs | limitados no compose (`10m` × 3) | já resolvido |

Um cron diário comparando `df --output=pcent /` com um limiar (80%) e mandando o alerta
pelo `NOTIFY_WEBHOOK_URL` cobre o caso — é a mesma URL que o resto do sistema já usa, sem
serviço novo e sem RAM. Vale somar `smartd` (o disco é um SSD/HD único, sem RAID: se ele
morrer, morre tudo) e `unattended-upgrades` para os patches de segurança.

### A entrega no WhatsApp

Duas opções, e a escolha depende de quanto se quer manter de pé:

**CallMeBot** — recomendado para começar. Cloud, gratuito, sem cadastro: manda-se uma
mensagem para o contato do bot, recebe-se uma APIKEY, e a partir daí um único HTTP GET
envia a mensagem. Zero infraestrutura, zero RAM, e é explicitamente feito para alerta de
monitoramento. Limitação: uso pessoal apenas, e é um terceiro no caminho.

**WAHA** — self-hosted, open source, sem limite de mensagens; o Uptime Kuma tem provider
nativo para ele (além de Whapi e Evolution). O custo é RAM: é um cliente WhatsApp de
verdade, com sessão que cai e QR para reescanear. O engine NOWEB é o leve; o baseado em
Chromium não cabe nesta máquina.

Como todas as quatro camadas falam webhook, e o CallMeBot é um GET, ele serve de destino
comum para as quatro. Trocar por WAHA depois é mudar a URL.

---

## O que continua em aberto

Hospedar em casa resolve compute barato, não durabilidade.

### 🔲 TODO — Backup e retenção (adiado por decisão, 14/08/2026)

Adiado deliberadamente: não bloqueia subir, e a decisão de *quanto tempo guardar* é de
produto, não de infra. Registrado aqui para não virar descoberta no dia em que o disco
morrer. Quando for feito, o desenho já está escolhido:

- **Backup.** `pg_dump` noturno dos **3** bancos (`orchestrator`, `blender_worker`,
  `content_scout`) para o R2. É aqui que a nuvem vale: compute em casa, estado replicado
  fora, sem custo de egress. O bucket e as credenciais **já existem** no `.env`, então é
  um script e uma entrada de cron — não há infraestrutura nova a montar.
- **Retenção dos `outputs/`.** Nada é apagado hoje, e o crescimento é monotônico. Falta
  decidir o prazo: o vídeo publicado já está no TikTok, então o que se guarda é histórico
  e matéria-prima para reedição, não o produto.
- **O disco é único e sem RAID.** Não há redundância local nenhuma; a réplica no R2 *é* a
  estratégia de durabilidade, não um complemento dela.

⚠️ **A urgência subiu quando a máquina foi medida.** São 193 GB livres, não os 500 GB que
este documento assumia — e ~36 GB já vão para as imagens. Continua confortável para ~3
vídeos/dia, mas a folga é menor do que o plano supunha.

### Autenticação

Enquanto não existir, a stack não pode sair da LAN. Vale lembrar que nesta máquina o
`server` tem `sudo` sem senha (ver `servidor.md`), o que torna a regra mais forte, não
mais fraca: **rede local apenas**, acesso remoto por Tailscale, nunca por port forwarding.

---

## Quando reconsiderar a nuvem

Se o volume subir a ponto do render virar gargalo, ou se houver publicação em vários
canais. O caminho natural então é trocar MinIO por R2 e mandar só o render para uma
máquina cloud sob demanda.

O compose atual é portátil justamente para que essa migração não doa depois — mas é
otimização para um problema que ainda não existe.
