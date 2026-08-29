# Produto — content_engine

Descrição do produto em linguagem natural, feature por feature. Este documento descreve **o que o sistema faz**, não como está implementado. É a referência para entender o comportamento esperado de ponta a ponta.

---

## O que é este produto

O content_engine é uma máquina de produção de conteúdo para TikTok. Você escreve um roteiro em texto simples, e o sistema cuida de todo o resto: melhora o texto, gera a narração em áudio, monta o vídeo e agenda a publicação. O objetivo é eliminar o trabalho repetitivo de produção e permitir que um único criador publique consistentemente sem depender de editores ou estúdio de gravação.

O sistema funciona completamente sozinho depois que você submete o roteiro. Você pode acompanhar o progresso em tempo real, mas não precisa intervir em nenhuma etapa.

---

## Feature 0 — Busca automática de roteiros na internet

Antes de tudo isso, existe a pergunta de onde vem o roteiro. O sistema consegue encontrá-los sozinho, sem ninguém escrever nada.

Periodicamente, ele varre comunidades do Reddit dedicadas a **contar histórias** e pega as mais votadas da semana. Cada história vira um roteiro candidato e entra no pipeline normal, exatamente como se você tivesse colado o texto à mão.

**As comunidades mudaram, e o motivo importa.** Antes o sistema lia desabafos, relatos de relacionamento e pedidos de conselho. Esses lugares são ótimos para quem precisa desabafar, mas ninguém escreve neles com intenção de entreter — não tem virada, não tem final. Agora ele lê comunidades onde as pessoas escrevem *para serem lidas*: uma em português onde cada post é um dilema pedindo julgamento ("sou babaca por…?"), e duas em inglês que são as maiores comunidades de história do Reddit. As histórias em inglês são traduzidas para português na etapa de refinamento, recontadas em vez de traduzidas ao pé da letra.

**Ele também vasculha o passado, não só a semana.** De tempos em tempos o sistema volta ao acervo de uma das comunidades e lê um pedaço das melhores histórias de todos os tempos — e a cada vez avança um pouco mais fundo, em vez de reler as mesmas quinze para sempre. Isso significa que a fila não depende de a semana ter sido boa: existem anos de história boa parada lá atrás.

**E ele não conta a mesma história duas vezes, mesmo repostada.** O sistema já sabia não repetir um post que já usou. O problema é que a mesma história aparece de novo, meses depois, postada por outra pessoa, com outro título — coisa comum nessas comunidades. Agora ele guarda uma "impressão digital" do texto e reconhece a história mesmo redigitada, com outra pontuação ou sem acentos. Se reconhecer, descarta e anota o motivo.

**Por que Reddit e não vídeos do YouTube:** a ideia de baixar vídeos populares e transcrevê-los foi considerada e descartada por três motivos. A transcrição de um vídeo é literalmente o roteiro de outra pessoa, o que é copiar e não se inspirar. Visualizações medem o canal e a thumbnail, não a qualidade do texto — seria otimizar pelo sinal errado. E o custo é muito maior: baixar e transcrever leva minutos por vídeo, enquanto um post do Reddit já chega pronto em texto. O YouTube ainda pode entrar depois, mas como **descobridor de temas** que estão performando, para o sistema escrever um roteiro original sobre o assunto.

**Como ele escolhe entre as histórias:**

Cada comunidade entrega suas histórias já ordenadas pelas mais votadas da semana — o próprio Reddit faz esse ranking, e é ele que o sistema usa como medida de qualidade.

Mas o sistema não pega simplesmente as melhores do topo geral, porque isso faria uma única comunidade dominar tudo. Em vez disso, ele alterna: pega a melhor disponível de cada comunidade, uma por vez, em rodízio. O resultado é que as histórias publicadas continuam sendo as mais bem ranqueadas, e ao mesmo tempo variam de origem e de tom entre um vídeo e outro.

O rodízio se mantém sozinho ao longo dos dias, porque o sistema lembra o que já usou e nunca repete.

**Ele lê o começo de cada história e julga se ela prende:**

O ranking do Reddit diz quantas pessoas votaram numa história, mas não diz se ela *se conta bem*. Um desabafo desorganizado pode ter mil upvotes e ainda assim não render vídeo, porque no TikTok o espectador decide em dois segundos se continua assistindo — e ele decide olhando o começo.

Então, antes de escolher, o sistema lê o título e a abertura de cada história coletada e responde duas perguntas.

A primeira: **existe um gancho ali?** Um gancho é uma frase que, sozinha, faz querer saber o resto. Ela tem gente concreta, de preferência uma relação, um conflito já acontecendo, a promessa de um desfecho, e uma curiosidade que fica no ar. Por exemplo: *"minha mãe foi intimidada por outras mães, então ela se vingou de forma doce"* — tem a mãe, tem a agressão, promete a vingança e não conta qual foi. Já *"desabafo"* ou *"preciso de conselhos"* nomeiam um sentimento e não prometem nada.

A terceira: **essa história dá vontade de comentar?** Aqui a pergunta não é sobre qualidade de escrita, é sobre revolta. O sistema procura uma pessoa claramente errada — o namorado que traiu e quer voltar, a sogra que sabota, a amiga que conta o segredo — e dá uma nota de 0 a 10 para o quanto a história provoca indignação em quem assiste. Tragédia sem culpado não conta: dá pena, e pena não faz ninguém comentar.

O topo dessa nota é reservado para a revolta que atinge o público principal do canal, **mulheres de 18 a 35 anos**. Uma injustiça que só funciona para outro público — uma briga de trânsito entre desconhecidos — ainda pontua, mas não chega ao topo.

A segunda: **a história se conta bem?** Isso rende uma nota de 0 a 10. Ganha ponto quem tem conflito claro, gente que age, cenas concretas e sinal de que vem uma virada. Perde ponto quem só reclama, quem apresenta gente demais de uma vez, quem faz pergunta ao fórum em vez de contar o que aconteceu, ou quem escreve uma abertura da qual não se entende nada.

**A nota máxima voltou a ser alcançável.** O topo da escala estava reservado para o excepcional, e na prática nenhuma história chegava lá: a nota ia de 2 a 8 e nada mais. Isso apagava justamente a diferença entre uma história boa e uma ótima — que é a diferença que decide qual vira vídeo primeiro. Agora a nota máxima é para a história que você contaria adiante depois de ler, não para uma raridade anual. O meio da escala não mudou: um post comum de fórum continua valendo nota média.

**O que o sistema faz com isso:**

Duas coisas, e nenhuma delas é jogar a história no lixo.

A primeira é **ordenar**. As vagas de cada ciclo passam a ir para as histórias que revoltam mais e abrem melhor, não para as que apareceram primeiro no feed. O rodízio entre comunidades continua valendo por cima disso: cada comunidade concorre com a sua melhor história, e nenhuma monopoliza.

**A revolta pesa mais que a escrita, mas não atropela.** A ordem é decidida por uma soma em que a nota de revolta conta o dobro. Não é uma ordem de prioridade: se fosse só revolta, uma história revoltante e mal contada passaria na frente de uma que é as duas coisas — e história que ninguém termina de assistir não rende o comentário pelo qual ela foi escolhida. Na prática, dois pontos de revolta valem mais que quatro pontos de escrita.

A segunda é **marcar**. Toda história avaliada recebe uma etiqueta: `strong` quando tem gancho e se conta bem, `no_hook` quando é boa história mas começa longe do assunto — o gancho existe, está enterrado mais adiante — e `weak_storytelling` quando a nota ficou abaixo do corte. A etiqueta viaja junto com o roteiro até a etapa de refinamento, então o modelo que escreve o texto final sabe se está polindo um gancho que já existe ou se vai ter que construir um. Quando existe uma frase que serve de gancho, ela vai junto, identificada.

**Nenhuma história é descartada por ser pouco revoltante.** Se um ciclo inteiro não trouxer nada indignante, a melhor história disponível vai ao ar do mesmo jeito — um dia sem vídeo é pior que um vídeo mais calmo. O que o sistema faz é contar quantas ficaram abaixo do corte e quantas tinham vilão, para você ver se a oferta secou antes de o canal sentir.

