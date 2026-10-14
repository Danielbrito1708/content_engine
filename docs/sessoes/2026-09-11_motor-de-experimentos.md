# Método científico para validar estratégias (motor de experimentos)

- **Período:** 11/09/2026
- **Sessão:** `d2d698dc-5456-4a98-ae62-4fde283b7fc1`
- **Branch:** `feat/fundo-sob-demanda`
- **Mensagens do usuário:** 2

## Pedido
Um método tipo científico para validar mudanças de conteúdo: analisar o vídeo que deu certo, formular hipótese, A/B com controle, replicar e escrever o porquê; resolver o fato de que mudar a produção exige mexer em código.

## O que foi feito
- `docs/playbook/metodo.md` com gabaritos: detecção de outlier, dossiê, pré-registro, sorteio contra controle, veredito, replicação, princípio escrito.
- **Perfis de produção** (`POST /profiles`): um perfil empacota template, prompt, voz, velocidade, música e fundo; rollback = apontar de volta. Prompts do roteiro saíram do Python para arquivos.
- Coleta de métricas virou loop interno, com cada publicação medida em quatro idades.
- Ajustes ao plano: `hashtag_variant` mantido (não agrupa), tabela de poder estatístico corrigida, janela de coleta limitada a 48 h para não estourar a cota do Buffer.
- Nada commitado (depois foi para o motor "desligado no ambiente" de 11/09).
