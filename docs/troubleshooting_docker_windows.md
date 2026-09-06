# Docker Desktop travado no Windows (sockets órfãos)

Guia de bolso para o problema que aconteceu em 06/09/2026: `docker info`, `docker ps` e
`docker compose up` ficam pendurados **sem erro nenhum** — nem timeout, nem mensagem — depois
de o Docker Desktop ter sido desligado de forma suja (queda de energia, `Stop-Process` forçado,
crash do WSL, etc.).

## Sintoma

- `docker ps` / `docker info` / `docker compose up` nunca retornam, sem erro.
- `Get-Process` mostra dezenas de processos `Docker Desktop`, `com.docker.backend`,
  `docker-ai`, `docker-mcp` rodando — o app **parece** ativo.
- Mas `Get-Service com.docker.service` mostra `Stopped`, e `wsl -l -v` mostra a distro
  `docker-desktop` como `Stopped`. O motor (engine) nunca subiu; só a casca do app está de pé.

## Causa

Ao subir, o backend do Docker (`com.docker.backend`) tenta remover sockets Unix (arquivos de
0 bytes, tipo *reparse point*) deixados por uma sessão anterior antes de recriá-los. Depois de
um desligamento sujo, o Windows às vezes recusa remover esses arquivos por qualquer API nativa
— `Remove-Item`, `cmd /c del`, até `fsutil reparsepoint delete` — todos falham com:

```
Não é possível o acesso ao arquivo pelo sistema.   (Windows error 1920 / ERROR_CANT_ACCESS_FILE)
```

Sem conseguir remover o socket, o backend trava silenciosamente na inicialização daquele
serviço específico. Os arquivos problemáticos ficam em:

```
%LOCALAPPDATA%\Docker\run\dockerInference
%LOCALAPPDATA%\Docker\run\userAnalyticsOtlpHttp.sock
%LOCALAPPDATA%\docker-secrets-engine\engine.sock
```

Confirme lendo `%LOCALAPPDATA%\Docker\backend.error.json` — se existir, a mensagem
`"...: The file cannot be accessed by the system."` referenciando um `.sock` confirma o
diagnóstico. **É whack-a-mole**: o backend trava em um socket, você remove, ele sobe mais um
passo e trava no próximo. Nas vezes em que isso aconteceu, foram até 3 rodadas antes de todos
os sockets órfãos sumirem.

## Correção

APIs nativas do Windows não conseguem apagar esse tipo de arquivo, mas o **WSL consegue** (via
`/mnt/c/...`, usando `unlink` do lado Linux). Passo a passo:

```powershell
# 1. Mata tudo que for processo do Docker
Get-Process | Where-Object { $_.Name -match "^(Docker Desktop|com\.docker\.backend|docker|docker-ai|docker-mcp|docker-compose|com\.docker\.diagnose|com\.docker\.dev-envs|com\.docker\.extensions|com\.docker\.vpnkit)$" } |
  Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 2. Desliga o WSL por completo (libera qualquer handle preso)
wsl --shutdown
Start-Sleep -Seconds 2

# 3. Lista o que sobrou nos dois diretórios conhecidos
wsl -d Debian -e ls -la /mnt/c/Users/Markov/AppData/Local/Docker/run/ `
  /mnt/c/Users/Markov/AppData/Local/docker-secrets-engine/

# 4. Apaga cada arquivo encontrado pelo nome (nunca com wildcard "*")
wsl -d Debian -e rm -fv `
  /mnt/c/Users/Markov/AppData/Local/Docker/run/dockerInference `
  /mnt/c/Users/Markov/AppData/Local/Docker/run/userAnalyticsOtlpHttp.sock `
  /mnt/c/Users/Markov/AppData/Local/docker-secrets-engine/engine.sock

# 5. Desliga o WSL de novo e relança o Docker Desktop
wsl --shutdown
Start-Sleep -Seconds 2
Start-Process -FilePath "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

Depois de ~60-90s, confirme:

```powershell
wsl -l -v                 # docker-desktop deve estar "Running"
Test-Path "$env:LOCALAPPDATA\Docker\backend.error.json"   # deve ser False
```

```bash
docker ps                 # deve responder na hora, sem pendurar
```

**Se travar de novo em outro socket diferente destes três**, o `backend.error.json` vai dizer
qual caminho ele tentou remover — repita os passos 1–5 trocando o nome do arquivo. Containers
com `restart: unless-stopped` (como o `db` do `docker-compose.yml`) voltam sozinhos assim que o
engine sobe, sem precisar de `docker compose up` de novo.

## Se nada disso resolver

Reiniciar o Windows resolve na certa (libera qualquer handle que nem o WSL consiga tocar), mas
derruba tudo mais que estiver rodando na máquina — só como último recurso, e avisando antes.