**Por que marcar e não descartar:** a etiqueta é informação, não veredito. Uma história com abertura fraca e nada melhor atrás dela ainda vai ao ar — recusá-la transformaria um sinal de qualidade em filtro rígido e poderia esvaziar a fila em semanas fracas. O que a nota faz é decidir a *ordem*, e a etiqueta serve para o refinamento trabalhar melhor e para você olhar depois quais notas correspondiam a vídeos que renderam. O ponto de corte entre fraco e forte é ajustável justamente por isso.

**Custo:** a avaliação é uma única consulta ao modelo por ciclo, com o começo de todas as histórias de uma vez, e não uma consulta por história. É o que torna viável avaliar tudo em vez de só o que já foi escolhido — sem isso, não haveria como ordenar. Se essa consulta falhar, o ciclo segue normalmente usando o ranking do Reddit, e as histórias ficam registradas como "não avaliadas", que é diferente de "avaliadas e ruins".

**O que é descartado automaticamente:**

Nem toda história serve. O sistema recusa textos curtos demais, que não têm história suficiente para sustentar um vídeo, e textos longos demais, que custariam mais narração e renderização do que uma vaga vale.

**Mas o limite de tamanho deixou de ser o primeiro critério.** Ele era: o texto longo era descartado assim que chegava, antes de qualquer pergunta sobre qualidade. O problema é que isso descartava justamente o melhor material. Numa medição sobre 45 histórias reais, 11 estavam acima do teto — e **nenhuma delas tirou nota baixa**, enquanto todas as histórias fracas estavam dentro do teto. Metade das notas mais altas estava do lado descartado. Faz sentido: as comunidades onde mora o gênero premiam quem escreve bem e longo, então tamanho e qualidade andam juntos, e cortar por tamanho primeiro era cortar por qualidade ao contrário.

Agora a história longa é coletada, lida e **avaliada normalmente**, recebe sua nota e sua etiqueta, e só então é recusada por tamanho. Ela não vira vídeo, mas fica registrada com a nota que tirou. A diferença prática é que o registro de rejeições passa a responder "o que estamos deixando passar", em vez de só dizer que algo foi descartado.

**E o teto subiu: de 6.000 para 30.000 caracteres.** A medição acima dizia que o limite antigo estava recusando o melhor material, então ele foi movido para onde existe uma razão real para ele estar. Esse ponto é o maior texto que ainda vira **um** vídeo só: acima disso, a etapa de refinamento passa a dividir a história em série com gancho de continuação. O valor antigo equivalia a cerca de cinco minutos e meio de narração — um quinto do que cabe num vídeo. O novo equivale a quase vinte e oito minutos, encostado no limite de trinta que o formato admite, com uma folga para o texto crescer enquanto é reescrito.

Na prática: as histórias longas e bem avaliadas que vinham sendo registradas e descartadas passam a virar vídeo. O teto continua existindo, mas agora ele marca uma fronteira de formato — onde um post deixa de caber num vídeo — em vez de um palpite sobre custo.

Depois disso, cada história que está prestes a ser publicada passa por uma leitura de segurança feita por um modelo de linguagem. A pergunta é uma só: publicar isso coloca a conta em risco de suspensão? Assuntos como automutilação, abuso sexual e violência gráfica são recusados. Não é moralismo, é sobrevivência do canal — o TikTok remove contas que publicam esse tipo de conteúdo, e uma única coleta ruim custaria o perfil inteiro.

**Por que um modelo e não uma lista de palavras proibidas:**

A primeira versão usava uma lista de termos vetados, e ela errava de um jeito instrutivo. Uma história sobre alguém que recebeu de volta um Pix enviado por engano foi descartada porque continha a frase "eram 3 mil que não *me mataria*, mas afundaria minhas contas". A lista viu a palavra e recusou uma história boa e inofensiva.

Segurança depende do contexto, não da presença de palavras. A mesma expressão pode ser figura de linguagem sobre dinheiro ou relato de ameaça real — só lendo a frase inteira dá para saber. Por isso a decisão passou a ser de um modelo, que lê a história e julga o sentido.

Histórias pesadas continuam passando: término, traição, briga de família, demissão, dívida, luto. Esse é justamente o material que funciona. O que é barrado é o que coloca a conta em risco.

Para não sair caro, essa leitura acontece só nas histórias que já estão na fila para virar vídeo — algumas por ciclo, não em tudo que foi coletado. E se o serviço de leitura estiver indisponível, o sistema **não publica sem checar nem descarta a história**: ele apenas espera e tenta de novo no ciclo seguinte.

**O que mais é guardado sobre cada história:**

Além do texto, o sistema registra quem escreveu (o usuário do Reddit) e, para as histórias que vão virar vídeo, **quantos comentários a postagem recebeu** e uma amostra das reações — as vinte primeiras, com autor e texto.

A quantidade de comentários é a medida de repercussão que dá para obter. Os feeds do Reddit não expõem número de curtidas — nem no post, nem nos comentários —, então quantas pessoas se sentiram compelidas a responder é o indicador mais próximo de "essa história mexeu com gente". Fica guardado para, mais adiante, dar para comparar o que repercutiu no Reddit com o que rendeu no TikTok.

As reações em si têm valor próprio: são o que o público achou da história, escrito por gente real. Servem de matéria-prima para os cards de comentário (Feature 8) e para entender por que uma história funcionou.

Esse enriquecimento custa caro em tempo — o Reddit só permite uma consulta por minuto —, então acontece **só nas histórias que já foram aprovadas e estão indo para a fila**, não em tudo que foi coletado. Se a consulta falhar, a história é publicada do mesmo jeito: o registro fica marcado como "não consultado", que é diferente de "não teve comentário nenhum".

**Nada é publicado duas vezes:**

O sistema guarda registro de toda história que já avaliou, inclusive as que rejeitou e o motivo. Uma história que reaparece no topo da semana seguinte não vira um segundo vídeo. E o registro das rejeições permite ajustar os critérios olhando dados reais, em vez de chutar.

**Ele respeita o ritmo da publicação:**

Antes de buscar mais material, o sistema verifica quantos vídeos já estão em produção. Se a fila está cheia, ele simplesmente não busca mais nada naquele ciclo. Produzir mais rápido do que se publica não adianta — só transformaria roteiro bom em vídeo travado esperando vaga.

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

Além de abrir o roteiro, esse gancho volta separado, como uma frase à parte. Antes ele era só a primeira linha de um texto corrido, e ninguém sabia dizer onde ele terminava; agora o sistema devolve a frase inteira, identificada, para poder ser usada sozinha. Se o modelo esquecer de mandá-la, o sistema a recupera pegando a primeira frase da parte 1 — o gancho nunca fica faltando.

**O texto narrado acaba junto com a história.** Antes, o modelo era instruído a fechar cada parte com uma chamada para ação — "comenta o que você faria", "segue para a parte 2" — e isso era narrado no vídeo. Virava um recado colado no fim de uma história que já tinha terminado: o espectador ouvia o desfecho e, em seguida, um pedido. Agora a última frase narrada é a última coisa que acontece na história, sem despedida, sem moral e sem pedido de like.

A chamada para ação não deixou de existir — ela continua sendo escrita para cada parte, mas só como **legenda do post**, que é onde o TikTok mostra esse tipo de texto e onde ele não interrompe a narração.

**Divisão em partes:**

O padrão é **não dividir**: a história vai inteira num vídeo só, do começo ao fim, por mais longa que seja. Dividir passou a ser exceção, e só acontece se a narração passar de **30 minutos**. Aí sim o sistema decide onde cortar, sempre num momento de cliffhanger, e a parte 2 começa com um breve resumo do que aconteceu antes.

O modelo também é instruído a não encurtar a história para caber em menos tempo. Antes, "caber" era a regra principal e o texto era espremido; agora o que manda é contar a história completa.

