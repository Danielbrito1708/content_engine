# Servidor — acesso e ambiente

Como chegar na máquina de casa onde o `content_engine` roda (ou vai rodar). Para o
**plano** de deploy — dimensionamento, limites de RAM, monitoramento — ver `deploy.md`;
este documento é só o acesso e o que já foi verificado na caixa.

---

## Acesso

```bash
ssh server@192.168.0.106
```

| | |
|---|---|
| IP | `192.168.0.106` (LAN, **DHCP** — ver ressalva abaixo) |
| Hostname | `debian` |
| Usuário | `server` (uid 1000) |
| Autenticação | chave pública `~/.ssh/id_ed25519` — **sem senha** |
| `sudo` | **sem senha** (`NOPASSWD:ALL`) |
| Sistema | Debian 13.6 (trixie), kernel 6.12.101, x86_64 |
| Interface | `enp1s0` |

Funciona com `BatchMode=yes`, então agentes conseguem rodar comandos sem interação:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 server@192.168.0.106 "comando"
```

---

## Armadilhas (todas custaram tempo na configuração inicial)

**Root não loga por SSH.** `root@192.168.0.106` recusa a senha mesmo estando correta —
é o `PermitRootLogin prohibit-password`, default do Debian. Use sempre `server` + `sudo`.
Localmente (console ou `su -` a partir do `server`) o root **tem** senha e funciona: nesta
instalação a senha de root foi definida no instalador, e é por isso que o primeiro usuário
ficou fora do grupo `sudo`.

**Prompts de senha não funcionam via agente.** As ferramentas de shell do Claude Code não
têm stdin interativo — qualquer comando que peça senha (`sudo` sem NOPASSWD, `ssh` por
senha, `su`) trava e falha por timeout. Não há `sshpass` nem `plink` na máquina Windows.
Por isso o acesso foi montado inteiro em cima de chave + `sudo` sem senha. Se algum dia
isso for revertido, o acesso automatizado morre junto.

**`ssh-copy-id` não existe no PowerShell.** É comando do Git Bash; o OpenSSH nativo do
Windows não o inclui. Pelo PowerShell, o equivalente é:

```powershell
ssh user@host "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo 'CHAVE_PUBLICA' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

**⚠️ A máquina suspende sozinha por inatividade.** Se o SSH der `Connection timed out` e o
IP sumir da tabela ARP, o primeiro palpite não é "desligaram" nem "mudou de IP" — é que ela
dormiu. Aconteceu em 14/08/2026: suspendeu e voltou às 15:07:28 **dentro do mesmo boot** das
14:28:41, o que é a assinatura do suspend/resume (num reboot, `uptime -s` mudaria).

Para distinguir os três casos:

```bash
ssh server@192.168.0.106 "uptime -s; uptime -p"       # mesmo boot = foi suspensão
sudo journalctl -b -g 'resume|Suspending' --no-pager  # confirma na fonte
```

Acordar só com teclado/mouse, botão de power ou Wake-on-LAN — não há como acordar por SSH,
porque a placa de rede não responde dormindo.

A causa era o GNOME (`graphical.target` + GDM sobre Wayland), que suspende por ociosidade
mesmo na tela de login.

✅ **Corrigido em 14/08/2026**, com as duas medidas em `deploy.md` → item 1: o boot passou a
`multi-user.target` (sem interface gráfica) e os quatro targets de sleep foram mascarados.
O diagnóstico acima fica registrado porque o sintoma é confuso e pode voltar se alguém
reverter qualquer uma das duas.

**⚠️ O IP é DHCP, não reservado.** O `enp1s0` pega endereço dinâmico via NetworkManager.
Sobreviveu a um reboot mantendo o `.106`, mas isso é comportamento do roteador, não
garantia. Se a máquina voltar com outro IP, todo comando fixado em `.106` quebra. A
correção definitiva é reserva de DHCP no roteador (amarrar o MAC ao `.106`) — **pendente**.

Para localizar a máquina quando o IP mudar, a partir do Windows:

```powershell
arp -a | Select-String "192.168.0"
```

Se o IP não aparecer na tabela ARP, a máquina está desligada ou fora da rede. Cuidado com
o `ping` no Windows: ele reporta "0% de perda" mesmo quando o host está fora, porque conta
as respostas de *host inacessível* do roteador como pacotes recebidos. A tabela ARP é o
sinal confiável, não o ping.

---

## Como o acesso foi configurado

Registrado para poder reproduzir numa máquina nova ou reverter aqui.

