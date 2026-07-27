import json

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from structlog import get_logger

from src.core import settings
from src.llm_service.llm.factory import get_llm_client
from src.llm_service.prompts.moderate import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.moderate import ModerateRequest, ModerateResponse

router = APIRouter()
log = get_logger(__name__)


@router.post("/moderate", response_model=ModerateResponse)
async def moderate(body: ModerateRequest) -> ModerateResponse:
    """Decide whether a story is safe to publish as a short-form video.

    Replaces a substring blocklist, which could not tell ``"3 mil que não me
    mataria"`` (a figure of speech about money) from an actual threat. Safety
    here is a judgement about context, so it takes a model — but a cheap one,
    since the call is a yes/no.

    Errors surface as 502: the caller must be able to tell "unsafe" from
    "could not check", and treat the second as a retry, never as approval.
    """
    client = get_llm_client(model=settings.env.llm_moderation_model)

    try:
        data = await client.complete_json(SYSTEM_PROMPT, build_user_prompt(body.title, body.text))
        result = ModerateResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.error("moderation returned unusable output", error=str(exc))
        raise HTTPException(status_code=502, detail="LLM returned invalid moderation JSON")
    except Exception as exc:
        log.error("moderation request failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")

    log.info("moderation done", safe=result.safe, category=result.category)
    return result