**Classificação do conteúdo:**

**Quem conta a história também é identificado.** Além de refinar o texto, o modelo diz se quem narra é homem ou mulher — deduzindo do próprio roteiro: a concordância das palavras ("fiquei cansada", "eu estava sozinho"), como as pessoas chamam o narrador, o papel que ele diz ter ("meu marido", "sou pai de dois"). É a partir daí que a voz da narração é escolhida (ver Feature 3).

Isso é diferente do público-alvo, que também é estimado logo abaixo: um é quem conta, o outro é para quem se conta, e os dois divergem o tempo todo — história de homem com público majoritariamente feminino é o caso mais comum do acervo. Na dúvida, o sistema prefere dizer que não sabe a chutar: errar o gênero de quem narra é a primeira coisa que o espectador percebe, e "não sei" apenas mantém a voz padrão.

Junto com o roteiro refinado, o sistema produz uma classificação automática do conteúdo. Ela inclui o tipo de conteúdo (drama, comédia, motivacional, educativo, entretenimento, suspense), o tom (suspense, engraçado, emocional, educativo, inspiracional, chocante), o público-alvo estimado (faixa etária, gênero, interesses) e sugestões de hashtags relevantes. Essa classificação é usada em etapas posteriores para escolher as hashtags certas e montar a legenda do post.

---

## Feature 3 — Geração do áudio

Com o roteiro pronto, o sistema gera a narração em áudio. O texto é transformado em fala por um motor de síntese de voz, e o resultado é um arquivo de áudio MP3.

**A voz combina com quem está contando a história.** As histórias são narradas em primeira pessoa, então o narrador tem um gênero — e agora a voz concorda com ele: história de homem sai em voz masculina, história de mulher em voz feminina. Antes, todas saíam na mesma voz feminina, inclusive as narradas por homens; é o tipo de coisa que o espectador percebe na primeira frase e que nenhum acerto de ritmo ou de volume compensa.

Quando o roteiro não deixa claro quem narra, o vídeo usa a voz padrão de sempre — o sistema não chuta. As três vozes (a masculina, a feminina e a padrão) são configuráveis, então trocar o timbre do canal é uma questão de configuração, não de código. Para roteiros divididos em partes, cada parte recebe seu próprio arquivo de áudio — todas na mesma voz, porque é a mesma pessoa contando.

**A frase gancho também é narrada, num arquivo só dela.** A mesma voz, o mesmo ritmo, o mesmo tratamento de áudio das partes — só que dizendo apenas a frase de abertura. O arquivo fica guardado junto com os outros do vídeo e pode ser usado para abrir a peça, testar ganchos diferentes ou montar uma prévia, sem depender de recortar o áudio da parte 1. O vídeo em si continua sendo montado como antes: a narração completa já contém o gancho na primeira frase, então nada é dito duas vezes.

Se a narração do gancho falhar, o vídeo é produzido do mesmo jeito. Esse arquivo é um extra — perdê-lo não pode custar a peça inteira, e a falta dele fica registrada para quem for olhar depois.

O sistema suporta três motores de voz. O primeiro é gratuito e funciona sem nenhuma configuração extra, mas entrega o áudio numa qualidade fixa e baixa — é a razão pela qual a narração soa abafada, como se viesse de um rádio. O segundo é o Azure, que usa exatamente as mesmas vozes do gratuito, só que numa qualidade muito superior. O terceiro é o ElevenLabs, ainda não implementado, para quando fizer sentido pagar por vozes mais expressivas. A escolha entre eles é feita por configuração, sem alterar nada no fluxo de produção.

**O motor em uso é o gratuito, por decisão.** O Azure está pronto e é melhor em áudio, mas exige uma conta e uma chave para manter — e a diferença, ainda que audível, não impede nada de ser publicado. Trocar continua sendo uma questão de configuração: preencher a chave e apontar para o Azure. Se a chave faltar, o sistema se recusa a subir em vez de descobrir o problema no meio de uma produção, então a troca falha de forma limpa e visível.

Quando essa troca acontecer, a voz e o ritmo não mudam — é a mesma locutora, gravada com muito mais definição. O que muda é a nitidez: os agudos da fala, que simplesmente não existem no áudio de hoje, passam a estar lá.

A narração sai acelerada em relação ao ritmo natural da voz. O padrão é **30% mais rápido** — o ritmo apressado que o formato do TikTok pede, sem que a fala fique difícil de acompanhar. A voz continua com o tom normal: ela fala mais rápido, não fica mais aguda, porque a aceleração é feita pelo próprio motor de voz e não por acelerar o arquivo depois de pronto.

**A velocidade faz parte do template do vídeo.** Cada template — que já define o visual, a fonte da legenda e o ritmo da edição — define também quão rápido a voz fala. Assim um template de drama pode ter narração mais pausada e um de curiosidades rápidas pode ser mais acelerado, sem que seja preciso mexer em configuração de servidor ou reiniciar nada: basta editar o template. Um template que não diz nada sobre velocidade simplesmente usa o padrão do sistema, então templates antigos continuam funcionando.

A velocidade aceita tanto acelerar quanto desacelerar, e também voltar ao ritmo original da voz, se um tipo de conteúdo pedir uma narração mais calma. Como o vídeo fica mais curto quando a fala é mais rápida, acelerar também ajuda roteiros na fronteira dos 60 segundos a caberem em um único vídeo em vez de serem divididos em partes.

Se o template não puder ser lido por algum motivo, o sistema não interrompe a produção do vídeo — ele usa a velocidade padrão e registra o ocorrido. Velocidade de narração é uma escolha estética, e não vale perder um vídeo inteiro por causa dela.

---

## Feature 4 — Remoção de silêncios do áudio

Antes de usar o áudio no vídeo, o sistema remove automaticamente os silêncios desnecessários. Isso inclui o silêncio antes da fala começar, o silêncio depois da fala terminar, e pausas longas no meio do áudio que deixam o vídeo pesado.

Pausas naturais entre palavras e frases — as que existem para dar ritmo à fala — são preservadas. O resultado é um áudio mais compacto e dinâmico, sem cortes bruscos.

**A narração continuava soando vazia, e a causa era o próprio corte.** O ajuste que definia quanto silêncio remover nunca funcionou como se imaginava: em vez de escolher *quais* pausas eliminar, ele definia *quanto silêncio sobrava* em cada uma. Com o valor antigo, uma pausa de um segundo e meio e uma de três segundos terminavam as duas com meio segundo de nada — todo intervalo longo virava a mesma lacuna. O valor foi reduzido para 200 milissegundos, e agora pausas curtas passam intactas enquanto as longas encolhem de verdade. Quem já tinha o ajuste antigo configurado não precisa mudar nada: o nome antigo continua sendo aceito.

Esse comportamento é ativo por padrão, mas pode ser desligado. Os limiares (quanto silêncio é considerado silêncio, qual o tamanho máximo de uma pausa) são configuráveis.

**Ferramenta avulsa para calibrar os limiares.** Existe também um comando que processa um arquivo de áudio na máquina, fora do pipeline. Ele serve para ouvir o resultado de um ajuste antes de aplicá-lo à produção: dá para rodar o mesmo áudio com limiares diferentes e comparar, em vez de descobrir que o corte ficou agressivo demais só depois de o vídeo estar pronto. Ele aplica exatamente o mesmo tratamento que a produção aplica — inclusive o ajuste de volume — para que o que se ouve no teste seja o que sai no vídeo.

O comando informa quanto tempo o áudio tinha, quanto ficou e qual a porcentagem removida. Tem um modo de simulação, que faz as contas e reporta o resultado sem gravar arquivo nenhum, e nunca sobrescreve um arquivo existente sem que isso seja pedido explicitamente. Aceita vários arquivos de uma vez, e um que falhe não interrompe os demais.

---

## Feature 4.1 — Volume padronizado da narração

