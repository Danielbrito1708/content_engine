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

# Vertical position of the subtitle, as a fraction of frame height (0 = bottom).
SUBTITLE_Y = 0.05
# Entrance animation: each word starts this far below SUBTITLE_Y and rises to it
# over rise_frames. Unlike the fade, this applies to every word — it's what makes
# each word read as a distinct "pop" even between back-to-back words.
DEFAULT_RISE_OFFSET = 0.025
DEFAULT_RISE_FRAMES = 4


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


def setup_vse(scene):
    if not scene.sequence_editor:
        scene.sequence_editor_create()
    return scene.sequence_editor


def add_movie_strip(vse, path, channel, frame_start):
    return vse.sequences.new_movie(
        name=os.path.basename(path),
        filepath=path,
        channel=channel,
        frame_start=frame_start,
    )


def add_sound_strip(vse, path, channel, frame_start):
    return vse.sequences.new_sound(
        name=os.path.basename(path),
        filepath=path,
        channel=channel,
        frame_start=frame_start,
    )


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
):
    specs = build_subtitle_timeline(
        parse_srt(srt_path),
        frame_rate,
        frame_offset,
        fade_frames,
        max_hold_seconds,
        rise_frames,
    )

    for i, spec in enumerate(specs):
        strip = vse.sequences.new_effect(
            name=f"sub_{i:04d}",
            type="TEXT",
            channel=channel,
            frame_start=spec["start"],
            frame_end=spec["end"],
        )
        strip.text = spec["text"]
        strip.align_x = "CENTER"
        strip.align_y = "BOTTOM"
        strip.location[1] = SUBTITLE_Y
        strip.blend_alpha = 1.0

        if spec["rise"] and rise_offset:
            strip.location[1] = SUBTITLE_Y - rise_offset
            strip.keyframe_insert("location", index=1, frame=spec["start"])
            strip.location[1] = SUBTITLE_Y
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

    add_movie_strip(vse, assets["video"], channels["video"], t["intro_start"] + 1)

    music_strip = add_sound_strip(vse, assets["music"], channels["music"], t["intro_start"] + 1)
    music_strip.volume = 0.2

    speech_start = t["speech_start"] + 1
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
    )

    # Set frame_end to the last frame where a content strip exists.
    # Music is excluded because its file may be longer than the actual content.
    music_channel = channels["music"]
    content_strips = [s for s in vse.sequences_all if s.channel != music_channel]
    last_frame = max(s.frame_final_end for s in content_strips) if content_strips else timing["frame_end"]
    scene.frame_end = last_frame

    if "music_fade_out" in t:
        apply_volume_fade(
            music_strip,
            start_frame=t["intro_start"] + 1,
            fade_start_frame=t["music_fade_out"],
            end_frame=last_frame,
            start_volume=0.2,
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
