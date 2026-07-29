# Deploy — plano para a máquina 24h

Este documento é o plano de deploy do `content_engine` na máquina de casa. Ele existe
para ser lido **na hora de subir**, não antes: nada aqui foi aplicado ainda.

A seção "Operação 24h" do `vision.md` descreve o que o `docker-compose.yml` já garante
sozinho. Este documento cobre o que fica **fora** do compose — a máquina, o hipervisor,
os limites e o monitoramento.

---

## O alvo

| | |
|---|---|
| Hardware | i5 8ª geração (4–6 núcleos), 8 GB RAM, 500 GB disco |
| Hipervisor | Proxmox VE (já em uso) |
| Carga | 3 vídeos/dia (teto de publicação), scout a cada 1h |
| Exposição | nenhuma — todo o tráfego é de saída |

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

## O orçamento de RAM é a única restrição real

CPU e disco sobram. Os 8 GB não.

| Item | Consumo |
|---|---|
| Proxmox VE (host) | ~1–1,5 GB |
| Postgres + MinIO + 6 FastAPI | ~2–2,5 GB |
| Modelo Whisper residente | incluído acima (`base` em `int8`) |
| **Cada** render Blender 1080×1920 | ~1–2 GB (estimativa — medir no primeiro render) |

O Whisper é a boa notícia: `transcribe.py:12` instancia `WhisperModel(model_name,
device="cpu", compute_type="int8")` com `base` como default (`config.py:109`) — é o
modelo pequeno, quantizado. Fica residente num global de módulo, então é ocupação
constante e não pico.

**O problema é a concorrência de render, e ela é ilimitada hoje.** `content_scout/config.ini:43`
permite 5 runs simultâneos (`max_pending_runs = 5`), e o `blender_worker` despacha via
`BackgroundTasks` sem fila nem semáforo — tech debt já registrado no `CLAUDE.md` do
serviço. Nada impede 5 processos Blender ao mesmo tempo, o que em 8 GB estoura.

Quando estoura, quem o OOM killer derruba é arbitrário. Se for o Postgres, perde-se o
estado de todos os runs — bem pior que perder um render, que é recuperável.

---

## Ajustes antes de subir

### 1. LXC, não VM

Com 8 GB, o hipervisor já cobra seu pedaço; uma VM cobraria mais um kernel convidado e
uma reserva fixa de memória. Um LXC compartilha memória com o host e tem overhead na casa
de 100 MB. A diferença — algo perto de 1 GB — é 12% do total da máquina.

Requisitos para Docker dentro de LXC: `nesting=1` e `keyctl=1` nas features do container.
Sem eles o Docker não usa keyrings do kernel nem redes overlay.

A documentação oficial do Proxmox recomenda VM para Docker, por isolamento e live
migration. Para um serviço interno, confiável e sem exposição, LXC é o trade-off certo
aqui — o que manda é o orçamento de memória.

### 2. Limitar o ARC do ZFS (se o Proxmox foi instalado em ZFS)

Este é o consumo invisível mais perigoso da lista: o ARC do ZFS usa **metade da RAM por
padrão**. Numa máquina de 8 GB são 4 GB que somem antes de qualquer serviço subir.

Conferir e, se for o caso, limitar `zfs_arc_max` para algo entre 1 e 2 GB. Se o Proxmox
estiver em ext4/LVM, isso não se aplica.

(Nota adicional: em ZFS, o `overlay2` do Docker dentro de LXC só funciona a partir do
OpenZFS 2.2 / PVE 8.1, que trouxe suporte nativo a overlayfs. Em versão anterior, o
storage driver degrada.)

### 3. Baixar `max_pending_runs` de 5 para 2

Uma linha em `content_scout/config.ini`. É o freio mais barato que existe: limita quantos
pipelines ficam em voo, o que limita quantos Blender rodam junto.

Com 3 publicações/dia, 2 em produção simultânea mantém a fila cheia — não há perda de
throughput, só remoção de um pico que a máquina não aguenta.