O corte de silêncios agora vem acompanhado de um ajuste de volume. Todas as narrações saem no mesmo nível de audição, independentemente da voz escolhida, do motor usado ou do conteúdo do roteiro. Antes, cada áudio tinha o volume que o motor de voz decidisse entregar — o que fazia uma parte 2 soar mais baixa que a parte 1 do mesmo vídeo, e a narração ora sumir sob a trilha sonora, ora estourar acima dela.

O nível escolhido é o mesmo que as plataformas de vídeo usam como referência, então o TikTok não precisa mexer no volume do vídeo depois de publicado — o que evita que ele abaixe a narração inteira só porque um trecho ficou alto demais.

Junto com isso, o sistema descarta os graves muito baixos que a voz não usa e que só ocupam espaço no áudio. O resultado é uma narração mais presente, sem que ela fique estridente.

O ajuste é ativo por padrão e pode ser desligado. O nível-alvo é configurável para quem quiser uma narração mais discreta ou mais agressiva.

Um detalhe invisível, mas que importa: o corte de silêncio, o ajuste de volume e a gravação do arquivo final acontecem todos de uma vez. Cada vez que um áudio é regravado ele perde um pouco de qualidade, e antes o sistema fazia isso mais vezes do que precisava — inclusive rebaixando a qualidade do arquivo sem que ninguém tivesse pedido. Agora, se não há nada a ajustar, o áudio é entregue exatamente como o motor de voz o produziu.

Na mesma linha, o arquivo final guarda exatamente a qualidade que o motor de voz entregou — nem menos, nem mais. Pedir "mais qualidade" do que existe na gravação original não melhora nada: só ocupa espaço. Quem determina a qualidade da narração é a escolha do motor de voz, e nenhum ajuste posterior substitui isso.

---

## Feature 5 — Montagem e renderização do vídeo

Com o áudio pronto, o sistema monta o vídeo no Blender. A montagem segue um template pré-configurado que define o visual do vídeo: o vídeo de fundo, a trilha sonora, a posição das legendas e o ritmo geral da edição.

**O que compõe o vídeo:**

O vídeo de fundo toca durante todo o tempo. A trilha sonora começa junto, com volume baixo, e some gradualmente ao final.

⚠️ **Até agora a trilha nunca tocou.** O arquivo de música configurado era um placeholder de 35 segundos de silêncio puro — silêncio digital mesmo, medido amostra por amostra. Todos os vídeos publicados saíram só com narração e fundo. Isso foi corrigido: existe uma trilha de verdade no lugar, e ela sai 13 unidades de volume abaixo da narração, que é a distância em que uma cama sonora se ouve sem competir com a voz. A narração em áudio (gerada na etapa anterior) entra no momento certo, de acordo com o timing definido no template. As legendas aparecem e somem de forma animada, sincronizadas com o texto narrado.

**Legendas:**

As legendas são geradas automaticamente a partir da narração — não do roteiro. O sistema escuta o áudio já pronto e descobre o momento exato em que cada palavra é dita, então a legenda acompanha de fato o que foi falado, e não uma estimativa de velocidade de fala.

Na tela aparece uma palavra por vez, no ritmo da narração. Cada palavra fica visível até a próxima começar, para que não haja buracos entre elas. Quando a narração faz uma pausa mais longa, a legenda sai da tela em vez de deixar a última palavra pendurada — e volta suavemente quando a fala recomeça.

**Animação de entrada:** cada palavra surge um pouco abaixo da posição final e sobe rapidamente até ela, desacelerando ao chegar. É um movimento curto — cerca de um oitavo de segundo — que dá um "pulo" a cada palavra e faz a legenda acompanhar o ritmo da fala em vez de só trocar o texto no lugar. Esse movimento vale para todas as palavras, inclusive as que vêm coladas umas nas outras.

O aparecer e desaparecer suave (a transparência) fica reservado só para os momentos de pausa — entre palavras seguidas a troca é direta, porque desbotar a cada palavra lê como piscada.

A altura da subida, a duração dela, o quanto a legenda pode "esperar" numa pausa antes de sair da tela e a duração do aparecer/desaparecer são todos ajustáveis no template.

**Aparência do texto:** a legenda usa Futura Bold, em branco com contorno preto. O contorno existe porque o vídeo de fundo muda o tempo todo — sem ele, uma palavra branca passando por cima de uma cena clara simplesmente some. Com o contorno a legenda continua legível seja qual for o fundo, sem precisar de tarja ou caixa atrás do texto.

O arquivo da fonte vive junto com o projeto, na pasta de fontes do serviço de montagem, e por isso vai junto para o ambiente onde o vídeo é renderizado. Se por algum motivo ele não estiver lá, a legenda é renderizada numa fonte alternativa em vez de o vídeo falhar — o vídeo sai, só com o visual diferente do pretendido.

A fonte, o tamanho, a cor do texto, a cor do contorno e a espessura do contorno são todos ajustáveis no template, e o contorno pode ser desligado.

**Velocidade do vídeo:**

O vídeo final agora respeita a taxa de quadros definida no template. Antes, um ajuste herdado do arquivo de template fazia a montagem rodar dez vezes mais rápido do que o pretendido — o que desalinhava a narração, a trilha e as legendas do vídeo de fundo. O tempo de cada elemento agora corresponde ao tempo real.

**Templates:**

O visual do vídeo é controlado por templates reutilizáveis. Um template define onde fica cada elemento na tela, qual é a duração total do vídeo, e o estilo visual (fonte das legendas, posição do texto, etc.). Criar um template bem calibrado é o trabalho manual que o sistema multiplica — cada roteiro submetido usa o mesmo template e produz um vídeo com aparência consistente.

**Processamento:**

A renderização é um processo pesado e acontece em segundo plano. O sistema não bloqueia durante esse tempo — é possível submeter novos roteiros enquanto um vídeo está sendo renderizado. O progresso pode ser acompanhado em tempo real.

**Um vídeo é montado por vez.** Renderizar é a etapa mais pesada de todas em memória, e a máquina que roda o sistema comporta um render de cada vez. Antes não havia limite nenhum: se cinco vídeos fossem aceitos juntos, cinco montagens começavam juntas e a máquina simplesmente ficava sem memória — e quando isso acontece, quem o sistema operacional derruba é arbitrário. Podia ser o banco de dados, levando junto o estado de todas as histórias em produção, e não só o render.

Agora os vídeos além do primeiro esperam a vez, na fila, exatamente como esperavam antes de começar. Nada é recusado e nada se perde: só deixa de acontecer ao mesmo tempo. Para três vídeos por dia isso não custa throughput nenhum — a fila continua cheia. Quantos podem rodar juntos é configuração, para o dia em que a máquina for maior.

---

## Feature 6 — Agendamento no TikTok

Quando o vídeo está pronto, o sistema agenda a publicação automaticamente no TikTok via Buffer. Não é necessário nenhuma ação manual — o vídeo vai para a fila e é publicado no horário certo.

**Ritmo de publicação:**

O sistema publica três vídeos por dia, dentro da janela em que o público brasileiro está acordado: **11h, 15h e 19h no horário de Brasília**. Ele nunca agenda dois posts no mesmo horário — se um slot já está ocupado, avança para o próximo disponível.

**Séries:**

Quando a história foi dividida — o que agora só acontece acima de 30 minutos —, as partes saem **coladas**: a parte 2 é publicada 30 minutos depois da parte 1, a parte 3 trinta minutos depois da parte 2, e assim por diante. Só a parte 1 entra na fila normal de horários preferidos; as seguintes penduram no horário dela.

Antes cada parte ia para um dia diferente, e quem via o começo tinha de esperar até o dia seguinte para o resto. Uma história partida é uma história continuada, não posts independentes — o intervalo curto é o que preserva isso.

Se o sistema reiniciar entre uma parte e outra, e a hora da continuação já tiver passado, ela é republicada 30 minutos a partir daquele momento em vez de ficar presa num horário vencido.

**Fila cheia:**

O Buffer free suporta até 10 posts agendados por vez. Se a fila estiver cheia, o sistema recusa o agendamento e sinaliza o problema. O vídeo não é perdido — o pipeline fica em estado de falha e pode ser retomado manualmente.

