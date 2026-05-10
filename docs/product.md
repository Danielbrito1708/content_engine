# Produto — content_engine

Descrição do produto em linguagem natural, feature por feature. Este documento descreve **o que o sistema faz**, não como está implementado. É a referência para entender o comportamento esperado de ponta a ponta.

---

## O que é este produto

O content_engine é uma máquina de produção de conteúdo para TikTok. Você escreve um roteiro em texto simples, e o sistema cuida de todo o resto: melhora o texto, gera a narração em áudio, monta o vídeo e agenda a publicação. O objetivo é eliminar o trabalho repetitivo de produção e permitir que um único criador publique consistentemente sem depender de editores ou estúdio de gravação.

O sistema funciona completamente sozinho depois que você submete o roteiro. Você pode acompanhar o progresso em tempo real, mas não precisa intervir em nenhuma etapa.

---

## Feature 1 — Submissão do roteiro

O usuário envia um roteiro em texto simples para o sistema. O roteiro pode ser qualquer coisa: um relato de relacionamento, um fato científico curioso, uma história de drama, uma dica motivacional. Não há formato obrigatório — o texto bruto é suficiente.

Junto com o roteiro, é possível enviar metadados opcionais que ajudam o sistema a entender o contexto. Por exemplo, indicar que o conteúdo veio de um post do Reddit, ou que é direcionado a um público específico. Esses metadados não são obrigatórios, mas melhoram a qualidade do resultado final.

Assim que o roteiro é enviado, o sistema confirma que recebeu e começa a trabalhar. O retorno é imediato — o usuário não precisa esperar o processamento terminar para saber que tudo está em ordem.

---

## Feature 2 — Refinamento do roteiro pelo LLM

O texto bruto raramente está pronto para virar um vídeo viral. O sistema passa o roteiro por um modelo de linguagem que o reescreve com as regras do TikTok em mente.

**O que acontece com o texto:**

A primeira frase é transformada para prender o espectador nos primeiros dois segundos. Esse gancho é o elemento mais importante de um vídeo — sem ele, o usuário passa para o próximo. O restante do roteiro é reescrito em linguagem coloquial, direta e envolvente, preservando o conteúdo e a essência do que foi enviado.

Cada parte termina com uma chamada para ação clara: um pedido para comentar, seguir, ver a parte 2, ou qualquer comportamento específico que maximize o engajamento.

**Divisão em partes:**

Se o roteiro é longo demais para um único vídeo (mais de 60 segundos de fala), o sistema decide onde cortar. O corte é sempre em um momento de cliffhanger — um ponto de tensão narrativa que deixa o espectador querendo assistir a próxima parte. A parte 2 começa com um breve resumo do que aconteceu antes, para quem não viu o início.

**Classificação do conteúdo:**

Junto com o roteiro refinado, o sistema produz uma classificação automática do conteúdo. Ela inclui o tipo de conteúdo (drama, comédia, motivacional, educativo, entretenimento, suspense), o tom (suspense, engraçado, emocional, educativo, inspiracional, chocante), o público-alvo estimado (faixa etária, gênero, interesses) e sugestões de hashtags relevantes. Essa classificação é usada em etapas posteriores para escolher as hashtags certas e montar a legenda do post.

---

## Feature 3 — Geração do áudio

Com o roteiro pronto, o sistema gera a narração em áudio. O texto é transformado em fala por um motor de síntese de voz, e o resultado é um arquivo de áudio MP3.

A voz padrão é feminina, jovem e adequada para o estilo TikTok brasileiro. É possível configurar outras vozes, inclusive masculinas, dependendo do tipo de conteúdo. Para roteiros divididos em partes, cada parte recebe seu próprio arquivo de áudio separado.

O sistema suporta dois motores de voz: um gratuito, baseado na tecnologia da Microsoft, que funciona sem nenhuma configuração extra; e o ElevenLabs, pago, que oferece qualidade superior e controle mais fino sobre a voz. A escolha entre eles é feita por configuração, sem alterar nada no fluxo de produção.

---

## Feature 4 — Remoção de silêncios do áudio

Antes de usar o áudio no vídeo, o sistema remove automaticamente os silêncios desnecessários. Isso inclui o silêncio antes da fala começar, o silêncio depois da fala terminar, e pausas longas no meio do áudio que deixam o vídeo pesado.

Pausas naturais entre palavras e frases — as que existem para dar ritmo à fala — são preservadas. Só os silêncios acima de 500 milissegundos são removidos. O resultado é um áudio mais compacto e dinâmico, sem cortes bruscos.

Esse comportamento é ativo por padrão, mas pode ser desligado. Os limiares (quanto silêncio é considerado silêncio, qual é o mínimo para remoção) são configuráveis.

---

## Feature 5 — Montagem e renderização do vídeo

Com o áudio pronto, o sistema monta o vídeo no Blender. A montagem segue um template pré-configurado que define o visual do vídeo: o vídeo de fundo, a trilha sonora, a posição das legendas e o ritmo geral da edição.

**O que compõe o vídeo:**

O vídeo de fundo toca durante todo o tempo. A trilha sonora começa junto, com volume baixo, e some gradualmente ao final. A narração em áudio (gerada na etapa anterior) entra no momento certo, de acordo com o timing definido no template. As legendas aparecem e somem de forma animada, sincronizadas com o texto narrado.

**Legendas:**

As legendas são geradas automaticamente a partir do roteiro. O sistema estima o tempo de cada trecho com base na velocidade média de fala, e as divide em blocos de até 8 palavras. Cada bloco aparece na tela com uma animação de entrada e saída suave.

**Templates:**

