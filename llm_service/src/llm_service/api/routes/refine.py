import json

from fastapi import APIRouter, HTTPException
from structlog import get_logger

from src.llm_service.llm.factory import get_llm_client
from src.llm_service.prompts.refine import SYSTEM_PROMPT, build_user_prompt
from src.llm_service.schemas.refine import RefineRequest, RefineResponse

router = APIRouter()
log = get_logger(__name__)


@router.post("/refine", response_model=RefineResponse)
async def refine(body: RefineRequest) -> RefineResponse:
    log.info("refine request", script_len=len(body.script))
    client = get_llm_client()
    user_prompt = build_user_prompt(body.script, body.metadata)

    try:
        result = await client.refine(SYSTEM_PROMPT, user_prompt)
    except json.JSONDecodeError as exc:
        log.error("llm returned invalid json", error=str(exc))
        raise HTTPException(status_code=502, detail="LLM returned invalid JSON")
    except Exception as exc:
        log.error("llm request failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")

    log.info(
        "refine done",
        parts=len(result.parts),
        content_type=result.classification.content_type,
        hook_chars=len(result.hook),
    )
    return result
