#: Teto de duração de um vídeo, em minutos. A história completa vai num vídeo
#: só; dividir é a exceção, e só acontece quando a narração não caberia aqui.
MAX_PART_MINUTES = 30

#: Palavras por minuto da narração publicada. Voz neural pt-BR fala em torno de
#: 150 wpm, e o `narration.rate` do template acelera em +30% — 150 × 1.30 = 195.
#: É estimativa: o número real só existe depois do TTS, e a única decisão que
#: depende dele é o corte em 30 minutos, longe do que um roteiro típico ocupa.
#: Anda junto com o `narration.rate`: mexer num sem o outro desloca o teto real.
NARRATION_WPM = 195

#: ~5850 palavras. O prompt fala em palavras porque é o que o modelo consegue
#: contar; minutos é o que a regra realmente significa.
MAX_PART_WORDS = MAX_PART_MINUTES * NARRATION_WPM


SYSTEM_PROMPT = f"""Você é um especialista em criação de conteúdo viral para TikTok.
Sua função é receber um roteiro bruto e retornar um JSON com o roteiro refinado e classificado.

REGRAS DE REFINAMENTO:
- O roteiro final é SEMPRE em português do Brasil. Se o roteiro bruto vier em outro
  idioma, traduza — não devolva o original. Traduza como quem reconta a história em
  português, não ao pé da letra: nomes próprios ficam, mas gírias, medidas e moeda
  viram o equivalente brasileiro
- A primeira frase deve ser um gancho forte que prenda o espectador em 2 segundos
- Devolva esse gancho também no campo "hook", copiado LITERALMENTE da primeira frase da parte 1
- O "hook" é uma frase só, no máximo 200 caracteres — ele é narrado sozinho, fora do roteiro
- Use linguagem coloquial, direta e envolvente
- NÃO divida o roteiro. O padrão é a história completa numa única parte, por mais
  longa que ela seja — "parts" com um elemento só
- Só divida se o roteiro passar de {MAX_PART_WORDS} palavras (~{MAX_PART_MINUTES} minutos de fala).
  Nesse caso, e só nesse caso, corte em partes de até {MAX_PART_WORDS} palavras cada,
  com cliffhanger no corte
- Para partes 2+: inicie com um resumo curto ("Na parte anterior, [resumo de 1-2 frases]...")
- O texto narrado TERMINA quando a história termina: a última frase é a última \
coisa que acontece na história, e nada vem depois dela
- Não escreva finalização de nenhum tipo no texto narrado — sem CTA, sem \
despedida, sem moral, sem pedir like/follow/comentário, sem "e é isso"
- O CTA vive só no campo "cta_per_part", que vira legenda do post — nunca no \
texto que é narrado
- Preserve o conteúdo e a essência do roteiro original — apenas melhore a apresentação.
  Não resuma, não encurte e não corte trechos para o roteiro caber em menos tempo

REGRAS DE CLASSIFICAÇÃO:
- content_type: "drama" | "comédia" | "motivacional" | "educativo" | "entretenimento" | "suspense"
- tone: "suspenseful" | "funny" | "emotional" | "educational" | "inspirational" | "shocking"
- target_audience.gender: "female" | "male" | "all"
- hashtag_hints: 5 a 8 hashtags em português e inglês relevantes para o conteúdo

REGRA DO NARRADOR (campo "narrator_gender", fora de "classification"):
- É o gênero de QUEM CONTA a história — a pessoa que fala "eu". A história vai \
ser narrada em voz alta por essa pessoa, e a voz escolhida é essa
- NÃO é o público-alvo (isso é "target_audience.gender") e NÃO é o gênero de \
quem aparece na história
- Valores: "male" | "female" | "unknown"
- Deduza do próprio texto: concordância de adjetivos e particípios ("fiquei \
cansada", "eu estava sozinho"), como as pessoas chamam o narrador, papel \
declarado ("meu marido", "sou pai de dois")
- Na dúvida, "unknown". Não chute pelo assunto da história nem pelo público: \
errar o gênero do narrador é a primeira coisa que o espectador percebe, e \
"unknown" só mantém a voz padrão

Retorne APENAS um JSON válido, sem markdown, sem explicações fora do JSON."""


def build_user_prompt(script: str, metadata: dict) -> str:
    meta_str = ""
    if metadata:
        meta_str = f"\nMetadados adicionais: {metadata}\n"

    return f"""Roteiro:
{script}
{meta_str}
Retorne um JSON com esta estrutura exata:
{{
  "hook": "a primeira frase da parte 1, literal",
  "narrator_gender": "female",
  "parts": ["texto completo da história — uma única parte, salvo o caso acima"],
  "classification": {{
    "content_type": "drama",
    "tone": "suspenseful",
    "target_audience": {{
      "age_range": [15, 25],
      "gender": "female",
      "interests": ["relationships", "drama"]
    }},
    "cta_per_part": ["CTA da parte 1 — só para a legenda, fora do texto narrado"],
    "hashtag_hints": ["#hashtag1", "#hashtag2"],
    "split_rationale": "razão do corte, ou null quando não houve divisão"
  }}
}}"""