**Caption e hashtags:**

A legenda do post é composta automaticamente com base na classificação do conteúdo. Ela inclui o CTA da respectiva parte e as hashtags selecionadas. A seleção de hashtags segue uma ordem de prioridade: primeiro as hashtags obrigatórias (sempre presentes, como `#tiktokbrasil` e `#fyp`), depois as sugeridas pelo LLM com base no conteúdo, e por último um pool de hashtags de fallback para completar até o máximo configurado. Para séries, a legenda inclui o indicador de parte ("Parte 1/2").

O indicador de parte **nunca chegou a aparecer**: ele era lido de um campo que o refinamento não preenchia, então toda legenda saía sem ele, inclusive nas séries. Agora a contagem vem de quem de fato criou as partes, e o indicador aparece quando — e só quando — há mais de uma.

---

## Feature 11 — O mesmo vídeo também vai para o YouTube

O vídeo que é publicado no TikTok passa a ser publicado também no YouTube, no **mesmo horário**, sem trabalho a mais e sem render a mais. É o mesmo arquivo indo para dois lugares, pelo mesmo Buffer — o que muda é o canal.

**Por que pelo Buffer e não direto pelo YouTube:** o YouTube tem uma API de upload própria, mas todo vídeo enviado por ela, vindo de um aplicativo que não passou pela auditoria do Google, fica **travado como privado** — e não há recurso: o vídeo precisa ser reenviado por outro caminho. Passar pela auditoria é um processo à parte, com prazo e critérios do Google. O Buffer já é um cliente auditado e já estava no sistema, então o vídeo sai público desde o primeiro dia.

**O título é escrito para o YouTube, não copiado do TikTok:**

No TikTok não existe título — existe só a legenda, que aparece por baixo do vídeo enquanto ele já está tocando. No YouTube o título é a única coisa que a pessoa lê **antes** de o vídeo abrir, numa lista ao lado de dezenas de outros. São textos com trabalhos diferentes, e reaproveitar um como o outro desperdiça o momento que mais decide o clique.

Então o refinamento passou a escrever um título próprio: até 100 caracteres, dizendo o conflito da história sem entregar o desfecho, sem caixa alta, sem emoji e sem prometer o que o vídeo não cumpre. Quando a história foi dividida, o "(Parte 2/3)" é acrescentado automaticamente — e se o título ficar comprido demais para caber com o rótulo, **quem encolhe é o título**, porque numa série saber qual parte é aquela é o que não pode faltar.

**As hashtags são outras:** `#tiktokbrasil` e `#fyp` não significam nada no YouTube, onde hashtag é busca e não distribuição. O YouTube tem sua própria lista de obrigatórias. `#shorts` ficou de fora de propósito: as histórias podem passar de três minutos, que é o limite de um Short, e marcar como Short um vídeo que não é engana quem clica sem trazer alcance nenhum.

**O vídeo é declarado como tendo narração gerada por IA**, que é o que ele é.

**Se o YouTube falhar, o vídeo não é perdido.** O TikTok é o destino principal e sai primeiro; se o agendamento no YouTube der errado depois disso, a produção continua normalmente e você recebe um aviso no WhatsApp dizendo qual parte não subiu e por quê. O contrário — deixar a falha do YouTube derrubar tudo — faria a tentativa seguinte republicar a mesma história no TikTok, que apareceria duas vezes lá.

**Enquanto o canal não estiver conectado**, nada muda: o sistema publica só no TikTok e não reclama. Destino desligado é uma escolha de configuração, não um problema — e um aviso que dispara em todo vídeo é um aviso que ninguém lê.

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

Além dos vídeos, o sistema consegue gerar imagens no estilo "card de comentário do TikTok" — fundo branco com bordas arredondadas, um cabeçalho no topo com a foto de perfil, o nome e os selos do autor, e o texto do comentário logo abaixo, em letra preta grossa (Arial Bold). Esse tipo de imagem é muito usado como overlay em vídeos de reação ou para dar contexto a uma história.

A imagem sai sempre com a mesma largura do vídeo do TikTok, e só a altura muda conforme o tamanho do texto. Dentro dessa moldura, o card em si é mais estreito e fica encostado mais à esquerda: o lado direito da tela do TikTok é ocupado pelos botões de curtir, comentar e compartilhar, então o card precisa dar espaço para eles. O resto da moldura é transparente, o que permite aplicar a imagem sobre o vídeo inteiro sem ninguém precisar calcular posição.

O visual do card é controlado por um arquivo de template que define: a largura e a posição do card, a cor e o arredondamento do fundo, o tamanho e a imagem do cabeçalho, a fonte, o tamanho e o espaçamento do texto, e o espaçamento interno. Todos esses parâmetros podem ser ajustados sem alterar o código.

O texto do comentário é quebrado automaticamente em múltiplas linhas para caber na largura definida. A altura do card cresce de acordo com o texto — não há limite de caracteres imposto pelo sistema.

Se alguém configurar um card largo demais, ou grudado demais numa das bordas, o sistema recusa a configuração com uma mensagem dizendo de que lado e por quantos pixels ele passou — em vez de gerar uma imagem com a sombra cortada pela metade.

O card também tem uma sombra projetada, que dá a sensação de que ele está flutuando sobre o vídeo em vez de estar colado nele. Dá para escolher a cor e a opacidade da sombra, o quanto ela é difusa, o quanto ela se espalha para além do card e para que lado ela cai — o padrão é uma sombra suave caindo para baixo, como se a luz viesse de cima. A sombra é opcional: templates que não a configuram continuam produzindo a mesma imagem de antes.

A altura da imagem já reserva o espaço que a sombra precisa em cima e embaixo, então ela nunca aparece cortada. A sombra também nunca escurece o próprio card por baixo — ela aparece só ao redor dele.

O resultado é uma imagem PNG salva no storage, pronta para ser usada como asset em um vídeo.

---

## Feature 9 — Configuração por tipo de conteúdo

O sistema é configurável em vários aspectos sem precisar alterar o código:

**Voz:** qual voz e qual motor de TTS usar. Padrão: voz feminina jovem brasileira, motor gratuito (o Azure está pronto e entrega áudio bem melhor, ao custo de uma conta e uma chave para manter).

**Velocidade da narração:** quanto mais rápido (ou mais devagar) a voz fala em relação ao ritmo natural dela. Definida no template do vídeo, junto com o resto do visual. Padrão: 30% mais rápido.

**Volume da narração:** o nível de audição padronizado de todas as narrações, e a opção de desligar essa padronização.

**Remoção de silêncio:** se deve remover silêncios, qual é o limiar de volume para considerar algo silêncio, e qual o tamanho máximo que uma pausa pode ter depois do corte.

**Hashtags obrigatórias:** quais hashtags sempre aparecem em todos os posts. Padrão: `#tiktokbrasil` e `#fyp`.

**Pool de hashtags:** uma lista de hashtags de fallback, editável sem reiniciar o sistema. Basta editar o arquivo e reiniciar o container.

**Horários de publicação:** em quais horários do dia os posts são agendados. Hoje: 11h, 15h e 19h de Brasília. O arquivo de configuração guarda esses horários em UTC (14h, 18h e 22h), então mudar um horário exige converter — três horas a mais.

**Limite de fila:** quantos posts podem estar agendados simultaneamente antes de o sistema recusar novos agendamentos. Padrão: 10 (limite do Buffer free).

**Modelo de LLM:** qual modelo de linguagem usar para refinar os roteiros. A troca de modelo não afeta o fluxo — só a qualidade e o custo do refinamento.

**Template de vídeo:** qual template Blender usar para montar os vídeos. Múltiplos templates podem coexistir — a escolha é feita por configuração.

---

## Feature 10 — Abertura do vídeo: o card com a frase gancho