1. Chave pública `id_ed25519` do Windows anexada em `/home/server/.ssh/authorized_keys`
   (`700` no diretório, `600` no arquivo).
2. `usermod -aG sudo server` — o usuário não estava no grupo `sudo`.
3. `/etc/sudoers.d/010_server-nopasswd`, modo `440`, com a linha:

   ```
   server ALL=(ALL) NOPASSWD:ALL
   ```

   Validado com `visudo -cf` **antes** de instalar no nome definitivo — um `sudoers`
   malformado quebra o `sudo` inteiro e o conserto exige acesso físico.

Reverter o sudo sem senha: `sudo rm /etc/sudoers.d/010_server-nopasswd`.

**Implicação de segurança, explícita:** qualquer processo rodando como `server` vira root
sem barreira, incluindo agentes. É uma escolha deliberada para permitir automação, aceita
no contexto de uma máquina em LAN atrás de NAT. Vale a mesma regra do `deploy.md`: **nada
disso pode sair da rede local** — acesso remoto por Tailscale, nunca por port forwarding.

---

## Inventário — verificado em 14/08/2026

| | |
|---|---|
| Máquina | Dell OptiPlex 3050, **bare metal** (`systemd-detect-virt` → `none`) |
| CPU | Intel i5-6500T — 4 núcleos, 4 threads, 2,5 GHz, 35 W |
| RAM | 12 GB (`MemTotal` 12135828 kB; ~700 MB em uso ocioso) |
| Disco | `sda` 223,6 GB — `sda1` 212 GB em `/` (**ext4**), 193 GB livres |
| Swap | `sda5`, 11,4 GB, partição (não arquivo), ativa |
| MAC `enp1s0` | `84:7b:eb:ff:77:8d` — para a reserva de DHCP |
| Ambiente | `multi-user.target` — **sem interface gráfica desde 14/08/2026** (GNOME/GDM continuam instalados, só não sobem no boot) |

**Esta É a máquina do `deploy.md`**, e a ressalva anterior ("não assuma") está resolvida:
não há Proxmox nem virtualização nenhuma. O `deploy.md` foi corrigido com estes números —
ele assumia i5 de 8ª geração, 8 GB e 500 GB, e as três estavam erradas.

⚠️ **Duas linhas desta tabela descrevem o próximo boot, não a sessão de agora.**
`systemctl set-default multi-user.target` só vale a partir do próximo boot, e a máquina não
reiniciou depois da mudança. Conferido em 14/08/2026, mesmo boot das 14:28:42: `gdm` e
`graphical.target` continuam `active`, e a RAM ociosa continua em **733 MB** — ou seja, a
economia de memória atribuída à saída do GNOME **ainda não aconteceu**, e o número da linha
"RAM" continua sendo o medido *com* GNOME. Decidido deixar assim: o ganho é de algumas
centenas de MB numa máquina com ~11 GB livres, e não vale um reboot dedicado. Ele vem de
graça no primeiro restart, e é ali que vale re-medir com `free -m`.

**Isso não afeta a suspensão**, que é o que importava. O `mask` dos quatro targets de sleep
vale imediatamente, independente de boot e independente do GNOME estar rodando — conferido:
os quatro respondem `masked`. O `gsettings` do GDM ainda diz `'suspend'` e isso é esperado:
o `mask` bloqueia o pedido no systemd, abaixo de quem o faz. Era exatamente esse o motivo
de mascarar em vez de só corrigir o `gsettings`.

Comando que levantou tudo isto, para repetir depois de qualquer mudança:

```bash
ssh -o BatchMode=yes server@192.168.0.106 "systemd-detect-virt; lscpu | grep -E 'Model name|^CPU\(s\)'; free -h; df -h /; findmnt -no FSTYPE /; cat /proc/swaps; docker --version; id -nG; ip link show enp1s0 | grep ether"
```

⚠️ **`swapon` e outros binários de `/sbin` não estão no PATH** de uma sessão SSH não
interativa — o comando falha com "command not found" mesmo existindo. Use `/proc/swaps`,
ou o caminho absoluto.

### Interface gráfica — ligar e desligar por comando

Desde 14/08/2026 a máquina boota em `multi-user.target`, sem interface. **Nada foi
desinstalado** — GNOME, GDM e Wayland continuam no disco, só não são iniciados. Reativar é
um comando, sem reinstalar pacote nenhum.

