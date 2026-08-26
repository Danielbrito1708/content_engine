from pydantic import BaseModel, field_validator

#: Score bounds. A model that answers outside them is clamped rather than
#: rejected — see ``_clamp``.
MIN_SCORE = 0
MAX_SCORE = 10


class StoryItem(BaseModel):
    """One candidate to judge, identified by its position in the request.

    ``opening`` is the start of the post, not the whole body: the endpoint judges
    whether the first seconds promise a payoff, which is the only thing a
    short-form viewer ever sees before deciding to swipe.
    """

    index: int
    opening: str
    title: str = ""


class StoryQualityRequest(BaseModel):
    """A batch. Batching is the point — see the route docstring for why."""

    items: list[StoryItem]


class StoryVerdict(BaseModel):
    """Judgement on one candidate.

    ``hook`` and ``score`` answer different questions and can disagree: a title
    can promise a great payoff over a body that then rambles (hook without
    story), and a well-told story can open on a buried lede (story without hook).
    ``outrage`` is a third such question — see the field.
    """

    index: int
    #: Does the title or the opening promise a story worth staying for?
    hook: bool
    #: Overall storytelling strength, 0–10. The caller decides where "weak" starts.
    score: int
    #: How much indignation the story is likely to provoke, 0–10. A third axis,
    #: not a flavour of ``score``: a badly written post can be infuriating and a
    #: beautifully told one can have nobody to be angry at. ``None`` means the
    #: model did not answer it — distinct from a zero, which is a judgement.
    outrage: int | None = None
    #: Is there someone in the story whose behaviour is plainly indefensible?
    #: Asked as a boolean next to the score because "who is the asshole here" is
    #: the question the audience answers in the comments, and a story without an
    #: answer to it does not get comments no matter how high the outrage reads.
    villain: bool = False
    #: The line the model read as the hook, when there was one. Kept for
    #: calibration: it shows *what* the model rewarded, not just how much.
    hook_line: str | None = None
    reason: str | None = None

    @field_validator("score", "outrage", mode="before")
    @classmethod
    def _clamp(cls, value):
        """Pull out-of-range scores into range instead of failing the batch.

        One bad number must not cost the verdict on every other candidate in the
        request — a batch is only cheaper than N calls if it does not fail as one.
        """
        try:
            return max(MIN_SCORE, min(MAX_SCORE, int(value)))
        except (TypeError, ValueError):
            return value


class StoryQualityResponse(BaseModel):
    results: list[StoryVerdict]
