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

    @model_validator(mode="after")
    def _fill_hook(self) -> "RefineResponse":
        self.hook = self.hook.strip() or derive_hook(self.parts)
        return self
