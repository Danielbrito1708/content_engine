import json

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from structlog import get_logger

from src.core import settings
from src.llm_service.llm.factory import get_llm_client
from src.llm_service.prompts.story import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.story import (
    StoryQualityRequest,
    StoryQualityResponse,
    StoryVerdict,
)

router = APIRouter()
log = get_logger(__name__)


@router.post("/story-quality", response_model=StoryQualityResponse)
async def story_quality(body: StoryQualityRequest) -> StoryQualityResponse:
    """Rank candidates by hook strength and storytelling, in one batched call.

    Takes a list rather than a single story on purpose. Unlike moderation — which
    only ever runs on the two or three candidates about to be published — this is
    a *selection* signal, so it has to see every candidate in the cycle to be able
    to order them. One call per candidate would be ~30 calls a cycle; one call
    carrying all 30 openings is a few thousand tokens and costs less than
    moderation already does.

    Each item carries its own ``index`` and the verdicts echo it back, so a model
    that reorders or drops entries cannot silently shift a score onto the wrong
    story.

    Degrades per item, not per batch: a malformed verdict is dropped and the rest
    are returned, because the caller treats a missing verdict as "not evaluated"
    and falls back to the source's own ranking. A response that yields no usable
    verdict at all is a 502 — that is a failure, not an empty answer.
    """
    if not body.items:
        return StoryQualityResponse(results=[])

    client = get_llm_client(model=settings.env.llm_story_model)

    try:
        data = await client.complete_json(SYSTEM_PROMPT, build_user_prompt(body.items))
    except json.JSONDecodeError as exc:
        log.error("story quality returned unusable output", error=str(exc))
        raise HTTPException(status_code=502, detail="LLM returned invalid story JSON")
    except Exception as exc:
        log.error("story quality request failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")

    raw_results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(raw_results, list):
        log.error("story quality response has no results list", keys=list(data or {}))
        raise HTTPException(status_code=502, detail="LLM returned no story results")

    requested = {item.index for item in body.items}
    results: list[StoryVerdict] = []
    seen: set[int] = set()

    for entry in raw_results:
        try:
            verdict = StoryVerdict.model_validate(entry)
        except ValidationError as exc:
            log.warning("story verdict dropped", error=str(exc))
            continue
        # An index nobody asked about is the model inventing a story; a repeated
        # one is it answering twice. Neither can be matched back to a candidate.
        if verdict.index not in requested or verdict.index in seen:
            log.warning("story verdict has unusable index", index=verdict.index)
            continue
        seen.add(verdict.index)
        results.append(verdict)

    if not results:
        log.error("story quality yielded no usable verdicts", requested=len(requested))
        raise HTTPException(status_code=502, detail="LLM returned no usable story verdicts")

    log.info("story quality done", requested=len(requested), scored=len(results))
    return StoryQualityResponse(results=results)
