import re

from pydantic import BaseModel, Field, model_validator


#: A frase gancho é narrada sozinha, num arquivo separado. Um "gancho" de 600
#: palavras seria a parte inteira de novo — o teto existe para o caso em que o
#: modelo omite o campo e a frase precisa ser derivada de um texto sem
#: pontuação terminal.
MAX_HOOK_CHARS = 200

#: Fim de frase = pontuação terminal (+ aspas/parênteses de fechamento) seguida
#: de espaço. Exigir o espaço é o que impede `R$ 3.5 milhões` de virar frase.
_SENTENCE_END = re.compile(r'[.!?…]+["\'”’)\]]*(?=\s)')

#: Teto rígido do título de vídeo do YouTube. Não é escolha editorial: a API
#: recusa acima disso, e o Buffer exige o campo na criação do post.
MAX_TITLE_CHARS = 100

#: Gênero de quem narra a história. `unknown` não é falha: história sem narrador
#: identificável — ou narrada em terceira pessoa — é resultado normal, e é o que
#: manda o `tts_service` manter a voz padrão.
NARRATOR_GENDERS = ("male", "female", "unknown")


def normalize_narrator_gender(value: str | None) -> str:
    """Qualquer entrada para um valor de ``NARRATOR_GENDERS``.

    Normaliza em vez de rejeitar: o campo sai de um LLM, e um `"masculino"` ou um
    `"m"` não podem derrubar o refino de um roteiro que está inteiro e correto.
    O custo de errar é a voz de sempre, que é o que todo vídeo já usava.
    """
    key = (value or "").strip().lower()
    return key if key in NARRATOR_GENDERS else "unknown"


#: Clima emocional da história, usado pelo orchestrador para escolher a trilha
#: sonora. `neutral` é o piso — cai nele tanto a história sem carga emocional
#: clara quanto o modelo que não devolve o campo, e é a única faixa com trilha
#: garantida hoje (o resto da biblioteca ainda está sendo montada).
MOODS = ("sad", "tense", "hopeful", "neutral")


def normalize_mood(value: str | None) -> str:
    """Qualquer entrada para um valor de ``MOODS``.

    Mesma razão do `normalize_narrator_gender`: o campo sai de um LLM, e um
    valor fora do contrato não pode derrubar o refino. O custo de errar é a
    trilha neutra, que é o que todo vídeo já usava antes deste campo existir.
    """
    key = (value or "").strip().lower()
    return key if key in MOODS else "neutral"


def derive_hook(parts: list[str]) -> str:
    """Primeira frase da parte 1, limitada a ``MAX_HOOK_CHARS``.

    Só entra em ação quando o modelo não devolve ``hook``: o prompt pede o
    campo, mas o contrato não pode depender de o modelo obedecer.
    """
    if not parts:
        return ""

    opening = parts[0].strip()
    if not opening:
        return ""

    end = _SENTENCE_END.search(opening)
    first = opening[: end.end()] if end else opening
    if len(first) <= MAX_HOOK_CHARS:
        return first

    # Sem pontuação terminal: corta na última palavra inteira que cabe, em vez
    # de mandar meia palavra para o narrador.
    cut = first[:MAX_HOOK_CHARS].rsplit(" ", 1)[0].strip()
    return cut or first[:MAX_HOOK_CHARS]


def truncate_title(text: str) -> str:
    """``text`` cortado em ``MAX_TITLE_CHARS`` sem partir palavra ao meio."""
    title = " ".join(text.split())
    if len(title) <= MAX_TITLE_CHARS:
        return title

    cut = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].strip()
    # Título de uma palavra só, mais longa que o teto: corte seco, porque um
    # título vazio faria o Buffer recusar o post inteiro.
    return (cut or title[:MAX_TITLE_CHARS]).rstrip(" ,;:-–—")


#: Teto do CTA de votação binária. Ele é o que abre a legenda no TikTok, e só
#: os primeiros ~60-80 caracteres aparecem antes do corte de "...mais" — o
#: valor tem folga porque garantir a visibilidade exata é trabalho de outra
#: frente (cortar hashtag/rótulo de parte depois dele), não deste campo.
MAX_BINARY_CTA_CHARS = 100


def truncate_binary_cta(text: str) -> str:
    """``text`` cortado em ``MAX_BINARY_CTA_CHARS`` sem partir palavra ao meio."""
    cta = " ".join(text.split())
    if len(cta) <= MAX_BINARY_CTA_CHARS:
        return cta

    cut = cta[:MAX_BINARY_CTA_CHARS].rsplit(" ", 1)[0].strip()
    return cut or cta[:MAX_BINARY_CTA_CHARS]