### 4. `mem_limit` no `blender_worker`

Contraintuitivo, mas é proteção. Com limite, quem morre por falta de memória é o render:
ele falha, fica registrado, e existe recuperação de runs órfãos. Sem limite, quem morre é
qualquer processo, inclusive o banco.

Trocar uma falha catastrófica por uma recuperável é o ponto todo.

### 5. Swap de 8–16 GB

Numa máquina que renderiza 3 vezes por dia, swap transforma "OOM" em "ficou lento por
dois minutos". Para esse duty cycle é a troca certa.

### 6. Buildar antes, não durante

`docker compose build` das 6 imagens (com o download do Blender) é pesado. Rodar isso com
um render em andamento em 8 GB é pedir problema. Build primeiro, `up` depois.

E atenção ao `up -d` sem `--build`: ele sobe a imagem antiga em silêncio.

---

## Monitoramento

O requisito é ser avisado no WhatsApp quando algo der errado, 24h, sem ninguém olhando.

**A regra que organiza tudo:** o monitor não pode viver só na máquina que ele monitora. Se
a caixa morre, um Uptime Kuma rodando nela morre junto e não avisa ninguém. Por isso a
divisão em camadas — cada uma pega uma classe de falha que as outras não pegam.

### Camada 1 — dead-man's switch externo (pega a morte total)

A mais importante, e é de graça. Um cron na máquina pinga um serviço externo
(Healthchecks.io) a cada 5 minutos. Se os pings **param**, o serviço externo avisa.

É a única camada que pega queda de luz, internet fora, kernel panic — nada rodando na
máquina consegue reportar a própria morte.

### Camada 2 — saúde dos serviços (pega "um serviço adoeceu")

Uptime Kuma num LXC próprio (~100–150 MB), monitorando os seis endpoints `/health`. Dá
granularidade: qual serviço, desde quando, histórico de resposta.

Fica em LXC separado da stack de propósito — se o Docker do content_engine cair inteiro,
o monitor continua de pé para contar.

### Camada 3 — o pipeline parou de produzir (pega o silêncio)

Esta é a que mais importa aqui e a que as outras duas **não** pegam: todos os serviços
podem responder `200` e mesmo assim nenhum vídeo sair. O `vision.md` já registra que um
run `failed` não notifica ninguém.

Implementação: um segundo check no Healthchecks.io que o orchestrator pinga **apenas após
um run concluir com sucesso**. Sem sucesso em 12h, alerta. Um dead-man's switch de
produto, não de infraestrutura.

### Camada 4 — host (disco, backup)

O Proxmox 8.1+ tem sistema de notificação próprio, com targets (SMTP, Gotify, webhook) e
matchers para rotear evento por evento — configurável em Datacenter → Notifications. Um
webhook aqui cobre disco cheio e falha de backup sem gastar RAM nenhuma.

Vale lembrar do que cresce sozinho: o build cache do Docker não tem limite
(`docker builder prune` periodicamente) e os `outputs/` não têm retenção.

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

Hospedar em casa resolve compute barato, não durabilidade:

- **Backup.** Hoje um disco que morre leva o Postgres e todos os `outputs/` junto. É aqui
  que a nuvem vale: `pg_dump` noturno dos 3 bancos + os vídeos para o R2 (sem custo de
  egress). Compute em casa, estado replicado fora.
- **Retenção.** Nada é apagado hoje. Em ~3 vídeos/dia o crescimento é lento, mas é
  monotônico.
- **Autenticação.** Enquanto não existir, a stack não pode sair da LAN.

---

## Quando reconsiderar a nuvem

Se o volume subir a ponto do render virar gargalo, ou se houver publicação em vários
canais. O caminho natural então é trocar MinIO por R2 e mandar só o render para uma
máquina cloud sob demanda.

O compose atual é portátil justamente para que essa migração não doa depois — mas é
otimização para um problema que ainda não existe.