| Objetivo | Comando |
|---|---|
| Subir a interface **agora**, só nesta sessão | `sudo systemctl start gdm` |
| Derrubar a interface **agora** | `sudo systemctl stop gdm` |
| Voltar a subir **em todo boot** | `sudo systemctl set-default graphical.target` |
| Voltar a bootar **sem** interface | `sudo systemctl set-default multi-user.target` |
| Conferir o que está valendo | `systemctl get-default` e `systemctl is-active gdm` |

Os dois eixos são independentes, e é isso que dá o comportamento pedido: o `start`/`stop`
age no estado atual e não sobrevive ao reboot; o `set-default` decide o boot e não muda o
estado atual. Ligar a interface para uma tarefa pontual não a traz de volta permanentemente
— basta o `start`, e o próximo boot volta a ser limpo sozinho.

O `stop gdm` derruba a sessão gráfica local imediatamente, então não rode isso com alguém
usando teclado e monitor na máquina. **O SSH não é afetado por nenhum dos quatro comandos.**

⚠️ Se um dia a interface voltar em definitivo, revisar a suspensão junto: o `mask` dos
targets de sleep continua de pé e é o que segura o GNOME, mas ele é reversível por engano
com um `unmask`. Interface ligada **sem** o mask = máquina dormindo de novo.

### O que roda na máquina — desde 14/08/2026

**A stack do `content_engine` está no ar aqui**, e esta é a máquina de produção: a stack da
máquina Windows foi desligada na mesma data, para não haver dois produtores publicando no
mesmo perfil do Buffer (ver `deploy.md` → "A virada da máquina local para o servidor").

| | |
|---|---|
| Docker | 29.7.2 + Compose v5.4.0, repo oficial `trixie`, `docker.service` habilitado |
| Repo | `/home/server/content_engine`, clonado por SSH |
| `.env` | `/home/server/content_engine/.env`, modo 600 — copiado por `scp`, **não está no git** |
| Subir/descer | `cd ~/content_engine && docker compose up -d` / `down` |
| Portas | as mesmas do `CLAUDE.md`, agora em `192.168.0.106` em vez de `localhost` |

**A máquina puxa do GitHub com uma deploy key própria**, `~/.ssh/id_ed25519_deploy`,
registrada no repositório como **read-only** e selecionada por um bloco `Host github.com` no
`~/.ssh/config`. Ela não é a chave de acesso SSH do Windows, e é read-only de propósito: a
máquina de deploy puxa, nunca empurra. Para atualizar o código lá:

```bash
ssh server@192.168.0.106 "cd ~/content_engine && git pull && docker compose up -d --build"
```

⚠️ O `--build` não é opcional — ver o aviso sobre `up -d` sem `--build` no `CLAUDE.md`.

### Os logs ficam no journal, não dentro do container

Desde 15/08/2026 o compose usa o driver `journald`. O motivo foi medido: o `json-file` guarda o log **dentro do container**, e `up -d --build` recria o container e apaga tudo. Dois runs falharam naquele dia com 500 do `tiktok_poster`, e o rebuild feito para investigar destruiu justamente o traceback que explicaria a falha — log que não sobrevive ao deploy não serve para diagnosticar o que motivou o deploy.

```bash
# Um serviço, com o log de antes do último rebuild
journalctl CONTAINER_NAME=content_engine-tiktok_poster-1 --since "2 hours ago" --no-pager

# A stack inteira, seguindo ao vivo
journalctl -f CONTAINER_NAME=content_engine-orchestrator-1
```

`docker compose logs` continua funcionando, mas só mostra o container atual. Para olhar antes de um rebuild, é o `journalctl`.

O journal desta máquina é **persistente** (`/var/log/journal` existe), então também sobrevive a reboot. A retenção passa a ser a do journald (`SystemMaxUse`, default de 10% do disco — hoje 16 MB usados de 208 GB), no lugar do `max-size`/`max-file` que o `json-file` tinha.

⚠️ **O driver só existe em host Linux com systemd.** Num Docker Desktop (a máquina Windows) o `up` recusa; lá, sobrepor com `json-file` num `docker-compose.override.yml` local.

### O que falta na máquina

- **Reserva de DHCP** no roteador, amarrando `84:7b:eb:ff:77:8d` ao `.106`. Ficou mais
  urgente agora que a stack roda aqui: se o IP mudar, todo comando fixado no `.106` quebra.
- **Monitoramento** — os três checks do Healthchecks.io, o cron do disco e o Uptime Kuma.
  Ver `deploy.md` → "Monitoramento".