O vídeo agora **abre com um card de comentário** — aquele card branco de fundo arredondado, com foto de perfil e nome no topo — trazendo a frase gancho da história escrita nele. Enquanto o card está na tela, uma narração lê essa frase em voz alta. Só quando ela termina é que a história começa, com as legendas palavra por palavra de sempre.

Antes, o vídeo começava com três segundos de fundo rodando sozinho: o card era gerado, a frase gancho era narrada em arquivo separado, e nada disso chegava ao vídeo. As duas peças existiam e não se encontravam.

**Como fica na tela.** O card **já está na tela no primeiro frame** — não aparece aos poucos — e some suavemente quando a abertura termina. Fica centralizado, com a mesma folga do lado direito que as demais artes deixam para os botões de curtir e comentar do TikTok. A posição, a duração do sumir e a pausa entre o gancho e o início da narração são ajustáveis no template.

**Por que o card não aparece mais aos poucos.** Todo vídeo abre com o mesmo card, no mesmo lugar, e antes todos abriam também com a mesma transição de meio segundo por cima dele — os primeiros frames de qualquer vídeo do canal eram, quadro a quadro, quase a mesma imagem. Isso é o tipo de coincidência que a plataforma lê como conteúdo repostado. Com o card já posicionado desde o começo, a abertura de cada vídeo passa a ser o card daquela história e mais nada. De quebra, a frase gancho fica legível alguns quadros antes.

**A foto de perfil e o texto do card ficaram maiores** — cerca de 40% em cima do tamanho original, tanto no cabeçalho (foto, nome e selos) quanto na letra da frase. No tamanho anterior o card era lido como uma legendinha no meio da tela; agora ele tem o peso de um card de comentário de verdade, que é o que faz alguém parar de rolar o feed para ler. O espaçamento entre o cabeçalho e o texto acompanhou o aumento. Nada disso é código: são números no arquivo de template do card, ajustáveis a qualquer momento.

Uma observação para quando quiser aumentar mais: a imagem do cabeçalho já está sendo exibida um pouco acima do tamanho em que foi salva, então a partir daqui vale gerar uma versão maior desse arquivo antes de crescer de novo — senão ela começa a perder nitidez.

**Quanto tempo dura.** A abertura dura o tempo da frase falada, mais uma pequena pausa — não um tempo fixo. Se o gancho for curto, a abertura mantém os três segundos mínimos do template; se for longo, ela estica para caber a frase inteira, e a narração espera. Nunca há palavra cortada nem card sumindo antes da hora.

**Vale para todas as partes da série.** Quando uma história vira duas ou três partes, todas abrem com o mesmo card e a mesma frase. É isso que faz as partes serem reconhecidas como do mesmo vídeo ao aparecerem no feed em dias diferentes.

**A frase é dita uma vez só.** Como o gancho é literalmente a primeira frase do roteiro, tocar a narração separada dele na frente da parte 1 faria o vídeo repetir a mesma frase logo em seguida — justamente nos segundos em que a pessoa decide se continua assistindo. Então na parte 1 a narração é a de sempre, inteira, e é ela que diz a frase; o card entra por cima desde o primeiro segundo e sai quando a frase termina. A narração separada nem toca ali — ela serve só para o sistema saber quanto tempo a frase leva.

Enquanto o card está na tela a legenda não aparece: o card já mostra aquele texto, e os dois ficam na mesma altura do vídeo. A legenda entra na palavra seguinte, junto com o resto da história.

Nas outras partes da série a frase não está na narração, então lá a narração separada toca normalmente como abertura — todas as partes começam do mesmo jeito.

Se a frase de abertura não corresponder ao começo do roteiro (o texto foi reescrito, por exemplo), a parte volta a abrir com a narração separada. De um jeito ou de outro a frase é dita uma vez.

**Se faltar alguma peça, o vídeo sai mesmo assim.** Se a narração do gancho falhar, ou se o card não puder ser gerado, o vídeo é montado sem a abertura, exatamente como era antes — nenhuma das duas peças vale perder o vídeo inteiro, que é o que o sistema existe para entregar.

---

## Quando o vídeo de fundo está quebrado

Antes, um arquivo de fundo corrompido ou vazio não impedia nada: o sistema montava o vídeo normalmente, marcava o trabalho como concluído e entregava um MP4 com a tela **preta** do começo ao fim. A narração e a legenda estavam lá, mas o vídeo era inutilizável — e nada avisava. Só olhando o resultado dava para perceber.

Agora o sistema confere o arquivo de fundo antes de montar. Se ele não tiver imagem de verdade, o trabalho falha na hora, dizendo qual arquivo está com problema. Você descobre em segundos, em vez de esperar a montagem inteira para receber um vídeo preto que parece bom pelo tamanho e pela duração.

## Duração do vídeo

O vídeo agora termina junto com a narração.

Antes, a duração era ditada pelo mais longo entre todos os arquivos — inclusive o vídeo de fundo e a música, que são apenas pano de fundo. Na prática, um fundo de 90 segundos sob uma narração de 68 gerava 22 segundos de silêncio no fim, com a legenda já fora da tela. Quem define onde a história acaba é a narração; fundo e trilha são decoração e não esticam mais o vídeo.

Se o fundo for **mais curto** que a narração, o sistema repete o próprio fundo até cobrir a história inteira. Antes, o trecho que sobrava saía **preto**, com a legenda aparecendo sobre o nada e sem nenhum aviso — um clipe de 45 segundos sob uma narração de 71 gerava 26 segundos de tela preta. Isso deixou de ser um erro do arquivo e passou a ser normal, porque o fundo agora vem de uma biblioteca de clipes curtos (ver abaixo).

## O fim do vídeo é a última palavra da história

O vídeo não tem finalização. Nada é acrescentado depois que a narração termina: a última palavra da história é o último frame, e a única coisa que marca o encerramento é a **música de fundo sumindo por baixo dessa última frase** — um fade de 1,5 segundo.

**Com meio segundo de respiro no fim.** O vídeo acabava colado na narração, tão em cima que a última palavra saía cortada — o vídeo parecia terminar no meio de uma frase. Agora ficam **0,5 segundo** depois do último som: o suficiente para a palavra terminar de ser dita e para a música completar o fade, e curto o bastante para não virar tempo morto. Quem manda continua sendo a narração — esse meio segundo é o respiro dela, não uma finalização. A duração desse respiro é configuração do template, e zero volta ao comportamento anterior.

Antes o fade era escrito como um instante fixo na linha do tempo: "comece a sumir no segundo 28", número anotado quando os vídeos eram rascunhos de 30 segundos. Como a duração passou a ser a da narração, esse instante deixou de significar "no finalzinho". Medido num vídeo de 23 segundos: a trilha começava a cair **desde o primeiro segundo** e chegava ao fim praticamente inaudível — de -26 dBFS no começo para -73 dBFS no fim, ou seja, mais da metade do vídeo com a música morrendo. Era esse o motivo de a trilha "não estar lá" mesmo estando no arquivo.

Agora o fade é contado de trás para frente, a partir do fim. Medido no mesmo vídeo: a trilha fica no mesmo volume do começo até 21,9s, e só então cai até o silêncio no último frame, em 23,4s. A duração do fade é configuração do template (`fade_out_seconds`), e zero desliga.

## Legenda no centro e maior

A legenda agora aparece **no meio da tela**, não mais no rodapé, e com a fonte bem maior.

O tamanho anterior era o padrão interno do Blender — pequeno demais para vídeo vertical, e menor do que a configuração do projeto dizia usar. O arquivo de template que estava em uso não trazia a definição de tamanho, então a configuração escrita no repositório nunca chegava ao vídeo. Corrigido: a palavra agora sai mais de três vezes maior do que saía antes.

**Palavras longas se ajustam sozinhas.** Uma palavra comprida como "procedimento," não caberia na largura da tela no tamanho novo — e antes seria simplesmente cortada nas bordas, sem aviso. Agora o tamanho escolhido funciona como um teto: a maioria esmagadora das palavras sai nele, e só as poucas que não cabem encolhem o suficiente para caber inteiras. Numa narração real de 178 palavras, apenas 13 precisaram de ajuste.