O visual do vídeo é controlado por templates reutilizáveis. Um template define onde fica cada elemento na tela, qual é a duração total do vídeo, e o estilo visual (fonte das legendas, posição do texto, etc.). Criar um template bem calibrado é o trabalho manual que o sistema multiplica — cada roteiro submetido usa o mesmo template e produz um vídeo com aparência consistente.

**Processamento:**

A renderização é um processo pesado e acontece em segundo plano. O sistema não bloqueia durante esse tempo — é possível submeter novos roteiros enquanto um vídeo está sendo renderizado. O progresso pode ser acompanhado em tempo real.

---

## Feature 6 — Agendamento no TikTok

Quando o vídeo está pronto, o sistema agenda a publicação automaticamente no TikTok via Buffer. Não é necessário nenhuma ação manual — o vídeo vai para a fila e é publicado no horário certo.

**Ritmo de publicação:**

O sistema publica dois vídeos por dia, nos horários preferidos configurados (padrão: 8h e 20h UTC). Ele nunca agenda dois posts no mesmo horário — se um slot já está ocupado, avança para o próximo disponível.

**Séries:**

Quando o roteiro foi dividido em partes, cada parte é agendada em um dia consecutivo, sempre no mesmo horário. Isso mantém a consistência para os seguidores que acompanham a série.

**Fila cheia:**

O Buffer free suporta até 10 posts agendados por vez. Se a fila estiver cheia, o sistema recusa o agendamento e sinaliza o problema. O vídeo não é perdido — o pipeline fica em estado de falha e pode ser retomado manualmente.

**Caption e hashtags:**

A legenda do post é composta automaticamente com base na classificação do conteúdo. Ela inclui o CTA da respectiva parte e as hashtags selecionadas. A seleção de hashtags segue uma ordem de prioridade: primeiro as hashtags obrigatórias (sempre presentes, como `#tiktokbrasil` e `#fyp`), depois as sugeridas pelo LLM com base no conteúdo, e por último um pool de hashtags de fallback para completar até o máximo configurado. Para séries, a legenda inclui o indicador de parte ("Parte 1/2").

---

## Feature 7 — Acompanhamento do pipeline

Em qualquer momento, é possível consultar o estado de um pipeline em andamento. O sistema expõe o status em tempo real, desde que o roteiro foi submetido até a publicação no TikTok.

O status de um pipeline passa por estas fases, nesta ordem:

- **Aguardando** — o roteiro foi recebido, o processamento ainda não começou.
- **Refinando** — o LLM está reescrevendo o roteiro.
- **Refinado** — o roteiro foi melhorado e classificado, as partes foram definidas.
- **Processando** — cada parte está passando por TTS e renderização.
- **Agendando** — os vídeos estão sendo enviados para o Buffer.
- **Agendado** — todos os vídeos estão na fila do TikTok.
- **Falhou** — alguma etapa deu errado. A mensagem de erro indica o que aconteceu.

Para pipelines com múltiplas partes, cada parte tem seu próprio status interno, que mostra em que etapa ela está (gerando áudio, renderizando, concluída, etc.).

Também é possível listar todos os pipelines já criados, com paginação, para ter um histórico de tudo que foi produzido.

---

## Feature 8 — Geração de cards de comentário

Além dos vídeos, o sistema consegue gerar imagens no estilo "card de comentário do TikTok" — aquele visual de fundo escuro com bordas arredondadas, avatar e texto de comentário. Esse tipo de imagem é muito usado como overlay em vídeos de reação ou para dar contexto a uma história.

O visual do card é controlado por um arquivo de template que define: a largura da imagem, a cor e o arredondamento do fundo, a posição e o tamanho do avatar, a fonte e o tamanho do texto, e o espaçamento interno. Todos esses parâmetros podem ser ajustados sem alterar o código.

O texto do comentário é quebrado automaticamente em múltiplas linhas para caber na largura definida. A altura do card cresce de acordo com o texto — não há limite de caracteres imposto pelo sistema.

O resultado é uma imagem PNG salva no storage, pronta para ser usada como asset em um vídeo.

---

## Feature 9 — Configuração por tipo de conteúdo

O sistema é configurável em vários aspectos sem precisar alterar o código:

**Voz:** qual voz e qual motor de TTS usar. Padrão: voz feminina jovem brasileira, motor gratuito da Microsoft.

**Remoção de silêncio:** se deve remover silêncios, qual é o limiar de volume para considerar algo silêncio, e qual é a duração mínima de silêncio para remover.

**Hashtags obrigatórias:** quais hashtags sempre aparecem em todos os posts. Padrão: `#tiktokbrasil` e `#fyp`.

**Pool de hashtags:** uma lista de hashtags de fallback, editável sem reiniciar o sistema. Basta editar o arquivo e reiniciar o container.

**Horários de publicação:** em quais horários do dia os posts são agendados. Padrão: 8h e 20h UTC.

**Limite de fila:** quantos posts podem estar agendados simultaneamente antes de o sistema recusar novos agendamentos. Padrão: 10 (limite do Buffer free).

**Modelo de LLM:** qual modelo de linguagem usar para refinar os roteiros. A troca de modelo não afeta o fluxo — só a qualidade e o custo do refinamento.

**Template de vídeo:** qual template Blender usar para montar os vídeos. Múltiplos templates podem coexistir — a escolha é feita por configuração.

---

## Fluxo completo resumido

```
Você escreve um roteiro em texto
         ↓
O LLM melhora o texto e define as partes
         ↓
Cada parte vira um áudio narrado
         ↓
Os silêncios do áudio são removidos
         ↓
Cada áudio é montado num vídeo com legenda e trilha sonora
         ↓
Cada vídeo é agendado no TikTok em dias consecutivos
         ↓
O TikTok publica automaticamente no horário certo
```

Do roteiro à publicação, sem intervenção manual.
