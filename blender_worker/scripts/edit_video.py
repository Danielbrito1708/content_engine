"""
Blender edit script — runs inside Blender's Python interpreter.
Usage: blender -b template.blend -P scripts/edit_video.py -- /path/to/job_config.json
"""

import json
import os
import re
import sys

DEFAULT_FADE_FRAMES = 3
DEFAULT_MAX_HOLD_SECONDS = 0.4

# A file with no decodable video track still loads as a movie strip — Blender
# gives it one placeholder frame instead of raising. The render then *succeeds*
# and quietly produces a black background for its full length, which is worse
# than failing: the job reports `completed`, the MP4 looks plausible by size and
# duration, and nothing downstream can tell a broken asset from a deliberately
# dark one. Measured against the real RNA: the 1 KB `assets/background.mp4` stub
# reports frame_duration=1, a valid 5s/30fps clip reports 150. Two frames is the
# floor that separates them — a genuinely 1-frame background is a still image
# and belongs in an image strip, not here.
MIN_MOVIE_FRAMES = 2

# Ceiling on how many times the background clip may be laid end to end to cover
# the narration. A well-sized clip needs one or two repeats; a number this far
# above that only bounds the degenerate case, where a near-empty file would
# otherwise ask Blender for thousands of strips.
MAX_BACKGROUND_REPEATS = 60

# Resting position of the subtitle, as a fraction of frame height (0 = bottom,
# 0.5 = dead centre). Paired with align_y = "CENTER" below, so this is the
# position of the text's own middle, not of its baseline — 0.5 puts the word in
# the optical centre of the frame regardless of how tall the glyphs are.
# Overridable per template via `subtitles.y_position`.
DEFAULT_SUBTITLE_Y = 0.5
# Entrance animation: each word starts this far below the resting position and rises to it
# over rise_frames. Unlike the fade, this applies to every word — it's what makes
# each word read as a distinct "pop" even between back-to-back words.
DEFAULT_RISE_OFFSET = 0.025
DEFAULT_RISE_FRAMES = 4

# Subtitle typography. Futura Bold is the design default and ships in
# assets/fonts/ — that directory is inside the Docker build context, so the
# image picks it up via COPY. The chain still falls through to DejaVu Sans Bold
# (fonts-dejavu-core) so a checkout without the font degrades the look instead
# of failing the render.
_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts"
)
DEFAULT_FONT_CANDIDATES = (
    os.path.join(_FONT_DIR, "Futura-Bold.ttf"),
    os.path.join(_FONT_DIR, "Futura-Bold.otf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
# None means "leave the size Blender gave the strip" — the size is a separate
# concern from the typeface and changing it would resize every existing render.
DEFAULT_FONT_SIZE = None
# Fraction of the frame width kept clear on each side. Word-level subtitles are
# centred, so a word that measures wider than what is left simply runs off both
# edges — Blender neither wraps a single word nor warns.
SUBTITLE_SIDE_MARGIN = 0.04
# Floor for the auto-fit, so a pathologically long token shrinks to something
# still legible instead of collapsing toward zero.
MIN_AUTOFIT_FONT_SIZE = 60
# Intro (comment card + hook narration). Channels default here because
# `template.json` lives in the bucket, not in the repo: a deployed template
# written before the intro existed has no entry for them, and a render must not
# fail — or worse, land the card on top of the video strip — over that.
DEFAULT_HOOK_CHANNEL = 5
DEFAULT_CARD_CHANNEL = 6
# Silence between the last word of the hook and the first word of the narration.
# Zero makes the two run together as one breathless sentence; a third of a
# second is the pause a person leaves after reading a title out loud.
DEFAULT_HOOK_TAIL_SECONDS = 0.3
# Card centre, as a fraction of frame height (same convention as the subtitles).
DEFAULT_CARD_Y = 0.5
DEFAULT_CARD_FADE_FRAMES = 4

# Breathing room after the last narrated word. `content_end_frame` lands the last
# frame exactly on the end of the voice strip, and that is too tight for two
# reasons that compound: the strip end is rounded to the frame, and the MP4's
# final audio packet falls right on the cut — so the last consonant is clipped
# and the video reads as if it ended mid-word. Half a second is a breath: enough
# for the word to finish and for the music fade to land, short enough that it
# does not read as dead air. Overridable per template via
# `narration.tail_seconds`; 0 restores the old flush ending.
DEFAULT_END_PADDING_SECONDS = 0.5

# Music bed. The video has no outro: it ends when the narration ends, so the
# only thing that marks the ending is the bed going quiet under the last words.
# 1.5s is a beat and a half — long enough to read as an ending, short enough
# that the music is still there while the story is being told.
DEFAULT_MUSIC_FADE_SECONDS = 1.5
# 13 LU below the narration in the measured mixdown: audible as a bed without
# competing with the voice.
DEFAULT_MUSIC_VOLUME = 0.2

DEFAULT_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)
DEFAULT_OUTLINE_COLOR = (0.0, 0.0, 0.0, 1.0)
# Blender's own default (0.05) is a hairline that disappears over a bright
# frame. 0.24 is the upper bound that still holds the letterforms: past ~0.30
# the outline blobs merge between adjacent glyphs and the counters of round
# letters start closing, which costs legibility at word-per-frame speed.
DEFAULT_OUTLINE_WIDTH = 0.24


def parse_args():
    argv = sys.argv
    idx = argv.index("--") + 1 if "--" in argv else len(argv)
    if idx >= len(argv):
        raise ValueError("Missing job_config.json path after '--'")
    return argv[idx]


def parse_srt(path):
    """Parse SRT file into list of (frame_start, frame_end, text) tuples."""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        r"\d+\s*\n"
        r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
        r"([\s\S]*?)(?=\n\n|\Z)",
        re.MULTILINE,
    )

    entries = []
    for match in pattern.finditer(content):
        start_ts, end_ts, text = match.group(1), match.group(2), match.group(3).strip()
        entries.append((start_ts, end_ts, text))
    return entries


