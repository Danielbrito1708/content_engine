# Resumos das sessões

Um arquivo por sessão do Claude Code neste repositório, em ordem de início. Cada resumo diz o que
foi pedido, o que foi feito ou decidido e o que ficou pendente **naquele momento** — não é fonte de
estado atual. Para estado, ver `docs/operations.md`; para decisões, `docs/decisions.md`.

Gerados em 14/10/2026 a partir dos transcripts locais (`~/.claude/projects/…content-engine/*.jsonl`).
Sessões que foram só uma tentativa interrompida da mesma conversa estão agrupadas na sessão
principal. Segredos colados no chat (API keys, tokens) foram omitidos de propósito.

**Para acrescentar uma sessão nova:** criar `AAAA-MM-DD_<slug>.md` com o mesmo cabeçalho
(período, id da sessão, branch) e as seções *Pedido* / *O que foi feito* / *Ficou em aberto*,
e adicionar a linha na tabela abaixo.

| Data | Sessão |
|---|---|
| 15/08/2026 | [YouTube não posta / aquecimento de contas](2026-08-15_youtube-nao-posta.md) |
| 15/08/2026 | [Integração de novas contas TikTok/YouTube (multi-conta)](2026-08-15_integracao-novas-contas.md) |
| 15/08/2026 | [Quais informações ficam no banco](2026-08-15_o-que-fica-no-banco.md) |
| 16/08/2026 | [Notificações do WhatsApp → ntfy; cadastro da redditors-inc em produção](2026-08-16_notificacoes-ntfy-e-redditors-inc.md) |
| 26/08/2026 | [Escalabilidade e camadas: infra × formato × serviço](2026-08-26_escalabilidade-e-camadas.md) |
| 29/08/2026 | [Origem da LLM, custo de escala, R2 e PR #26](2026-08-29_origem-da-llm-e-custos.md) |
| 30/08/2026 | [Contas com formatos diferentes → edição declarativa (origem do VSEL)](2026-08-30_edicao-declarativa-vsel.md) |
| 04/09/2026 | [Clipes de 3 s no fundo e template por vídeo (PR #28)](2026-09-04_clipes-curtos-e-template-id.md) |
| 06/09/2026 | [Alterações pendentes e worktrees](2026-09-06_alteracoes-pendentes-e-worktrees.md) |
| 06/09/2026 | [Segunda conta de Reddit no TikTok: multi-conta Fase 1 e rampa de aquecimento](2026-09-06_multi-conta-fase-1-e-aquecimento.md) |
| 06/09/2026 | [API de análise das contas e testes A/B por variante](2026-09-06_metricas-e-testes-ab.md) |
| 06/09/2026 | [Docker não funciona](2026-09-06_docker-nao-funciona.md) |
| 06/09/2026 | [Nota: validar roteiro em conta pequena](2026-09-06_nota-validar-em-conta-pequena.md) |
| 06/09/2026 | [Vídeos de até 40 segundos e rollback](2026-09-06_videos-de-ate-40s.md) |
| 06/09/2026 | [Passo a passo para conectar outra conta (PR #29)](2026-09-06_conectar-outra-conta.md) |
| 06/09/2026 | [Atraso entre mudança no repo e vídeo publicado](2026-09-06_atraso-entre-repo-e-post.md) |
| 06/09/2026 | [Novo formato com imagem de abertura; limites do VSEL e keyframes](2026-09-06_limites-do-vsel-e-keyframes.md) |
| 07/09/2026 | [Regras dos CLAUDE.md, prompt para projeto novo e refatoração do CLAUDE.md](2026-09-07_regras-claude-md-e-refatoracao.md) |
| 07/09/2026 | [Histórias com arco de redenção; novos subreddits (PR #30)](2026-09-07_arco-de-redencao-e-novos-subs.md) |
| 07/09/2026 | [Provider de TTS ElevenLabs](2026-09-07_elevenlabs-tts.md) |
| 07/09/2026 | [Caixa de entrada de fundos está no ar?](2026-09-07_caixa-de-fundos-no-ar.md) |
| 07/09/2026 | [Extrair roteiro de vídeo do TikTok; bug da faixa de áudio](2026-09-07_extrair-roteiro-tiktok.md) |
| 07/09/2026 | [Relatório do content_scout; upvotes via OAuth; fim do teto de tamanho](2026-09-07_relatorio-content-scout-e-oauth-reddit.md) |
| 07/09/2026 | [Ver upvotes sem credencial do Reddit](2026-09-07_acesso-ao-reddit-sem-credencial.md) |
| 07/09/2026 | [Postar na redditors-inc: round-robin de contas no scout (PR #31)](2026-09-07_round-robin-de-contas.md) |
| 08/09/2026 | [Navegadores feitos para IA](2026-09-08_navegadores-para-ia.md) |
| 10/09/2026 | [Monitorar quais vídeos dão certo → Metricool](2026-09-10_monitorar-videos-metricool.md) |
| 10/09/2026 | [Tarefa agendada: começar a postar na redditors-inc](2026-09-10_lembrete-redditors-inc.md) |
| 10/09/2026 | [redditors-inc não postava: deploy do round-robin (PR #33) e primeiro post](2026-09-10_redditors-inc-comeca-a-postar.md) |
| 10/09/2026 | [Dry run do Metricool e métricas por vídeo](2026-09-10_dry-run-metricool-e-video-metrics.md) |
| 10/09/2026 | [Teste do MCP do Metricool](2026-09-10_teste-mcp-metricool.md) |
| 10/09/2026 | [Coleta pública de dados do TikTok → serviço tiktok_scout](2026-09-10_servico-tiktok-scout.md) |
| 11/09/2026 | [Método científico para validar estratégias (motor de experimentos)](2026-09-11_motor-de-experimentos.md) |
| 12/09/2026 | [Ideia: highlights de lutas com narrativa por IA](2026-09-12_ideia-highlights-lutas.md) |
| 12/09/2026 | [Formato de vídeo sobre política](2026-09-12_formato-politica.md) |
| 12/09/2026 | [Skill /baixar-video](2026-09-12_skill-baixar-video.md) |
| 12/09/2026 | [Monitorar posts do X/Twitter](2026-09-12_monitorar-twitter.md) |
| 12/09/2026 | [Upscaling de vídeo 1080p → 4K](2026-09-12_upscaling-de-video.md) |
| 15/09/2026 | [Alterações dos últimos dias; duração fora das alavancas de experimento](2026-09-15_alteracoes-recentes-e-revert-duracao.md) |
| 15/09/2026 | [O que não foi commitado (+13 mil linhas)](2026-09-15_o-que-nao-foi-commitado.md) |
| 15/09/2026 | [DeepSeek / GLM no Claude Code](2026-09-15_outros-modelos-no-claude-code.md) |
| 15/09/2026 | [Ideia: resumo de filmes](2026-09-15_ideia-resumo-de-filmes.md) |
| 15/09/2026 | [Decisões hardcoded na criação dos vídeos](2026-09-15_decisoes-hardcoded.md) |
| 16/09/2026 | [Hashtags, restrição do TikTok e filtro de palavrão](2026-09-16_hashtags-e-filtro-de-palavrao.md) |
| 16/09/2026 | [Ideia: cortes de canais do YouTube com animações](2026-09-16_ideia-cortes-de-canais.md) |
| 16/09/2026 | [Contêiner por formato? → design multi-formato (docs/formatos.md)](2026-09-16_motor-multi-formato.md) |
| 16/09/2026 | [Como redesenhar a estrutura/arquitetura (só análise)](2026-09-16_redesenho-da-estrutura.md) |
| 17/09/2026 | [Baixar imagem de post do TikTok](2026-09-17_baixar-imagem-tiktok.md) |
| 17/09/2026 | [Pergunta fora do escopo (IA para modelos)](2026-09-17_pergunta-fora-do-projeto.md) |
| 20/09/2026 | [SSH do servidor](2026-09-20_ssh-do-servidor.md) |
| 20/09/2026 | [Ideia: cortes de futebol com IA](2026-09-20_ideia-highlights-futebol.md) |
