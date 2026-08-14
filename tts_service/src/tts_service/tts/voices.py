"""Which voice narrates — decided by the gender of the story's narrator.

The stories are told in the first person, so the narrator has a gender and the
voice has to agree with it: a man's story read by a female voice is the first
thing a viewer notices, and no amount of pacing or loudness work recovers it.

The mapping lives in this service, which owns the providers, and not in the
orchestrator: `edge` and `azure` serve the same neural voice names, so "male"
means one string for both. What the orchestrator sends is the *narrator's
gender* — a fact about the script — never a voice name. The names themselves are
`TTS_VOICE_MALE` / `TTS_VOICE_FEMALE` (defaults in `src/core/config.py`) and are
injected here, so this module stays pure.
"""

MALE = "male"
FEMALE = "female"
#: Not "we failed to look": a story whose narrator has no stated gender, or none
#: that matters, is a normal outcome. It keeps whatever `TTS_VOICE` is set to.
UNKNOWN = "unknown"

GENDERS = (MALE, FEMALE, UNKNOWN)


def normalize_gender(value: str | None) -> str:
    """Any input to one of ``GENDERS``. Anything unrecognised is ``UNKNOWN``.

    Lenient on purpose. The value originates in an LLM classification several
    services upstream, and the worst case of a wrong one is a video narrated in
    the voice that every video used before this existed. Rejecting the request
    would cost the whole run over a cosmetic field; the resolved voice is logged,
    so a model that starts answering "masculino" is visible without being fatal.
    """
    key = (value or "").strip().lower()
    return key if key in GENDERS else UNKNOWN


def resolve_voice(gender: str | None, *, default: str, male: str, female: str) -> str:
    """The voice for a narrator of `gender`; `default` when it is not known.

    Pure — the three voices are injected — because `settings.env` is a frozen
    model built once at bootstrap, so a test cannot reach it through the
    environment.
    """
    key = normalize_gender(gender)
    if key == MALE:
        return male
    if key == FEMALE:
        return female
    return default