def ts_to_frame(timestamp, frame_rate):
    """Convert SRT timestamp (HH:MM:SS,mmm) to frame number."""
    h, m, rest = timestamp.split(":")
    s, ms = rest.split(",")
    total_seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return round(total_seconds * frame_rate)


def build_subtitle_timeline(
    entries,
    frame_rate,
    frame_offset=0,
    fade_frames=DEFAULT_FADE_FRAMES,
    max_hold_seconds=DEFAULT_MAX_HOLD_SECONDS,
    rise_frames=DEFAULT_RISE_FRAMES,
):
    """Turn SRT entries into non-overlapping text strip specs.

    Entries are word-level and often back-to-back, so each one is held on screen
    until the next starts — capped by max_hold_seconds so a word doesn't linger
    through a long pause. Fades are applied only where there is a real gap:
    fading in/out between adjacent words reads as flicker.

    frame_offset shifts every entry so the SRT (timed from the start of the
    narration audio) lines up with where the voice strip sits on the timeline.
    """
    items = []
    for start_ts, end_ts, text in entries:
        if not text:
            continue
        start = ts_to_frame(start_ts, frame_rate) + frame_offset
        end = max(ts_to_frame(end_ts, frame_rate) + frame_offset, start)
        if items and start <= items[-1]["start"]:
            # Two entries landing on the same frame — merge so no word is lost.
            items[-1]["own_end"] = max(items[-1]["own_end"], end)
            items[-1]["text"] = f"{items[-1]['text']} {text}"
            continue
        items.append({"start": start, "own_end": end, "text": text})

    max_hold = max(0, round(max_hold_seconds * frame_rate))

    specs = []
    for i, item in enumerate(items):
        end = item["own_end"] + max_hold
        if i + 1 < len(items):
            end = min(end, items[i + 1]["start"])
        specs.append({
            "start": item["start"],
            "end": max(end, item["start"] + 1),
            "text": item["text"],
        })

    for i, spec in enumerate(specs):
        duration = spec["end"] - spec["start"]
        fade = min(fade_frames, duration // 3)
        preceded_by_gap = i == 0 or specs[i - 1]["end"] < spec["start"]
        followed_by_gap = i == len(specs) - 1 or spec["end"] < specs[i + 1]["start"]
        spec["fade_in"] = fade if preceded_by_gap else 0
        spec["fade_out"] = fade if followed_by_gap else 0
        # Capped at duration - 1 so the word actually reaches its resting
        # position before the strip ends.
        spec["rise"] = min(rise_frames, max(0, duration - 1))

    return specs


def resolve_font_path(configured=None, candidates=DEFAULT_FONT_CANDIDATES, exists=os.path.exists):
    """First font file that actually exists: configured path, then the defaults.

    Returns None when none of them is present, in which case the caller leaves
    Blender's built-in font in place. A missing font file is a styling problem,
    not a reason to fail a render that is otherwise complete.
    """
    for path in ([configured] if configured else []) + list(candidates):
        if path and exists(path):
            return path
    return None


def _parse_color(value, fallback):
    """Accept [r, g, b] or [r, g, b, a] from JSON as an RGBA tuple."""
    if value is None:
        return fallback
    channels = [float(c) for c in value]
    if len(channels) == 3:
        channels.append(1.0)
    if len(channels) != 4:
        raise ValueError(f"Subtitle color needs 3 or 4 channels, got {len(channels)}")
    return tuple(channels)


def resolve_subtitle_style(config=None, exists=os.path.exists):
    """Build the text-strip style dict from the template's `subtitles` block.

    Pure — no bpy — so the whole resolution (font fallback included) is
    testable outside Blender.
    """
    config = config or {}
    return {
        "font_path": resolve_font_path(config.get("font_path"), exists=exists),
        "font_size": config.get("font_size", DEFAULT_FONT_SIZE),
        # Clamped to the frame: a value outside 0..1 parks every word off-screen,
        # which renders as subtitles silently missing rather than as an error.
        "y_position": min(1.0, max(0.0, float(config.get("y_position", DEFAULT_SUBTITLE_Y)))),
        "color": _parse_color(config.get("color"), DEFAULT_TEXT_COLOR),
        "use_outline": config.get("use_outline", True),
        "outline_color": _parse_color(config.get("outline_color"), DEFAULT_OUTLINE_COLOR),
        # Blender clamps outline_width to 0..1; clamping here keeps a bad
        # template value from silently rendering as something else.
        "outline_width": min(1.0, max(0.0, float(config.get("outline_width", DEFAULT_OUTLINE_WIDTH)))),
    }


def fit_font_size(text, font_size, max_width, measure, min_size=MIN_AUTOFIT_FONT_SIZE):
    """Largest size <= font_size at which `text` fits within max_width.

    `font_size` is a ceiling, not a fixed value: short words — the vast majority —
    render at exactly that size, and only the ones that would overrun the frame
    are scaled down. Without this, raising the body size clips the long words
    instead of shrinking them, and the clipping is silent: Blender does not wrap
    a single word and reports nothing.

    Measured on a real 178-word narration at 1080px wide: at size 190 only 13
    words (7%) need fitting, and the longest one ("procedimento,") lands at 133.

    Pure — `measure(text, size) -> width` is injected, so the rule is testable
    without Blender. Returns font_size unchanged when it is None (meaning "leave
    Blender's own size alone") or when there is nothing to measure.
    """
    if not font_size or not text:
        return font_size
    width = measure(text, font_size)
    if width <= max_width:
        return font_size
    return max(min_size, int(font_size * max_width / width))


def make_text_measurer(font_path):
    """Width-measuring callable backed by Blender's own font module.

    Returns None when the font could not be loaded — the caller then skips
    auto-fit rather than measuring with a different typeface than it renders,
    which would scale words by the wrong ratio.

    blf is the same rasteriser the VSE text strip uses. Verified against a real
    render: blf reports 1046px for "procedimento," at size 140 where the rendered
    bounding box (outline included) is 1037px, so it errs slightly wide — the
    safe direction for a fits-on-screen test.
    """
    if not font_path:
        return None
    import blf

    font_id = blf.load(font_path)
    if font_id == -1:
        return None

    def measure(text, size):
        blf.size(font_id, size)
        return blf.dimensions(font_id, text)[0]

    return measure


def load_subtitle_font(font_path):
    """Load the font datablock once so every strip shares it."""
    if not font_path:
        return None
    import bpy

    return bpy.data.fonts.load(font_path, check_existing=True)


def apply_text_style(strip, style, font=None, font_size=None):
    """Apply typeface, fill colour and outline to one text strip.

    `font_size` overrides the style's size for this strip alone — that is how the
    per-word auto-fit gets applied without mutating the shared style dict.

    The outline properties require Blender 4.2+ (the version pinned in the
    Dockerfile); on older builds this raises rather than silently dropping the
    outline, which is what keeps the subtitles readable over bright video.
    """
    if font is not None:
        strip.font = font
    size = font_size if font_size is not None else style["font_size"]
    if size:
        strip.font_size = size
    strip.color = style["color"]
    strip.use_outline = style["use_outline"]
    strip.outline_color = style["outline_color"]
    strip.outline_width = style["outline_width"]


def setup_vse(scene):
    if not scene.sequence_editor:
        scene.sequence_editor_create()
    return scene.sequence_editor


def check_movie_strip(strip, path, min_frames=MIN_MOVIE_FRAMES):
    """Raise if the movie strip carries no decodable video.

    Pure — takes anything with a `frame_duration`, so it is testable without
    Blender. See MIN_MOVIE_FRAMES for why this check has to exist at all.
    """
    if strip.frame_duration < min_frames:
        raise ValueError(
            f"Background video has no decodable frames: {path} "
            f"(frame_duration={strip.frame_duration}). The file is a "
            f"placeholder or is corrupt — replace the asset before rendering."
        )
    return strip


def add_movie_strip(vse, path, channel, frame_start, min_frames=MIN_MOVIE_FRAMES):
    strip = vse.sequences.new_movie(
        name=os.path.basename(path),
        filepath=path,
        channel=channel,
        frame_start=frame_start,
    )
    return check_movie_strip(strip, path, min_frames)


def add_sound_strip(vse, path, channel, frame_start):
    return vse.sequences.new_sound(
        name=os.path.basename(path),
        filepath=path,
        channel=channel,
        frame_start=frame_start,
    )


def intro_frames(default_start, hook_end=None, tail_frames=0, hook_muted=False, intro_start=1):
    """`(card_end, narration_start)` — where the card leaves and the story begins.

    Three shapes, and the two numbers only diverge in the third:

    - **No hook audio.** The card (if any) covers the template's fixed intro and
      the narration starts there, exactly as every render did before the intro
      existed.
    - **Hook played.** The card holds until the hook finishes speaking, plus a
      beat, and the narration waits for it. `max` and not a plain sum: a hook
      shorter than the template's intro must not *shorten* the opening, so
      `speech_start` stays the floor.
    - **Hook muted** — the part's own narration opens with that same sentence, so
      the file is there only to say how long it takes to say it. The narration
      starts with the video and the card holds for exactly the length of the
      phrase: no floor and **no tail**.

    The tail is what separates two different audio files, and a muted hook has
    only one — the narration runs straight through. Charging it anyway costs the
    story's first word its subtitle: measured on a real narration, the spoken
    hook ends at 2.560s and the next word starts at 2.759s, so a 0.3s tail holds
    the card past it and the word gets hidden with the hook's own. Same reason
    there is no floor: the card is tracking a sentence that is already being
    spoken, and holding it longer would cover the story's second one.

    Pure — frame numbers in, frame numbers out.
    """
    if not hook_end:
        return default_start, default_start

    if hook_muted:
        return hook_end, intro_start
    hook_out = hook_end + tail_frames
    return max(default_start, hook_out), max(default_start, hook_out)


def drop_specs_before(specs, frame):
    """Subtitle specs that start at or after `frame`.

    While the card is up it *is* the text: the card and the word-level subtitle
    sit at the same height, so letting the narration's first words through would
    stack the same sentence on top of itself. Only used when the hook is muted —
    that is the only case where narration and card overlap in time.

    Pure.
    """
    if not frame:
        return list(specs)
    return [spec for spec in specs if spec["start"] >= frame]


def card_offset_y(y_position, frame_height):
    """Vertical offset in pixels for the card image strip.

    Blender centres an image strip in the frame, and `transform.offset_y` moves
    it from there in pixels (positive = up). `y_position` is a fraction of frame
    height with the same meaning as the subtitles' — 0 = bottom, 0.5 = dead
    centre — so both are read off the same scale, and it addresses the card's
    own centre, so a two-line card and a five-line one sit at the same place.

    Clamped to 0..1: an off-frame value renders as a card silently missing.

    Pure — no bpy.
    """
    position = min(1.0, max(0.0, float(y_position)))
    return round((position - 0.5) * frame_height)


def add_image_strip(vse, path, channel, frame_start, frame_end):
    """Image strip covering [frame_start, frame_end), composited over the video.

    `fit_method="ORIGINAL"` keeps the PNG at its own pixel size: the card is
    authored at exactly the frame width (1080) with transparent margins, so any
    fitting would only resample it. `blend_type` must be set explicitly —
    a strip added through the API does not inherit the ALPHA_OVER the UI gives
    it, and without it the card's transparent frame renders as a black box over
    the video.
    """
    strip = vse.sequences.new_image(
        name=os.path.basename(path),
        filepath=path,
        channel=channel,
        frame_start=frame_start,
        fit_method="ORIGINAL",
    )
    strip.frame_final_duration = max(1, frame_end - frame_start)
    strip.blend_type = "ALPHA_OVER"
    strip.blend_alpha = 1.0
    return strip


def add_card(scene, vse, path, channel, frame_start, frame_end, config=None, frame_height=None):
    """The comment card, on screen for the whole intro.

    Up at full opacity on its first frame — there is no fade in. Every video
    opens on the same card in the same place, and the same short ramp on top of
    that is a frame-level signature the platform can read as the same opening
    reposted; the card landing already there breaks the pattern, and it puts the
    hook on screen a beat earlier besides.

    `fade_frames` therefore measures the fade *out* only. It stays capped at a
    third of the strip for the same reason the subtitles' is: a fade longer than
    the strip would insert inverted keyframes and the card would end up
    half-transparent for its whole life.
    """
    config = config or {}
    strip = add_image_strip(vse, path, channel, frame_start, frame_end)

    if frame_height:
        strip.transform.offset_y = card_offset_y(
            config.get("y_position", DEFAULT_CARD_Y), frame_height
        )

    duration = strip.frame_final_duration
    fade = min(int(config.get("fade_frames", DEFAULT_CARD_FADE_FRAMES)), duration // 3)
    if fade > 0:
        strip.blend_alpha = 1.0
        strip.keyframe_insert("blend_alpha", frame=frame_start + duration - fade)
        strip.blend_alpha = 0.0
        strip.keyframe_insert("blend_alpha", frame=frame_start + duration)
        _set_easing(scene, strip, "blend_alpha", 0, "SINE", "EASE_OUT")

    return strip


def content_end_frame(strips, bed_channels, fallback):
    """Last frame carrying content, ignoring the background beds.

    Music and the background video are *beds*: each is however long its asset
    happens to be, and neither says anything about when the story ends — only
    the narration and its subtitles do. Counting the beds makes the video as
    long as the longest asset: measured, a 90s background under a 68s narration
    rendered 22s of dead air after the last word had already left the screen.

    Pure — takes any objects with `channel` and `frame_final_end`, so the rule is
    testable without Blender.

    A bed *shorter* than the narration is the mirror case, and the timeline still
    must not shrink to it — the fix is to make the bed longer, which is what
    `background_repeats` does before the render.
    """
    content = [s for s in strips if s.channel not in bed_channels]
    if not content:
        return fallback
    return max(s.frame_final_end for s in content)


def end_padding_frames(config, frame_rate, default_seconds=DEFAULT_END_PADDING_SECONDS):
    """`narration.tail_seconds` in frames — the silence held after the last word.

    Configured in seconds, like the music fade, because it is a length of
    *listening*, not a frame count: the same number has to mean the same pause at
    any `frame_rate`.

    Ends the video on the narration and nothing else, which is the rule, but not
    on the very frame the narration stops: the last word needs somewhere to
    finish. Measured on a real render, `scene.frame_end` flush against the voice
    strip clips the closing consonant — the strip end is already rounded to the
    frame, and the encoder's last audio packet lands on the cut.

    Pure — frame arithmetic, tested without Blender. Negative values are floored
    at 0: a template must not be able to make the video end *before* the
    narration does, which would cut a whole word instead of a syllable.
    """
    seconds = float((config or {}).get("tail_seconds", default_seconds))
    return max(0, round(seconds * frame_rate))


def background_repeats(clip_frames, first_start, needed_end, max_repeats=MAX_BACKGROUND_REPEATS):
    """Start frames for the extra background copies needed to reach `needed_end`.

    The background is a bed: it is however long its file happens to be, and the
    narration decides where the video ends. When the bed runs out first the tail
    renders **black** — no error, no warning, a video that looks finished and is
    not. Measured: a 45s clip under a 71s narration produced 26s of black with
    subtitles still popping over it.

    That used to be filed as an asset problem, and it was, while every render
    shared one long hand-picked file. With backgrounds now drawn from a library
    of clips, a clip shorter than the narration is the normal case, not a
    mistake — so the timeline covers it by repeating the clip.

    Pure — takes frame numbers, so the arithmetic is testable without Blender.
    `max_repeats` bounds the pathological case (a two-frame file would otherwise
    ask for thousands of strips); past it the tail goes black as before.
    """
    if clip_frames < 1:
        return []

    # Coerced because the caller reads these off a strip, and Blender's RNA hands
    # back `frame_start`/`frame_duration` as floats while `new_movie` accepts only
    # ints — the repeats then died with a TypeError mid-assembly. It never fired in
    # production because a clip longer than the narration asks for no repeats at
    # all, so the float never reached the API.
    clip_frames = int(clip_frames)
    next_start = int(round(first_start)) + clip_frames

    starts = []
    while next_start <= needed_end and len(starts) < max_repeats:
        starts.append(next_start)
        next_start += clip_frames
    return starts


def extend_background(vse, path, channel, strip, needed_end):
    """Lay extra copies of the background clip until `needed_end` is covered."""
    starts = background_repeats(strip.frame_duration, strip.frame_start, needed_end)
    for start in starts:
        add_movie_strip(vse, path, channel, start)
    return len(starts)


def music_fade_frames(config, frame_rate, default_seconds=DEFAULT_MUSIC_FADE_SECONDS):
    """`music.fade_out_seconds` converted to frames.

    Configured in seconds because a fade is a musical length, not a frame count:
    the same number has to mean the same ending at any `frame_rate`.
    """
    seconds = float((config or {}).get("fade_out_seconds", default_seconds))
    return round(seconds * frame_rate)


def music_fade_start(last_frame, fade_frames, first_frame=1):
    """Frame where the music starts fading out, counted back from the end.

    Where the video ends is decided by the narration (`content_end_frame`), so a
    fade anchored to a *frame number* in the template only lines up with the
    length that template happened to be written for. `timing.music_fade_out` was
    frame 840 against a `frame_end` of 900: a 2-second fade for a 30s draft, but
    on a real 60s render the bed started dying halfway through the story and
    spent the whole second half in decline. Under 28s it is worse: the keyframes
    go in out of order (1, 840, `frame_end`), Blender sorts them by frame, and the
    curve reads 0.2 → 0.0 → 0.2 — measured, the bed fell from -26 dBFS at 0s to
    -73 dBFS at the end of a 23s video. That key is no longer read.

    Returns None when there is nothing to fade — a non-positive `fade_frames`
    (how a template turns the fade off) or a timeline with no room for one.
    Clamped to `first_frame`: a video shorter than the fade ramps throughout
    instead of getting keyframes before its own start.

    Pure — frame arithmetic, tested without Blender.
    """
    if fade_frames <= 0 or last_frame <= first_frame:
        return None
    return max(first_frame, last_frame - fade_frames)


def apply_volume_fade(strip, start_frame, fade_start_frame, end_frame, start_volume):
    """Hold start_volume until fade_start_frame, then ramp to 0 at end_frame."""
    strip.volume = start_volume
    strip.keyframe_insert("volume", frame=start_frame)
    strip.volume = start_volume
    strip.keyframe_insert("volume", frame=fade_start_frame)
    strip.volume = 0.0
    strip.keyframe_insert("volume", frame=end_frame)


def _set_easing(scene, strip, data_path, index, interpolation, easing):
    """Pin keyframe interpolation for one strip property.

    Newly inserted keyframes inherit the user's preferences, so a dev with
    non-default prefs would get a different animation than the container.
    """
    action = scene.animation_data.action if scene.animation_data else None
    if action is None:
        return
    full_path = f'sequence_editor.sequences_all["{strip.name}"].{data_path}'
    for curve in action.fcurves:
        if curve.data_path == full_path and curve.array_index == index:
            for point in curve.keyframe_points:
                point.interpolation = interpolation
                point.easing = easing


def import_subtitles(
    scene,
    vse,
    srt_path,
    channel,
    frame_rate,
    frame_offset=0,
    fade_frames=DEFAULT_FADE_FRAMES,
    max_hold_seconds=DEFAULT_MAX_HOLD_SECONDS,
    rise_frames=DEFAULT_RISE_FRAMES,
    rise_offset=DEFAULT_RISE_OFFSET,
    style=None,
    frame_width=None,
    hide_before=0,
):
    specs = drop_specs_before(
        build_subtitle_timeline(
            parse_srt(srt_path),
            frame_rate,
            frame_offset,
            fade_frames,
            max_hold_seconds,
            rise_frames,
        ),
        hide_before,
    )

    style = style or resolve_subtitle_style()
    # Loaded once, outside the loop: one datablock shared by every word strip.
    font = load_subtitle_font(style["font_path"])
    # Same reason — the measurer loads the font into blf once for all words.
    measure = make_text_measurer(style["font_path"])
    max_text_width = (
        frame_width * (1 - 2 * SUBTITLE_SIDE_MARGIN) if frame_width else None
    )

    for i, spec in enumerate(specs):
        strip = vse.sequences.new_effect(
            name=f"sub_{i:04d}",
            type="TEXT",
            channel=channel,
            frame_start=spec["start"],
            frame_end=spec["end"],
        )
        subtitle_y = style["y_position"]
        strip.text = spec["text"]
        strip.align_x = "CENTER"
        # CENTER, not BOTTOM: anchoring the text's own middle is what makes
        # y_position mean the same thing for a tall word and a short one.
        strip.align_y = "CENTER"
        strip.location[1] = subtitle_y
        strip.blend_alpha = 1.0
        fitted = (
            fit_font_size(spec["text"], style["font_size"], max_text_width, measure)
            if measure and max_text_width
            else None
        )
        apply_text_style(strip, style, font, font_size=fitted)

        if spec["rise"] and rise_offset:
            strip.location[1] = subtitle_y - rise_offset
            strip.keyframe_insert("location", index=1, frame=spec["start"])
            strip.location[1] = subtitle_y
            strip.keyframe_insert("location", index=1, frame=spec["start"] + spec["rise"])
            # Ease out: fast off the mark, settling into place — reads as a pop
            # rather than a drift.
            _set_easing(scene, strip, "location", 1, "SINE", "EASE_OUT")

        if spec["fade_in"]:
            strip.blend_alpha = 0.0
            strip.keyframe_insert("blend_alpha", frame=spec["start"])
            strip.blend_alpha = 1.0
            strip.keyframe_insert("blend_alpha", frame=spec["start"] + spec["fade_in"])

        if spec["fade_out"]:
            strip.blend_alpha = 1.0
            strip.keyframe_insert("blend_alpha", frame=spec["end"] - spec["fade_out"])
            strip.blend_alpha = 0.0
            strip.keyframe_insert("blend_alpha", frame=spec["end"])

    return len(specs)


def apply_payload(scene, vse, payload, assets):
    """Fase 2 dispatcher for the format in `docs/edicao_declarativa.md` —
    the replacement for `main()`'s hardcoded six-channel assembly.

    `payload` is what `timeline/payload.py`'s `build_payload` produces. Each
    clip's `type` picks the same bpy helper the legacy `main()` already
    calls (`add_movie_strip`, `add_sound_strip`, `add_card`,
    `import_subtitles`), so a payload built from `templates_v2/default.yaml`
    produces the same strips `main()` does from the current `template.json`.
    Not wired into `main()` or `worker.py` yet — see
    `docs/edicao_declarativa.md`'s "Decisões em aberto" for the
    `template.json` v1 coexistence question that has to be settled first.

    **Two passes, `content` then `bed`** — this is not an arbitrary choice,
    it is what makes the result match `main()`. `content` clips are created
    first; `scene.frame_end` is then *re-derived from those real strips*
    with `content_end_frame`, the same rule `main()` uses, instead of
    trusting `payload["timeline_end"]`. Only then are `bed` clips created,
    with their loop/fade geometry computed against this corrected value
    rather than `payload`'s advisory `repeats`/`fade_start`. See
    `timeline/payload.py`'s module docstring for why: a real-Blender
    verification run measured the Fase 1 resolver's `timeline_end` landing
    10 frames early, because it approximates `subtitles` duration as the
    voice's, and a real SRT's last word can hold past that. Every other
    strip matched exactly; this is the one number that cannot be known
    without either parsing the SRT in the (deliberately `bpy`-free) resolver
    or re-deriving it here, against what was actually created. This does.

    `assets` maps each clip's `source` name to a downloaded file path —
    `job_config["assets"]` already has this shape, just keyed by the new
    format's input names (e.g. `"background"` instead of `"video"`).
    """
    frame_rate = payload["frame_rate"]
    scene.frame_start = 1
    # See main()'s comment on fps_base: must be reset to match frame_rate.
    scene.render.fps = frame_rate
    scene.render.fps_base = 1.0
    scene.render.resolution_x = payload["canvas"]["width"]
    scene.render.resolution_y = payload["canvas"]["height"]

    dispatch = {
        "video": _apply_video_clip,
        "audio": _apply_audio_clip,
        "image": _apply_image_clip,
        "subtitles": _apply_subtitles_clip,
    }
    content_clips = [c for c in payload["clips"] if c["role"] == "content"]
    bed_clips = [c for c in payload["clips"] if c["role"] == "bed"]

    for clip in content_clips:
        _dispatch_clip(dispatch, scene, vse, clip, assets, frame_rate)

    bed_channels = {c["channel"] for c in bed_clips}
    fallback = payload["timeline_end"] - payload["tail_frames"]
    content_end = content_end_frame(vse.sequences_all, bed_channels, fallback)
    scene.frame_end = content_end + payload["tail_frames"]

    for clip in bed_clips:
        _dispatch_clip(dispatch, scene, vse, clip, assets, frame_rate)


def _dispatch_clip(dispatch, scene, vse, clip, assets, frame_rate):
    handler = dispatch.get(clip["type"])
    if handler is None:
        raise ValueError(f"unknown clip type {clip['type']!r}")
    handler(scene, vse, clip, assets, frame_rate)


def _apply_video_clip(scene, vse, clip, assets, frame_rate):
    path = assets[clip["source"]]
    min_frames = clip.get("min_frames", MIN_MOVIE_FRAMES)
    strip = add_movie_strip(vse, path, clip["channel"], clip["frame_start"], min_frames)
    if clip.get("loop"):
        # Recomputed against the real scene.frame_end (set from the real
        # content strips just before bed clips run) rather than trusting
        # `repeats` — see apply_payload's docstring.
        extend_background(vse, path, clip["channel"], strip, scene.frame_end)


def _apply_audio_clip(scene, vse, clip, assets, frame_rate):
    path = assets[clip["source"]]
    strip = add_sound_strip(vse, path, clip["channel"], clip["frame_start"])
    strip.volume = clip.get("volume", 1.0)
    # Muted, not skipped: same reason as the legacy hook — the strip still
    # carries the duration something else (the card) was measured against.
    strip.mute = clip.get("muted", False)
    if clip.get("fade_duration_frames") is not None:
        # Recomputed against the real scene.frame_end, same reason as the
        # background loop above — see apply_payload's docstring.
        fade_start = music_fade_start(scene.frame_end, clip["fade_duration_frames"], clip["frame_start"])
        if fade_start is not None:
            apply_volume_fade(
                strip,
                start_frame=clip["frame_start"],
                fade_start_frame=fade_start,
                end_frame=scene.frame_end,
                start_volume=clip.get("volume", 1.0),
            )


def _apply_image_clip(scene, vse, clip, assets, frame_rate):
    path = assets[clip["source"]]
    config = {"y_position": clip.get("y_position", DEFAULT_CARD_Y)}
    if "fade_frames" in clip:
        config["fade_frames"] = clip["fade_frames"]
    add_card(
        scene, vse, path, clip["channel"],
        clip["frame_start"], clip["frame_end"], config,
        scene.render.resolution_y,
    )


def _apply_subtitles_clip(scene, vse, clip, assets, frame_rate):
    path = assets[clip["source"]]
    style = resolve_subtitle_style(clip.get("style"))
    import_subtitles(
        scene, vse, path, clip["channel"], frame_rate,
        frame_offset=clip["frame_start"],
        fade_frames=clip.get("fade_frames", DEFAULT_FADE_FRAMES),
        max_hold_seconds=clip.get("max_hold_seconds", DEFAULT_MAX_HOLD_SECONDS),
        rise_frames=clip.get("rise_frames", DEFAULT_RISE_FRAMES),
        rise_offset=clip.get("rise_offset", DEFAULT_RISE_OFFSET),
        style=style,
        frame_width=scene.render.resolution_x,
        hide_before=clip.get("hide_before", 0),
    )


def main():
    import bpy

    config_path = parse_args()
    with open(config_path) as f:
        config = json.load(f)

    timing = config["timing"]
    assets = config["assets"]
    channels = timing["channels"]
    t = timing["timing"]
    frame_rate = timing["frame_rate"]

    scene = bpy.context.scene
    scene.frame_start = 1
    # fps_base must be reset: a template authored at e.g. 6/0.1 keeps its base,
    # and the scene would run at frame_rate/fps_base — 10x off every timing here.
    scene.render.fps = frame_rate
    scene.render.fps_base = 1.0

    vse = setup_vse(scene)

    # Deselect all existing strips to avoid affecting new additions
    for strip in vse.sequences_all:
        strip.select = False

    background = add_movie_strip(vse, assets["video"], channels["video"], t["intro_start"] + 1)

    music_strip = add_sound_strip(vse, assets["music"], channels["music"], t["intro_start"] + 1)
    music_strip.volume = DEFAULT_MUSIC_VOLUME

    # Intro: the hook is read aloud over the comment card, and only then does the
    # narration start. Both assets are optional — a run whose hook TTS failed, or
    # whose card could not be composed, still renders exactly as it did before.
    card_config = timing.get("card", {})
    intro_start = t["intro_start"] + 1

    # A part whose own narration opens with the hook keeps the file only as a
    # measure of how long that sentence takes: playing it too would say the same
    # phrase twice in a row.
    hook_muted = bool(config.get("hook_muted"))

    hook_strip = None
    if assets.get("hook"):
        hook_strip = add_sound_strip(
            vse, assets["hook"], channels.get("hook", DEFAULT_HOOK_CHANNEL), intro_start
        )
        hook_strip.volume = 1.0
        # Muted, not removed: the strip is what carries the duration, and leaving
        # it on the timeline keeps the assembled .blend readable — the card's
        # length is visibly tied to something.
        hook_strip.mute = hook_muted

    tail_frames = round(
        float(card_config.get("tail_seconds", DEFAULT_HOOK_TAIL_SECONDS)) * frame_rate
    )
    card_end, speech_start = intro_frames(
        t["speech_start"] + 1,
        hook_strip.frame_final_end if hook_strip else None,
        tail_frames,
        hook_muted=hook_muted,
        intro_start=intro_start,
    )

    if assets.get("card"):
        add_card(
            scene,
            vse,
            assets["card"],
            channels.get("card", DEFAULT_CARD_CHANNEL),
            intro_start,
            card_end,
            card_config,
            scene.render.resolution_y,
        )

    voice_strip = add_sound_strip(vse, assets["voice"], channels["voice"], speech_start)
    voice_strip.volume = 1.0

    # SRT timestamps are relative to the narration audio, so they shift with it.
    subs = timing.get("subtitles", {})
    import_subtitles(
        scene,
        vse,
        assets["subtitles"],
        channels["subtitles"],
        frame_rate,
        frame_offset=speech_start,
        fade_frames=subs.get("fade_frames", DEFAULT_FADE_FRAMES),
        max_hold_seconds=subs.get("max_hold_seconds", DEFAULT_MAX_HOLD_SECONDS),
        rise_frames=subs.get("rise_frames", DEFAULT_RISE_FRAMES),
        rise_offset=subs.get("rise_offset", DEFAULT_RISE_OFFSET),
        style=resolve_subtitle_style(subs),
        frame_width=scene.render.resolution_x,
        # Only ever non-zero with a muted hook: that is the one case where the
        # narration runs while the card is still up, and the two would print the
        # same sentence at the same height.
        hide_before=card_end if hook_muted else 0,
    )

    bed_channels = {channels["music"], channels["video"]}
    # The narration decides where the video ends, plus the breath the last word
    # needs to finish — flush against the voice strip, it gets clipped.
    last_frame = content_end_frame(
        vse.sequences_all, bed_channels, timing["frame_end"]
    ) + end_padding_frames(timing.get("narration"), frame_rate)
    scene.frame_end = last_frame

    # After the length is fixed, never before: the repeats are on a bed channel
    # and so are invisible to content_end_frame either way, but laying them first
    # would make the covering depend on a number that has not been decided yet.
    extend_background(vse, assets["video"], channels["video"], background, last_frame)

    # The only ending the video has: no outro card, no closing beat — the last
    # narrated word is the last frame, with the bed fading under it.
    fade_start = music_fade_start(
        last_frame,
        music_fade_frames(timing.get("music"), frame_rate),
        first_frame=intro_start,
    )
    if fade_start is not None:
        apply_volume_fade(
            music_strip,
            start_frame=intro_start,
            fade_start_frame=fade_start,
            end_frame=last_frame,
            start_volume=DEFAULT_MUSIC_VOLUME,
        )

    # Render output format: MP4/H264/AAC
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.ffmpeg.audio_bitrate = 192
    scene.render.filepath = config["render_output_path"]

    bpy.ops.wm.save_as_mainfile(filepath=config["output_path"])


if __name__ == "__main__":
    main()