**Dá para mudar sem mexer em código.** Posição vertical e tamanho são configuração do template: `y_position` (0.5 = centro exato, 0.05 = rodapé como antes) e `font_size`.

Valores em uso hoje: **tamanho 100**, posição **0.474** — ou seja, 50 pixels abaixo do centro da tela.

## Fundo diferente a cada vídeo

Antes, todo vídeo do canal usava **o mesmo arquivo de fundo, começando no mesmo segundo**. Duas partes seguidas da mesma história saíam com exatamente a mesma imagem por trás, mudando só as palavras.

Agora o fundo vem de uma biblioteca: um vídeo longo é cortado em dezenas de clipes curtos, e cada parte sorteia o seu. Partes da mesma história — que vão ao ar uma atrás da outra, onde a repetição seria mais visível — praticamente nunca caem no mesmo clipe.

A escolha é **estável**: se a mesma parte precisar ser montada de novo, ela volta com o mesmo fundo, em vez de virar um vídeo diferente do que já foi revisado.

Se a biblioteca estiver vazia, o sistema continua usando o arquivo único de antes — nada quebra por falta de clipes.

## Fila cheia deixou de jogar vídeo fora

O serviço de agendamento aceita no máximo 10 posts na fila. Quando ela enchia, a história inteira era marcada como **falha** — depois de já ter pago o refinamento do texto, a narração, a transcrição e a montagem do vídeo. O trabalho ia todo para o lixo por causa de um minuto de fila cheia.

Agora a história fica **esperando vaga**, com os vídeos prontos guardados. De tempos em tempos o sistema tenta de novo sozinho, e assim que abre espaço na fila ela é agendada.

Isso também resolveu um desequilíbrio silencioso: a busca de roteiros trazia até dois por hora, enquanto a publicação dá conta de três por dia. Como uma história esperando vaga conta como trabalho em andamento, a busca agora se segura sozinha enquanto a fila está cheia, em vez de produzir vídeos que morreriam na última etapa.

**Faltava um caso, e ele apareceu na prática.** O sistema conferia a fila antes de tentar publicar, mas nem sempre a conta dele e a do serviço de agendamento batem — e às vezes a recusa por fila cheia só vem na hora de criar o post. Nesse caminho a história voltava a ser marcada como falha, do jeito antigo, e dessa vez sem volta: nem a tentativa automática a resgatava. Em 15 de agosto de 2026 duas histórias terminaram assim, com o vídeo pronto e guardado, sem nunca serem publicadas.

Agora tanto faz onde a fila cheia é descoberta: nos dois casos a história espera vaga e é retomada sozinha. Uma recusa que **não** seja fila cheia continua sendo tratada como erro de verdade — o que espera para sempre também nunca é publicado, e um problema real precisa aparecer em vez de virar espera silenciosa.

## O serviço de agendamento tem uma cota diária

O plano do serviço de agendamento permite **250 pedidos por dia**. Passou disso, ele recusa tudo até virar a janela — o que pode levar horas. Era isso que estava acontecendo em 15 de agosto de 2026, e o sistema tratava a recusa como defeito: a história era marcada como falha e o vídeo pronto ficava para trás.

Agora ela **espera**, como já esperava quando a fila estava cheia, e o aviso no WhatsApp diz qual dos dois é o caso e em quanto tempo libera ("8h33"). São problemas diferentes: fila cheia se resolve quando um vídeo publica, cota se resolve com o tempo passando — e saber qual é o seu evita procurar no lugar errado.

Há um efeito colateral que vale conhecer: **esperar também consome cota**. Cada história parada pergunta de tempos em tempos se já pode publicar, e cada pergunta conta. Perguntando de 15 em 15 minutos, uma história esperando o dia inteiro consumia quase a cota do dia sozinha — o sistema gastava, esperando, o que precisava para deixar de esperar.

A pergunta passou a ser de hora em hora, que é o ritmo de quem espera: a fila abre três vezes por dia, então nada se atrasa mais que uma hora e o gasto cai para um quarto. Junto com isso, uma configuração que faltava (`BUFFER_ORG_ID`) tirou uma consulta extra de **cada** pedido ao serviço de agendamento.

## Nada mais fica preso depois de um reinício

Se a máquina reiniciasse no meio de uma produção, a história ficava **presa para sempre** no estado "em andamento". Ninguém percebia — e como o sistema conta as histórias em andamento para decidir se busca mais roteiro, bastavam cinco presas para a busca parar de vez, em silêncio.

Ao subir, o sistema agora revisa o que ficou pela metade: histórias com todos os vídeos prontos são retomadas direto na etapa de agendamento, e as que pararam antes disso são marcadas como falhas, com o motivo escrito. De um jeito ou de outro, a fila é liberada.

## Tropeços passageiros não derrubam mais a produção

Uma conexão que cai ou um serviço que ainda está subindo devolvia erro e matava a história na hora. Agora cada chamada entre os serviços é repetida algumas vezes, com intervalo crescente, antes de desistir. Erros que são resposta definitiva — como "essa fila está cheia" — não são repetidos, porque insistir neles não muda nada.

## Preparado para ficar ligado sem ninguém olhando

Três mudanças de bastidor para o sistema aguentar rodar sozinho:

- **Volta sozinho.** Os serviços reiniciam automaticamente depois de uma queda ou de um reboot da máquina. Antes ficavam desligados até alguém reparar.
- **Log não enche mais o disco.** O registro de cada serviço passou a ser resumido em vez de detalhado, e é limitado a um tamanho máximo com descarte do que é antigo. Antes, cada chamada de rede gravava cabeçalhos inteiros, sem limite de tamanho.
- **O modelo de transcrição não é mais baixado toda vez.** Ele agora fica guardado na máquina; antes, cada atualização de container baixava de novo os 420 MB e dependia do serviço externo estar no ar naquele momento.

## Você é avisado no WhatsApp quando algo acontece

Até aqui o sistema trabalhava mudo. Um vídeo podia falhar às três da manhã e a primeira pessoa a descobrir era quem fosse conferir o perfil dias depois — e o caso pior nem parecia problema: todos os serviços respondendo normalmente e, mesmo assim, nenhum vídeo saindo.

Agora cada passo manda uma mensagem no WhatsApp, na hora em que acontece:

| O que chega no celular | Quando |
|---|---|
| 🔎 Pesquisa de roteiros iniciada | O scout começa a procurar histórias (de hora em hora) |
| 📝 História enviada ao pipeline | Achou uma história boa — vem com o título, o subreddit e a nota que ela tirou |
| 📥 Roteiro recebido | O pipeline começou a trabalhar naquela história |
| ✂️ Roteiro refinado | O LLM terminou — em quantas partes ficou, quem narra, qual é a frase de abertura |
| 🎙️ 🖼️ Gancho narrado / card pronto | As duas peças da abertura do vídeo ficaram prontas |
| 🔊 Narração pronta | O áudio de uma parte saiu |
| 🎞️ 🎬 Render iniciado / vídeo renderizado | O vídeo de cada parte, com o clipe de fundo que ele usou |
| 📅 Publicação agendada | Cada parte, com o dia e a hora em que vai ao ar |
| 🚀 Run concluído | Tudo daquela história agendado |
| ⏸️ Fila do Buffer cheia | O run está esperando vaga — não é erro, mas explica por que nada saiu |
| ⚠️ Avisos | Algo saiu pior do que deveria, mas o vídeo continuou |
| ❌ Falhas | Alguma coisa parou de verdade |

**Os avisos ⚠️ são a parte menos óbvia e a mais útil.** Existe uma classe de problema que nunca aparecia em lugar nenhum: o vídeo publica, o sistema diz que deu tudo certo, e mesmo assim ele saiu pior. É o caso do gancho que não pôde ser narrado, do card que não foi composto, da narração que saiu na velocidade errada porque o template não pôde ser lido, ou de todos os vídeos dividindo o mesmo fundo porque a biblioteca de clipes estava vazia. Nenhuma dessas coisas derruba o vídeo — e por isso mesmo nenhuma delas aparecia. Agora todas avisam.

