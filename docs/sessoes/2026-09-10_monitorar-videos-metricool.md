# Monitorar quais vídeos dão certo → Metricool

- **Período:** 10/09/2026
- **Sessão:** `af9d960e-9723-4407-801f-f25552729049`, `ee660eb6-f8e5-4ba6-86a6-9b6c4602ba68`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 12

## Pedido
Uma forma de saber qual vídeo dá certo; avaliar o Metricool com sinceridade; planejar a integração, pela análise de concorrentes. (A sessão `ee660eb6` foi o começo interrompido desta conversa.)

## O que foi feito
- Já existia histórico de métricas por post (`/metrics/sync`), só agregado por variante.
- Recomendação: usar o Metricool para visão de canal e escrever código só para cruzar com dados do scout. Plano free: 1 marca, 30 dias de histórico, concorrentes, sem API REST mas com **Metricool MCP**.
- Implementados endpoints de ingestão de concorrentes no `tiktok_poster` (migration `004`, 14 testes), sem cliente de saída.
- MCP conectado pelo usuário; escrito o prompt do dry run para uma sessão nova (ver `c4db7a69`).
