from fastapi import APIRouter

from src.blender_worker.schemas.timeline import (
    ResolvedClipOut,
    TimelineIssue,
    TimelineValidateRequest,
    TimelineValidateResponse,
)
from src.blender_worker.timeline.expr import ExprError
from src.blender_worker.timeline.loader import TimelineLoadError, load_timeline
from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.resolver import TimelineResolutionError, resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc

router = APIRouter(prefix="/timelines")


@router.post("/validate", response_model=TimelineValidateResponse)
async def validate_timeline(body: TimelineValidateRequest) -> TimelineValidateResponse:
    """Fase 3, nível 1 of docs/edicao_declarativa.md — resolves a timeline
    YAML to frame numbers without Blender, a job, or a real asset. Always
    `200`: a bad template is an expected, constant state while editing, not a
    transport-level failure — see the module note in blender_worker/CLAUDE.md
    for the reasoning. FastAPI's own `422` still applies if the request body
    itself doesn't match `TimelineValidateRequest` (e.g. `template` missing).
    """
    try:
        doc = load_timeline(body.template)
    except TimelineLoadError as exc:
        return TimelineValidateResponse(
            ok=False,
            errors=[TimelineIssue(location=e.location, message=e.message) for e in exc.errors],
        )

    flags = {**doc.flags, **body.flags}
    inputs = {name: round(seconds * doc.canvas.fps) for name, seconds in body.inputs.items()}

    try:
        resolved = resolve_timeline(doc, inputs=inputs, flags=flags)
        # Discarded — run purely for the extra validation it does that
        # resolve_timeline doesn't: `max_hold`/`fade`/`fade_out.duration`
        # expressions and `AudioClip.muted`'s `$flag` reference are only
        # ever evaluated inside build_payload (see its module docstring).
        # Without this call, a bad one of those would only ever surface in
        # the (out-of-scope, Blender-side) Fase 2 executor.
        build_payload(doc, resolved, flags=flags)
    except (TimelineResolutionError, ExprError) as exc:
        return TimelineValidateResponse(
            ok=False,
            errors=[TimelineIssue(location="<resolve>", message=str(exc))],
        )

    return TimelineValidateResponse(
        ok=True,
        timeline_end=resolved.timeline_end,
        anchors=resolved.anchors,
        clips=[
            ResolvedClipOut(
                track=c.track,
                channel=c.channel,
                role=c.role,
                type=c.type,
                frame_start=c.frame_start,
                frame_end=c.frame_end,
                repeats=c.repeats,
                fade_start=c.fade_start,
                extra=c.extra,
            )
            for c in resolved.clips
        ],
        warnings=resolved.warnings,
    )


@router.get("/schema")
async def get_timeline_schema() -> dict:
    """Raw `TimelineDoc.model_json_schema()` — for an external editor's
    autocomplete/validation of the YAML document. No post-processing: this
    pydantic version already emits `by_alias=True` by default, so
    `Conditional.else_` already serialises as `"else"`."""
    return TimelineDoc.model_json_schema()