**Você pode diminuir o volume depois.** No começo vale receber tudo: é assim que se descobre o que o sistema realmente faz quando roda sozinho. Quando o fluxo já for previsível, uma linha de configuração corta as mensagens miúdas e deixa só os marcos, ou só os problemas. Nada disso exige mexer no código.

**O aviso agora sai no celular por um app próprio, não mais pelo WhatsApp.** O canal antigo era um serviço gratuito de WhatsApp com uma cota mensal de mensagens, e em 16/08/2026 essa cota acabou. As notificações pararam de chegar e **ninguém foi avisado de que elas tinham parado** — o serviço continuava respondendo "recebi" para mensagens que jamais entregava, então o sistema achava que estava tudo certo. O silêncio durou dias e só foi notado porque você estranhou não receber nada.

Duas coisas mudaram por causa disso. A primeira: o destino passou a ser o **ntfy**, um app de notificação sem cota — não existe mais um limite mensal para estourar. Você instala o app no celular, assina um endereço secreto, e as mensagens chegam como notificação normal do celular. A segunda, mais importante: o sistema agora **confere se a mensagem foi realmente aceita**, em vez de confiar no "recebi". Se um destino passar a recusar mensagens, isso vira um registro explícito de falha em vez de silêncio.

**Por que isso importa mais do que parece:** o canal de aviso é o único que conta quando todo o resto quebra. Um canal de aviso que morre em silêncio é pior do que não ter canal nenhum, porque a ausência de mensagens passa a ser interpretada como "está tudo bem". Era exatamente o que estava acontecendo.

**E existe um alarme para o silêncio.** Notificação só funciona quando o sistema está vivo o bastante para mandá-la — uma queda de luz não avisa ninguém. Por isso o sistema também bate ponto num serviço externo de vigília: um sinal a cada 15 minutos dizendo "estou de pé", outro a cada busca do scout, e um terceiro **só quando um vídeo é concluído de verdade**. Se os sinais param de chegar, é o serviço de fora que avisa. O terceiro é o que resolve o pior caso de todos: tudo aparentemente funcionando e nenhum vídeo saindo.

## A legenda pergunta de que lado você está

O vídeo escolhido é o que tem alguém claramente errado. A legenda passou a fechar o ciclo: quando existe um vilão na história, o convite ao comentário pede o **seu veredito sobre ele** — "ela tava errada de perdoar?" — em vez do convite genérico de antes. Tomar partido é o que faz alguém parar de assistir e escrever.

A regra tem limite escrito: sem xingamento e sem mandar odiar ninguém. A pergunta é o convite; a raiva é de quem responde. Histórias sem vilão continuam com o convite normal, sobre o que você faria no lugar de quem viveu aquilo.

## Os vídeos saem no horário em que o público está acordado

Antes os posts saíam às 21h, 9h e 17h de Brasília — um deles à noite, quase no fim do dia, e outro logo cedo. Não era escolha: os horários estavam escritos em UTC no arquivo de configuração, e ninguém tinha convertido para o fuso de quem assiste.

Desde 25 de agosto de 2026 a publicação acontece **entre 11h e 20h**, três vezes por dia: **11h, 15h e 19h**, sempre no horário de Brasília.

**Por que o último é às 19h e não às 20h.** Quando uma história é dividida, a parte 2 sai meia hora depois da parte 1 — ela não entra na fila de horários, ela pendura na anterior. Um último post às 20h em ponto empurraria a continuação para 20h30, fora da janela. A folga de uma hora é o que mantém a história inteira dentro dela.

**O erro que isso previne é silencioso.** Um horário fora da janela não gera falha nenhuma: o vídeo é produzido, agendado e publicado normalmente — só que de madrugada. Por isso existe agora uma verificação automática que lê a configuração e recusa qualquer horário fora de 11h–20h, junto com a conta da continuação.

## Dois hábitos que faziam todo vídeo parecer o mesmo vídeo

Os vídeos do canal são montados por uma receita, e isso é o que permite produzir três por
dia. O problema é quando a receita deixa marcas iguais em todos eles — aí quem assiste (e
quem distribui) começa a ver um vídeo só, repetido.

Duas dessas marcas foram removidas em 28/08/2026.

**O fundo repetia muito antes de a biblioteca acabar.** Existem 40 clipes de fundo, e a
ideia sempre foi rodar entre eles para dois vídeos seguidos não parecerem o mesmo com
outras palavras por cima. Só que o sistema sorteava um clipe a cada vídeo, sem lembrar
quais já tinham saído — como tirar uma carta do baralho e devolvê-la antes da próxima. Na
prática, dos 47 vídeos publicados, 20 reaproveitaram um fundo que já tinha aparecido, e a
primeira repetição foi no segundo dia do canal. Agora o sistema anota o fundo de cada vídeo
e escolhe sempre entre os que ainda não saíram: a biblioteca inteira passa antes de
qualquer clipe voltar. Um clipe novo adicionado à pasta entra na frente da fila, então
material novo chega ao canal sem esperar o ciclo terminar.

**A legenda terminava sempre igual.** Todo post levava as mesmas duas hashtags fixas, e as
vagas restantes eram preenchidas sempre pelas primeiras da lista — 47 legendas terminando
com as mesmas palavras na mesma ordem. As hashtags fixas foram removidas, e as de
preenchimento agora vêm embaralhadas de um jeito diferente em cada post. O que descreve a
história continua vindo do texto dela, como antes.

Em ambos os casos o comportamento é reproduzível: refazer o mesmo vídeo devolve o mesmo
fundo e a mesma legenda, para que uma nova tentativa não publique algo diferente do que foi
conferido.

## O fundo dos vídeos virou uma biblioteca que não cabe no disco

O canal tinha 40 clipes de fundo cortados de um único vídeo de gameplay, e
depois 11 clipes de ASMR. Em 29 de agosto de 2026 a fonte passou a ser uma
playlist de **326 vídeos** de gameplay sem direitos autorais — 55 horas de
material, que cortadas em pedaços de dois minutos dão **1.677 fundos
diferentes**. A três vídeos por dia, isso é mais de um ano antes de qualquer
fundo se repetir.

O problema é que baixar tudo custaria em torno de 100 GB, num servidor que tem
175 GB livres. Então nada é baixado antes da hora: o sistema guarda apenas uma
**lista** de onde cada pedaço mora, e vai buscar o vídeo só no momento em que
aquele pedaço é sorteado para um vídeo. Baixa só os dois minutos que interessam,
não o vídeo inteiro. Depois de alguns dias o sistema apaga os pedaços que estão
há mais tempo sem aparecer, mantendo no máximo algumas dezenas guardados.

Como os vídeos da playlist são horizontais e o formato do canal é vertical,
cada pedaço é recortado pelo meio. Comparamos esse recorte com a alternativa —
encaixar a imagem inteira no meio da tela e preencher as bordas com uma versão
borrada dela mesma — e o recorte ganhou com folga: a câmera do jogo mantém o
carro no centro, então nada de importante se perde, e o céu no topo e o chão na
base são justamente onde entram o card de comentário e a legenda.

**Se o download falhar, o vídeo sai assim mesmo**, com outro fundo que já esteja
disponível. Um tropeço de rede não pode custar um vídeo que já passou pelo
roteirista, pela narração e pela legendagem — mas você recebe um aviso no
celular dizendo que aconteceu.

## Fluxo completo resumido

```
Você escreve um roteiro em texto
         ↓
O LLM melhora o texto e define as partes
         ↓
A história inteira vira um áudio narrado (só divide acima de 30 min)
         ↓
Os silêncios do áudio são removidos
         ↓
O áudio é montado num vídeo com legenda e trilha sonora
         ↓
O vídeo é agendado no TikTok e no YouTube, no mesmo horário
         ↓
Os dois publicam automaticamente no horário certo
```

Do roteiro à publicação, sem intervenção manual.
