# Dry run do Metricool e métricas por vídeo

- **Período:** 10/09/2026
- **Sessão:** `c4db7a69-8ea6-429a-b385-d50be9c11bcd`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 6

## Pedido
Dry run real da análise de concorrentes via Metricool MCP; depois, guardar localmente as métricas (o Metricool só mantém 30 dias).

## O que foi feito
- O Metricool MCP **não tem concorrentes para TikTok** (`connector=competitors` inválido para `tiktok`). Nada de dado inventado; achado documentado.
- Feature nova: **métricas por vídeo próprio** — `VideoMetricSnapshot` (migration `005`), `POST /video-metrics/snapshots`, `GET /video-metrics/latest`. Dry run com 90+ vídeos reais: `reach`, watch rate e origem de tráfego vêm sempre `null`.
- Commit `7998ea6` (branch divergida do remoto; não foi feito push).
