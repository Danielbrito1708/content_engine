"""
Blender edit script — runs inside Blender's Python interpreter.
Usage: blender -b template.blend -P scripts/edit_video.py -- /path/to/job_config.json
"""

import json
import os
import re
import sys


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
    return int(total_seconds * frame_rate)


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


def import_subtitles(scene, vse, srt_path, channel, frame_rate, fade_frames=3):
    entries = parse_srt(srt_path)

    for start_ts, end_ts, text in entries:
        frame_start = ts_to_frame(start_ts, frame_rate)
        frame_end = ts_to_frame(end_ts, frame_rate)

        strip = vse.sequences.new_effect(
            name=f"sub_{frame_start}",
            type="TEXT",
            channel=channel,
            frame_start=frame_start,
            frame_end=frame_end,
        )
        strip.text = text
        strip.align_x = "CENTER"
        strip.align_y = "BOTTOM"
        strip.location[1] = 0.05

        # Fade in
        strip.blend_alpha = 0.0
        strip.keyframe_insert("blend_alpha", frame=frame_start)
        strip.blend_alpha = 1.0
        strip.keyframe_insert("blend_alpha", frame=frame_start + fade_frames)

        # Fade out
        strip.blend_alpha = 1.0
        strip.keyframe_insert("blend_alpha", frame=frame_end - fade_frames)
        strip.blend_alpha = 0.0
        strip.keyframe_insert("blend_alpha", frame=frame_end)


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
    scene.frame_end = timing["frame_end"]
    scene.render.fps = frame_rate

    vse = setup_vse(scene)

    # Deselect all existing strips to avoid affecting new additions
    for strip in vse.sequences_all:
        strip.select = False

    add_movie_strip(vse, assets["video"], channels["video"], t["intro_start"] + 1)

    music_strip = add_sound_strip(vse, assets["music"], channels["music"], t["intro_start"] + 1)
    music_strip.volume = 0.2
    if "music_fade_out" in t:
        apply_volume_fade(
            music_strip,
            start_frame=t["intro_start"] + 1,
            fade_start_frame=t["music_fade_out"],
            end_frame=timing["frame_end"],
            start_volume=0.2,
        )

    voice_strip = add_sound_strip(vse, assets["voice"], channels["voice"], t["speech_start"] + 1)
    voice_strip.volume = 1.0

    import_subtitles(scene, vse, assets["subtitles"], channels["subtitles"], frame_rate)

    # Render output format: MP4/H264/AAC
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.ffmpeg.audio_bitrate = 192
    scene.render.filepath = config["render_output_path"]

    bpy.ops.wm.save_as_mainfile(filepath=config["output_path"])


main()