def derive_youtube_title(hook: str, parts: list[str]) -> str:
    """Título do vídeo quando o modelo não devolve ``youtube_title``.

    Cai no gancho, que é a frase escrita justamente para prender — o pior
    título aceitável é bem melhor que nenhum, porque sem título o Buffer recusa
    a criação do post e o vídeo não sai no YouTube.
    """
    source = hook.strip() or derive_hook(parts)
    return truncate_title(source) if source else ""


class RefineRequest(BaseModel):
    script: str
    metadata: dict = {}


class TargetAudience(BaseModel):
    age_range: list[int] = Field(min_length=2, max_length=2)
    gender: str
    interests: list[str]


class Classification(BaseModel):
    content_type: str
    tone: str
    target_audience: TargetAudience
    cta_per_part: list[str]
    hashtag_hints: list[str]
    split_rationale: str | None = None
    #: Pergunta de escolha binária para a legenda ("quem errou mais: X ou Y?"),
    #: pensada para quem só lê o texto antes de assistir. Não é `cta_per_part`:
    #: aquele é a pergunta que FECHA A NARRAÇÃO e nunca vai para a legenda (ver
    #: REGRA DO FECHAMENTO); este é texto novo, só para o post. Vazio é
    #: resultado normal — a legenda segue sem pergunta, como antes deste campo.
    binary_cta: str = ""

    @model_validator(mode="after")
    def _truncate_binary_cta(self) -> "Classification":
        stripped = self.binary_cta.strip()
        self.binary_cta = truncate_binary_cta(stripped) if stripped else ""
        return self


class RefineResponse(BaseModel):
    parts: list[str]
    classification: Classification
    #: A frase que abre o vídeo. É a mesma primeira frase da parte 1 — vem
    #: separada porque o pipeline a narra num arquivo próprio.
    hook: str = ""
    #: Gênero de quem conta a história em primeira pessoa. Decide a voz da
    #: narração no `tts_service` — não é o público-alvo, que é
    #: `classification.target_audience.gender` e responde outra pergunta.
    narrator_gender: str = "unknown"
    #: Título do vídeo no YouTube. O TikTok não tem título — só legenda —, então
    #: este campo não tem paralelo lá: é um gênero próprio de texto, escrito
    #: para ser lido antes de o vídeo abrir e para ser encontrado na busca.
    youtube_title: str = ""
    #: Clima emocional da história — escolhe a trilha sonora, não é uma leitura
    #: de audiência. Não é `classification.tone`: tone informa hashtag/edição e
    #: mistura registros (funny/educational/shocking) que não mapeiam para
    #: música; mood responde só "que cama sonora combina com isto".
    mood: str = "neutral"

    #: Antes de todos os outros: o formato é um vídeo por história, e o resto do
    #: pipeline (uma `PipelinePart` por elemento, o rótulo "(Parte 1/2)", o
    #: encadeamento de slots) se organiza a partir desta lista.
    @model_validator(mode="after")
    def _collapse_to_single_part(self) -> "RefineResponse":
        """Roteiro dividido pelo modelo volta a ser um só.

        **Junta em vez de descartar o excedente.** O prompt proíbe dividir, mas o
        contrato não pode depender de obediência — e os dois modos de falha não
        se equivalem: um vídeo longo demais é um vídeo ruim, visível na hora e
        auditável em `split_rationale`; um vídeo sem o fim da história é um
        produto quebrado, e ninguém repara até alguém assistir. O corte de
        tamanho é responsabilidade do prompt, não deste validator, justamente
        porque aqui não há como cortar sem partir frase ao meio.
        """
        kept = [p for p in self.parts if p and p.strip()]
        if len(kept) > 1:
            self.parts = [" ".join(p.strip() for p in kept)]
        else:
            self.parts = kept
        return self

    @model_validator(mode="after")
    def _fill_hook(self) -> "RefineResponse":
        self.hook = self.hook.strip() or derive_hook(self.parts)
        return self

    #: Depois de `_fill_hook` de propósito: o fallback do título é o gancho, que
    #: precisa já estar preenchido. Validators `mode="after"` rodam na ordem de
    #: definição, então inverter as duas derivaria o título de um gancho vazio.
    @model_validator(mode="after")
    def _fill_youtube_title(self) -> "RefineResponse":
        given = self.youtube_title.strip()
        self.youtube_title = truncate_title(given) if given else derive_youtube_title(self.hook, self.parts)
        return self

    @model_validator(mode="after")
    def _normalize_narrator(self) -> "RefineResponse":
        self.narrator_gender = normalize_narrator_gender(self.narrator_gender)
        return self

    @model_validator(mode="after")
    def _normalize_mood(self) -> "RefineResponse":
        self.mood = normalize_mood(self.mood)
        return self
