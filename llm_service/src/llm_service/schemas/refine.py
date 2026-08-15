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
