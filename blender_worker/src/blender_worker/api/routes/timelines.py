import asyncio
import glob
import os
import shutil
import subprocess
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.blender_worker.db.engine import get_session
from src.blender_worker.db.models import Template, Video
from src.blender_worker.schemas.timeline import (
    ResolvedClipOut,
    TimelineIssue,
    TimelinePreviewClipRequest,
    TimelinePreviewFrameRequest,
    TimelineValidateRequest,
    TimelineValidateResponse,
)
from src.blender_worker.timeline.expr import ExprError
from src.blender_worker.timeline.loader import TimelineLoadError, load_timeline
from src.blender_worker.timeline.payload import build_payload
from src.blender_worker.timeline.preview import assemble_preview
from src.blender_worker.timeline.resolver import TimelineResolutionError, resolve_timeline
from src.blender_worker.timeline.schema import TimelineDoc
from src.core import settings

log = get_logger(__name__)

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


# --- Fase 3, níveis 2/3 — preview de frame único e clipe curto -----------
#
# Diferente de /validate: erro vira status HTTP de verdade (422/404/502), não
# `200` com `ok: false`. Essas rotas são uma ação explícita ("gerar preview"),
# não algo disparado a cada tecla digitada — ver blender_worker/CLAUDE.md.


async def _get_video_or_404(video_id: uuid.UUID, session: AsyncSession) -> Video:
    video = await session.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


async def _get_template_or_404(template_id: uuid.UUID, session: AsyncSession) -> Template:
    result = await session.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


def _load_doc_or_422(template_text: str) -> TimelineDoc:
    try:
        return load_timeline(template_text)
    except TimelineLoadError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"location": e.location, "message": e.message} for e in exc.errors],
        )


async def _assemble_or_error(*, video: Video, template: Template, doc: TimelineDoc, flags: dict[str, bool]):
    try:
        return await assemble_preview(
            video=video, template_blend_key=template.blend_key, doc=doc, flags=flags,
        )
    except (TimelineResolutionError, ExprError) as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"location": "<resolve>", "message": str(exc)}],
        )
    except Exception as exc:
        log.error("preview assemble failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Preview assembly failed: {exc}")


@router.post("/preview/frame")
async def preview_frame(
    body: TimelinePreviewFrameRequest,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A single rendered frame — level 2 of "Loop de preview". Requires a
    real `video_id` (unlike `/validate`): a synthetic asset would answer
    "does the layout math work" but not "does the card sit where it should",
    which is the whole point of looking at an actual frame."""
    video = await _get_video_or_404(body.video_id, session)
    template = await _get_template_or_404(body.template_id, session)
    doc = _load_doc_or_422(body.template)
    flags = {**doc.flags, **body.flags}

    output_path, tmpdir, _resolved = await _assemble_or_error(
        video=video, template=template, doc=doc, flags=flags
    )
    try:
        prefix = os.path.join(tmpdir, "frame_")
        result = await asyncio.to_thread(
            subprocess.run,
            [settings.CONFIG.blender.bin, "-b", output_path, "-o", prefix, "-F", "PNG", "-f", str(body.frame)],
            capture_output=True,
            text=True,
        )
        produced = glob.glob(prefix + "*.png")
        if result.returncode != 0 or not produced:
            log.error(
                "frame preview render failed",
                returncode=result.returncode,
                stdout=result.stdout[-3000:],
                stderr=result.stderr[-3000:],
            )
            raise HTTPException(
                status_code=502,
                detail=f"Blender exited {result.returncode}. stderr: {result.stderr[-500:]}",
            )
        with open(produced[0], "rb") as f:
            png_bytes = f.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return Response(content=png_bytes, media_type="image/png")


@router.post("/preview/clip")
async def preview_clip(
    body: TimelinePreviewClipRequest,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A short low-resolution clip — level 3. Same real-asset requirement as
    the frame preview above, plus an optional `resolution_percentage` (the
    speedup factor is unmeasured — docs/edicao_declarativa.md § "Loop de
    preview" — so this is opt-in, not defaulted down)."""
    video = await _get_video_or_404(body.video_id, session)
    template = await _get_template_or_404(body.template_id, session)
    doc = _load_doc_or_422(body.template)
    flags = {**doc.flags, **body.flags}

    output_path, tmpdir, resolved = await _assemble_or_error(
        video=video, template=template, doc=doc, flags=flags
    )
    try:
        frame_rate = doc.canvas.fps
        start_frame = max(1, round(body.start_s * frame_rate) + 1)
        end_frame = min(resolved.timeline_end, start_frame + round(body.duration_s * frame_rate))
        if end_frame <= start_frame:
            raise HTTPException(
                status_code=422,
                detail=[{
                    "location": "<preview>",
                    "message": (
                        f"clip range is empty after clamping to the resolved timeline "
                        f"(start={start_frame}, end={end_frame}, timeline_end={resolved.timeline_end})"
                    ),
                }],
            )

        clip_path = os.path.join(tmpdir, "clip.mp4")
        cmd = [settings.CONFIG.blender.bin, "-b", output_path]
        if body.resolution_percentage is not None:
            cmd += [
                "--python-expr",
                f"import bpy; bpy.context.scene.render.resolution_percentage = {int(body.resolution_percentage)}",
            ]
        cmd += ["-o", clip_path, "-s", str(start_frame), "-e", str(end_frame), "-a"]

        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(clip_path):
            log.error(
                "clip preview render failed",
                returncode=result.returncode,
                stdout=result.stdout[-3000:],
                stderr=result.stderr[-3000:],
            )
            raise HTTPException(
                status_code=502,
                detail=f"Blender exited {result.returncode}. stderr: {result.stderr[-500:]}",
            )
        with open(clip_path, "rb") as f:
            mp4_bytes = f.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return Response(content=mp4_bytes, media_type="video/mp4")
