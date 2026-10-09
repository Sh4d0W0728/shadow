#!/usr/bin/env python3
"""Shadow: deterministic media inspection, editing, timed text and Premiere handoff.

Python 3.9+; standard library only. FFmpeg and ffprobe are external dependencies.
Run ``python shadow.py --help`` for commands. No shell command construction.
"""
from __future__ import annotations

import argparse
import hashlib
import ctypes
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import struct
import zlib
from fractions import Fraction
from urllib.parse import quote
import xml.etree.ElementTree as ET

VERSION = "2.1.0"
MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".mts",
    ".m2ts", ".mpg", ".mpeg", ".flv", ".mxf", ".wav", ".mp3", ".m4a",
    ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".wma",
}


class ShadowError(Exception):
    def __init__(self, message, code=2, details=None):
        super().__init__(message)
        self.code, self.details = code, details


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def fail(message, details=None, code=2):
    raise ShadowError(message, code, details)


def run(argv, timeout=120, check=True):
    try:
        result = subprocess.run(
            [str(x) for x in argv], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=timeout, shell=False,
        )
    except FileNotFoundError:
        fail("Executable was not found.", {"executable": str(argv[0])}, 3)
    except subprocess.TimeoutExpired:
        fail("Media command exceeded its timeout.", {"timeout_seconds": timeout, "argv": argv}, 3)
    except OSError as exc:
        fail("Could not run media command.", {"reason": str(exc)}, 3)
    if check and result.returncode:
        fail("Media command failed.", {
            "returncode": result.returncode, "argv": argv,
            "stderr_tail": result.stderr[-6000:],
        }, 3)
    return result


def executable(candidate):
    expanded = os.path.expandvars(os.path.expanduser(str(candidate)))
    path = Path(expanded)
    if path.is_file():
        return str(path.resolve())
    return shutil.which(expanded)


def find_tool(name, explicit=None):
    environment_key = "SHADOW_" + name.upper()
    preferred = explicit or os.environ.get(environment_key)
    if preferred:
        found = executable(preferred)
        if not found:
            fail("Configured media executable does not exist.", {
                "tool": name, "configured_path": preferred,
                "configuration": "CLI option" if explicit else environment_key,
            }, 3)
        return found
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files")]
        roots += [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        candidates = []
        for root in roots:
            for product in ("Topaz Video AI", "Topaz Video"):
                candidates.append(Path(root) / "Topaz Labs LLC" / product / (name + ".exe"))
            candidates.append(Path(root) / "ffmpeg" / "bin" / (name + ".exe"))
        candidates.append(Path(r"C:\ffmpeg\bin") / (name + ".exe"))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
    fail("Required media executable was not found.", {
        "tool": name,
        "remedy": f"Set {environment_key}, pass --{name}, or add the executable to PATH. No installation was attempted.",
    }, 3)


def media_path(value, base=None):
    if not isinstance(value, str) or not value.strip():
        fail("A media source must be a nonempty path string.")
    path = Path(os.path.expanduser(value))
    if not path.is_absolute() and base is not None:
        path = base / path
    path = path.resolve()
    if not path.is_file():
        fail("Media source does not exist or is not a file.", {"path": str(path)})
    return path


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def output_guard(path, sources, force=False, suffix=None):
    path = Path(path).resolve()
    if suffix is not None and path.suffix.lower() not in suffix:
        fail("Unsupported output extension.", {"path": str(path), "allowed": list(suffix)})
    same_source = path_key(path) in {path_key(s) for s in sources}
    if path.exists() and not same_source:
        for source in sources:
            try:
                if os.path.samefile(path, source):
                    same_source = True
                    break
            except OSError:
                pass
    if same_source:
        fail("An output cannot overwrite a source or the timeline file, even with --force.", {"path": str(path)})
    if path.exists() and (not path.is_file() or not force):
        fail("Output already exists. Use --force only to replace generated outputs.", {"path": str(path)})
    return path


def atomic_write(path, text, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".shadow-write-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        if path.exists() and not force:
            fail("Output appeared while writing; replacement was refused.", {"path": str(path)})
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def save_json(path, data, force=False):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", force)


def as_float(value, field, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail("Expected a finite number.", {"field": field, "value": repr(value)})
    result = float(value)
    if minimum is not None and result < minimum:
        fail("Number is below the allowed minimum.", {"field": field, "minimum": minimum, "value": value})
    return result


def parse_fps(value, field="project.fps"):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d*(?:/[1-9]\d*)?", value):
        fail("FPS must be an exact positive rational string, e.g. '25' or '30000/1001'.", {"field": field, "value": value})
    fps = Fraction(value)
    if not Fraction(1, 1) <= fps <= Fraction(240, 1):
        fail("FPS must be between 1 and 240.", {"field": field, "value": value})
    return fps


def fraction_string(value):
    return f"{value.numerator}/{value.denominator}"


def probe_fraction(value):
    try:
        result = Fraction(str(value))
        return result if result > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def number_or_none(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def duration_of(stream, fallback=None):
    duration = number_or_none(stream.get("duration"))
    if duration is not None and duration > 0:
        return duration
    duration_ts = number_or_none(stream.get("duration_ts"))
    time_base = probe_fraction(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return duration_ts * float(time_base)
    return fallback


def probe(path, ffprobe):
    result = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("ffprobe returned invalid JSON.", {"path": str(path)}, 3)
    streams = raw.get("streams", [])
    container = raw.get("format", {})
    format_duration = number_or_none(container.get("duration"))
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    summary = {
        "path": str(path), "size_bytes": path.stat().st_size,
        "format": container.get("format_name"), "duration_seconds": format_duration,
        "video_streams": [], "audio_streams": [],
    }
    for s in videos:
        summary["video_streams"].append({
            "index": s.get("index"), "codec": s.get("codec_name"),
            "width": s.get("width"), "height": s.get("height"),
            "avg_frame_rate": s.get("avg_frame_rate"), "r_frame_rate": s.get("r_frame_rate"),
            "duration_seconds": duration_of(s, format_duration), "start_seconds": number_or_none(s.get("start_time")),
            "pixel_format": s.get("pix_fmt"), "frame_count": s.get("nb_frames"),
            "field_order": s.get("field_order"),
            "sample_aspect_ratio": s.get("sample_aspect_ratio"),
            "rotation_degrees": next((x.get("rotation") for x in s.get("side_data_list", []) if "rotation" in x), 0),
        })
    for s in audios:
        summary["audio_streams"].append({
            "index": s.get("index"), "codec": s.get("codec_name"),
            "sample_rate": int(s.get("sample_rate") or 0), "channels": s.get("channels"),
            "channel_layout": s.get("channel_layout"),
            "duration_seconds": duration_of(s, format_duration), "start_seconds": number_or_none(s.get("start_time")),
        })
    if not videos and not audios:
        fail("No usable video or audio streams were found.", {"path": str(path)}, 3)
    return summary


def round_frames(seconds, fps):
    # Round half up; avoid the surprising even-frame rule of Python round().
    value = Fraction(str(seconds)) * fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def check_keys(data, allowed, field):
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        fail("Unknown timeline fields were found; they would otherwise be ignored.", {"field": field, "unknown_fields": unknown})


def load_timeline(value, ffprobe):
    timeline_path = media_path(value)
    try:
        def reject_constant(value):
            fail("Timeline JSON cannot contain NaN or Infinity.", {"constant": value})
        data = json.loads(timeline_path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail("Timeline must be a UTF-8 JSON file.", {"path": str(timeline_path), "reason": str(exc)})
    if not isinstance(data, dict):
        fail("Timeline must be a JSON object.")
    schema = data.get("schema_version")
    if type(schema) is not int or schema not in (1, 2):
        fail("Supported timeline schema_version is 1 or 2.")
    check_keys(data, {"schema_version", "project", "clips", "music", "title", "identity"} | ({"titles", "subtitles"} if schema == 2 else set()), "timeline")
    project = data.get("project")
    if not isinstance(project, dict):
        fail("project must be an object.")
    check_keys(project, {"name", "width", "height", "fps", "sample_rate"}, "project")
    if not isinstance(project.get("name"), str) or not project["name"].strip():
        fail("project.name must be a nonempty string.")
    for dimension in ("width", "height"):
        value = project.get(dimension)
        if type(value) is not int or value < 16 or value > 8192 or value % 2:
            fail("Project dimensions must be even integers from 16 to 8192.", {"field": "project." + dimension, "value": value})
    fps = parse_fps(project.get("fps"))
    sample_rate = project.get("sample_rate", 48000)
    if type(sample_rate) is not int or sample_rate not in (44100, 48000, 96000):
        fail("project.sample_rate must be 44100, 48000, or 96000.")
    project = dict(project, fps=fraction_string(fps), sample_rate=sample_rate)
    clips = data.get("clips")
    if not isinstance(clips, list) or not clips:
        fail("clips must be a nonempty array.")
    if len(clips) > 10000:
        fail("A timeline is limited to 10000 clips.")
    cache, normalized, warnings = {}, [], []
    cursor = 0
    for index, clip in enumerate(clips):
        prefix = f"clips[{index}]"
        if not isinstance(clip, dict):
            fail("Each clip must be an object.", {"field": prefix})
        check_keys(clip, {"source", "in", "out", "label", "audio"} | ({"kind", "transition_in", "gain_db"} if schema == 2 else set()), prefix)
        kind = clip.get("kind", "video")
        if kind not in ("video", "image"):
            fail("Clip kind must be video or image.", {"field": prefix})
        path = media_path(clip.get("source"), timeline_path.parent)
        if path_key(path) == path_key(timeline_path):
            fail("The timeline JSON cannot itself be used as media.")
        start = as_float(clip.get("in"), prefix + ".in", 0)
        end = as_float(clip.get("out"), prefix + ".out", 0)
        if end <= start:
            fail("Clip out must be greater than clip in.", {"field": prefix})
        audio = clip.get("audio", kind != "image")
        if not isinstance(audio, bool):
            fail("Clip audio must be true or false.", {"field": prefix + ".audio"})
        gain = as_float(clip.get("gain_db", 0), prefix + ".gain_db")
        if not -96 <= gain <= 12:
            fail("Clip gain_db must be in [-96,12].", {"field": prefix})
        if kind == "image" and (start != 0 or audio or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")):
            fail("Image clips require in=0, audio=false and a supported still-image extension.", {"field": prefix})
        label = clip.get("label", path.stem)
        if not isinstance(label, str):
            fail("Clip label must be a string.", {"field": prefix + ".label"})
        key = path_key(path)
        if key not in cache:
            cache[key] = probe(path, ffprobe)
        media = cache[key]
        if not media["video_streams"]:
            fail("Timeline clips require a video stream; use music for an audio-only source.", {"field": prefix, "source": str(path)})
        video = media["video_streams"][0]
        duration = end if kind == "image" else video["duration_seconds"]
        if duration is None or duration <= 0:
            fail("Video duration could not be determined.", {"source": str(path)})
        if end > duration + 0.001:
            fail("Clip out exceeds the available video duration.", {"field": prefix + ".out", "out_seconds": end, "source_duration_seconds": duration})
        # Absorb only floating-point/metadata noise, never an extra frame of footage.
        end = min(end, duration)
        frame_count = round_frames(end - start, fps)
        if frame_count < 1:
            fail("Clip is shorter than one sequence frame after quantization.", {"field": prefix})
        realized = float(Fraction(frame_count, 1) / fps)
        if abs(realized - (end - start)) > 0.000001:
            warnings.append(f"{prefix}: duration quantized to {frame_count} sequence frames ({realized:.9f} seconds).")
        if len(media["video_streams"]) > 1:
            warnings.append(f"{prefix}: first non-cover video stream is selected; additional video streams are ignored.")
        if len(media["audio_streams"]) > 1:
            warnings.append(f"{prefix}: render selects the first audio stream; XML export refuses ambiguous multiple audio streams.")
        if audio and not media["audio_streams"]:
            warnings.append(f"{prefix}: no source audio; render supplies silence and XML leaves the audio tracks empty here.")
        if video.get("rotation_degrees"):
            warnings.append(f"{prefix}: source has rotation metadata; FFmpeg auto-rotates, and Premiere import requires visual verification.")
        sar = video.get("sample_aspect_ratio")
        if sar not in (None, "N/A", "0:1", "1:1"):
            warnings.append(f"{prefix}: source has non-square pixels; rough-cut render preserves display aspect and uses square output pixels.")
        if video.get("field_order") not in (None, "unknown", "progressive"):
            warnings.append(f"{prefix}: interlaced source; the preview does not deinterlace automatically. Select and verify a deinterlacing method before final delivery.")
        transition = None
        if schema == 2 and clip.get("transition_in") is not None:
            transition = clip["transition_in"]
            if not isinstance(transition, dict):
                fail("transition_in must be an object or null.", {"field": prefix})
            check_keys(transition, {"type", "duration", "audio"}, prefix + ".transition_in")
            if not normalized:
                fail("The first clip cannot have transition_in.")
            if transition.get("type") not in ("dissolve", "fadeblack"):
                fail("Transition type must be dissolve or fadeblack.")
            transition_duration = as_float(transition.get("duration"), prefix + ".transition_in.duration", 0)
            transition_frames = round_frames(transition_duration, fps)
            if transition_frames < 1 or transition_frames >= frame_count or transition_frames >= normalized[-1]["frames"]:
                fail("Transition must have at least one frame and be shorter than both adjacent clips.", {"field": prefix})
            previous_in = normalized[-1].get("transition_in")
            if transition_frames + (previous_in["frames"] if previous_in else 0) >= normalized[-1]["frames"]:
                fail("Adjacent transitions consume the entire intermediate clip.", {"field": prefix})
            audio_transition = transition.get("audio", "crossfade")
            if audio_transition not in ("crossfade", "cut"):
                fail("Transition audio must be crossfade or cut.")
            transition = {"type": transition["type"], "frames": transition_frames, "duration_seconds": float(Fraction(transition_frames, 1) / fps), "audio": audio_transition}
            cursor -= transition_frames
        if kind == "image":
            media = dict(media, duration_seconds=end, is_still=True)
            media["video_streams"] = [dict(media["video_streams"][0], duration_seconds=end, avg_frame_rate=fraction_string(fps), r_frame_rate=fraction_string(fps))]
        normalized.append({
            "source": str(path), "in": start, "out": end, "label": label, "audio": audio,
            "start_frame": cursor, "end_frame": cursor + frame_count, "frames": frame_count,
            "duration_seconds": realized, "media": media, "kind": kind, "gain_db": gain, "transition_in": transition,
        })
        cursor += frame_count
    music = data.get("music")
    if music is not None:
        if not isinstance(music, dict):
            fail("music must be an object or null.")
        check_keys(music, {"source", "in", "gain_db"}, "music")
        path = media_path(music.get("source"), timeline_path.parent)
        start = as_float(music.get("in", 0), "music.in", 0)
        gain = as_float(music.get("gain_db", 0), "music.gain_db")
        if not -96 <= gain <= 12:
            fail("music.gain_db must be between -96 and 12.")
        key = path_key(path)
        if key not in cache:
            cache[key] = probe(path, ffprobe)
        media = cache[key]
        if not media["audio_streams"]:
            fail("Music source has no audio stream.", {"source": str(path)})
        available = media["audio_streams"][0]["duration_seconds"]
        if available is None or start >= available:
            fail("Music in must be before the known audio duration.", {"in_seconds": start, "source_duration_seconds": available})
        total = float(Fraction(cursor, 1) / fps)
        used = min(available - start, total)
        if used < total - 0.001:
            warnings.append("Music ends before the sequence; the remainder is silent. Music is never looped automatically.")
        warnings.append("Source audio and music are added at the requested gain without automatic ducking; audition the mix for clipping and intelligibility.")
        music = {"source": str(path), "in": start, "gain_db": gain, "duration_seconds": used, "media": media}
    result = {
        "timeline_path": str(timeline_path), "schema_version": schema,
        "project": project, "fps_fraction": fps, "clips": normalized,
        "music": music, "total_frames": cursor,
        "duration_seconds": float(Fraction(cursor, 1) / fps), "warnings": warnings,
        "title": data.get("title"), "identity": data.get("identity"),
        "sources": [timeline_path] + [Path(item["path"]) for item in cache.values()],
    }
    result["overlay_events"] = load_overlay_events(data, timeline_path, result) if schema == 2 else []
    return result


def timeline_report(timeline):
    return {
        "ok": True, "schema_version": timeline["schema_version"], "shadow_version": VERSION,
        "timeline": timeline["timeline_path"], "project": timeline["project"],
        "total_frames": timeline["total_frames"], "duration_seconds": timeline["duration_seconds"],
        "clip_count": len(timeline["clips"]), "hard_cuts_only": not any(c.get("transition_in") for c in timeline["clips"]),
        "clips": [{key: value for key, value in clip.items() if key != "media"} for clip in timeline["clips"]],
        "music": ({key: value for key, value in timeline["music"].items() if key != "media"} if timeline["music"] else None),
        "title": timeline["title"], "identity": timeline["identity"],
        "warnings": list(timeline["warnings"]),
        "overlay_events": timeline.get("overlay_events", []),
        "subtitle_source": timeline.get("subtitle_source"),
    }


def caption_seconds(value):
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", value.strip())
    if not match or int(match[2]) >= 60 or int(match[3]) >= 60:
        fail("Invalid subtitle timestamp.", {"timestamp": value})
    return int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3]) + int(match[4]) / (10 ** len(match[4]))


def parse_caption_file(path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        fail("Subtitles must be UTF-8. Convert the encoding before import.", {"source": str(path)})
    events, warnings = [], []
    if path.suffix.lower() == ".srt":
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
            lines = block.splitlines()
            if lines and re.fullmatch(r"\d+", lines[0].strip()):
                lines = lines[1:]
            if not lines:
                continue
            match = re.fullmatch(r"(.+?)\s*-->\s*([^\s]+)(?:\s+.*)?", lines[0])
            if not match or len(lines) < 2:
                fail("Invalid SRT cue; an index, timing line and text are expected.", {"source": str(path), "block": block[:200]})
            cue = "\n".join(lines[1:])
            cue = re.sub(r"</?(?:i|b|u|font)(?:\s[^>]*)?>", "", cue, flags=re.I)
            events.append({"start": caption_seconds(match[1]), "end": caption_seconds(match[2]), "text": cue})
    elif path.suffix.lower() == ".ass":
        active, fields = False, None
        for line in text.splitlines():
            if line.startswith("["):
                active = line.strip().lower() == "[events]"
            elif active and line.lower().startswith("format:"):
                fields = [part.strip().lower() for part in line.split(":", 1)[1].split(",")]
                if not fields or fields[-1] != "text" or not {"start", "end", "text"} <= set(fields):
                    fail("ASS Events Format requires Start, End and Text as its last column.")
            elif active and line.lower().startswith("dialogue:"):
                if not fields:
                    fail("ASS Dialogue needs an Events Format line.")
                values = line.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
                if len(values) != len(fields):
                    fail("Invalid ASS Dialogue field count.")
                row = dict(zip(fields, values))
                cue = re.sub(r"\{[^}]*\}", "", row["text"])
                cue = cue.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
                events.append({"start": caption_seconds(row["start"]), "end": caption_seconds(row["end"]), "text": cue})
        warnings.append("ASS preview uses Dialogue timing and plain text. ASS styles, override tags, animation, drawings and karaoke are not reproduced; the original ASS is retained and canonical SRT is supplied.")
    else:
        fail("Subtitles source must be .srt or .ass.")
    if not events:
        fail("No subtitle cues were found.", {"source": str(path)})
    return events, warnings


def overlay_style(event, defaults, height):
    result = dict(defaults)
    result.update(event)
    if not isinstance(result.get("text"), str) or not result["text"].strip() or len(result["text"]) > 4000 or "\0" in result["text"]:
        fail("Overlay text must contain 1-4000 characters.")
    try:
        result["text"].encode("utf-8")
    except UnicodeEncodeError:
        fail("Overlay text contains an invalid Unicode surrogate.")
    result.setdefault("font_face", "Microsoft YaHei")
    result.setdefault("font_size", max(18, round(height * (0.075 if result.get("role") == "title" else 0.045))))
    result.setdefault("color", "#FFFFFF")
    result.setdefault("position", "center" if result.get("role") == "title" else "bottom")
    result.setdefault("bold", result.get("role") == "title")
    result.setdefault("fade_in", 0)
    result.setdefault("fade_out", 0)
    if not isinstance(result["font_face"], str) or not result["font_face"].strip() or len(result["font_face"]) > 31 or "\0" in result["font_face"]:
        fail("font_face must be an installed font family name of 1-31 characters.")
    if type(result["font_size"]) is not int or not 10 <= result["font_size"] <= 512:
        fail("font_size must be an integer from 10 to 512 pixels.")
    if not isinstance(result["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result["color"]):
        fail("Overlay color must be #RRGGBB.")
    if result["position"] not in ("top", "center", "bottom") or type(result["bold"]) is not bool:
        fail("Overlay position must be top/center/bottom and bold must be boolean.")
    return result


def load_overlay_events(data, timeline_path, timeline):
    events = []
    allowed = {"text", "start", "end", "font_face", "font_size", "color", "position", "bold", "fade_in", "fade_out"}
    titles = data.get("titles", [])
    if not isinstance(titles, list):
        fail("titles must be an array.")
    for title in titles:
        if not isinstance(title, dict):
            fail("Each title must be an object.")
        check_keys(title, allowed, "titles")
        events.append(overlay_style(title, {"role": "title"}, timeline["project"]["height"]))
    subtitles = data.get("subtitles")
    if subtitles is not None:
        if not isinstance(subtitles, dict):
            fail("subtitles must be an object or null.")
        check_keys(subtitles, {"source", "font_face", "font_size", "color", "position", "bold", "fade_in", "fade_out"}, "subtitles")
        path = media_path(subtitles.get("source"), timeline_path.parent)
        raw_events, warnings = parse_caption_file(path)
        defaults = {key: value for key, value in subtitles.items() if key != "source"}
        defaults["role"] = "subtitle"
        for event in raw_events:
            events.append(overlay_style(event, defaults, timeline["project"]["height"]))
        timeline["warnings"] += warnings
        timeline["sources"].append(path)
        timeline["subtitle_source"] = str(path)
    if len(events) > 1000:
        fail("A timeline is limited to 1000 title/subtitle overlay events.")
    fps = timeline["fps_fraction"]
    for index, event in enumerate(events):
        start = as_float(event.get("start"), f"overlay[{index}].start", 0)
        end = as_float(event.get("end"), f"overlay[{index}].end", 0)
        if end <= start or end > timeline["duration_seconds"] + .001:
            fail("Overlay must have start<end within the final sequence duration (after transition overlaps).", {"event": index, "sequence_seconds": timeline["duration_seconds"]})
        start_frame = round_frames(start, fps)
        end_frame = min(timeline["total_frames"], round_frames(end, fps))
        if end_frame <= start_frame:
            fail("Overlay is shorter than one sequence frame.")
        event.update(start_frame=start_frame, end_frame=end_frame, start=float(Fraction(start_frame, 1) / fps), end=float(Fraction(end_frame, 1) / fps))
        for fade in ("fade_in", "fade_out"):
            event[fade] = as_float(event[fade], f"overlay[{index}].{fade}", 0)
        if event["fade_in"] + event["fade_out"] > event["end"] - event["start"]:
            fail("Overlay fades cannot exceed the event duration.")
    events.sort(key=lambda e: (e["start_frame"], e["role"], e["end_frame"]))
    return events


def png_bytes(width, height, rgba):
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff)
    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def text_png(event, width, height):
    """Draw installed Unicode fonts using Windows GDI; return an RGBA PNG.

    The rasterized text is a transparent handoff asset, not editable native text.
    """
    if os.name != "nt":
        fail("This text rasterizer currently requires Windows GDI. Use a Windows host or pre-rendered title images; no fallback font downloads are attempted.", code=3)
    from ctypes import wintypes as W
    gdi, user = ctypes.WinDLL("gdi32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)
    class Header(ctypes.Structure):
        _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG), ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD), ("image_size", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG), ("used", W.DWORD), ("important", W.DWORD)]
    class Info(ctypes.Structure):
        _fields_ = [("header", Header), ("colors", W.DWORD * 3)]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [W.HDC], W.HDC
    gdi.CreateDIBSection.argtypes, gdi.CreateDIBSection.restype = [W.HDC, ctypes.POINTER(Info), W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD], W.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [W.HDC, W.HANDLE], W.HANDLE
    gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [W.HANDLE], W.BOOL
    gdi.DeleteDC.argtypes, gdi.DeleteDC.restype = [W.HDC], W.BOOL
    gdi.CreateFontW.argtypes = [ctypes.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]
    gdi.CreateFontW.restype = W.HFONT
    gdi.SetTextColor.argtypes, gdi.SetTextColor.restype = [W.HDC, W.DWORD], W.DWORD
    gdi.SetBkMode.argtypes, gdi.SetBkMode.restype = [W.HDC, ctypes.c_int], ctypes.c_int
    gdi.GdiFlush.argtypes, gdi.GdiFlush.restype = [], W.BOOL
    gdi.GetTextFaceW.argtypes, gdi.GetTextFaceW.restype = [W.HDC, ctypes.c_int, W.LPWSTR], ctypes.c_int
    user.DrawTextW.argtypes, user.DrawTextW.restype = [W.HDC, W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.RECT), W.UINT], ctypes.c_int
    info = Info(Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
    dc, bitmap, font = None, None, None
    old_bitmap, old_font = None, None
    try:
        dc = gdi.CreateCompatibleDC(None)
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not dc or not bitmap or not bits.value:
            fail("Windows text canvas allocation failed.", code=3)
        old_bitmap = gdi.SelectObject(dc, bitmap)
        font = gdi.CreateFontW(-event["font_size"], 0, 0, 0, 700 if event["bold"] else 400, 0, 0, 0, 1, 0, 0, 4, 0, event["font_face"])
        if not font:
            fail("Windows font creation failed.", {"font_face": event["font_face"]}, 3)
        old_font = gdi.SelectObject(dc, font)
        actual_face = ctypes.create_unicode_buffer(64)
        gdi.GetTextFaceW(dc, 64, actual_face)
        aliases = ({"microsoft yahei", "微软雅黑"}, {"microsoft yahei ui", "微软雅黑 ui"})
        requested, actual = event["font_face"].casefold(), actual_face.value.casefold()
        if requested != actual and not any(requested in family and actual in family for family in aliases):
            fail("Requested font family is not installed; silent font substitution was refused.", {"requested": event["font_face"], "windows_substitute": actual_face.value})
        ctypes.memset(bits, 0, width * height * 4)
        gdi.SetTextColor(dc, 0xFFFFFF)
        gdi.SetBkMode(dc, 1)
        margin_x, margin_y = max(4, round(width * .08)), max(4, round(height * .07))
        rect = W.RECT(margin_x, 0, width - margin_x, height)
        flags = 1 | 0x10 | 0x800
        measured = user.DrawTextW(dc, event["text"], -1, ctypes.byref(rect), flags | 0x400)
        if measured <= 0 or measured > height - 2 * margin_y or rect.right > width - margin_x + 2:
            fail("Overlay text does not fit. Reduce font_size or add explicit line breaks.", {"text": event["text"][:100], "font_size": event["font_size"], "measured_height": measured})
        top = margin_y if event["position"] == "top" else (height - margin_y - measured if event["position"] == "bottom" else (height - measured) // 2)
        rect = W.RECT(margin_x, top, width - margin_x, top + measured)
        user.DrawTextW(dc, event["text"], -1, ctypes.byref(rect), flags)
        gdi.GdiFlush()
        bgra = ctypes.string_at(bits, width * height * 4)
        rgb = tuple(int(event["color"][i:i + 2], 16) for i in (1, 3, 5))
        rgba = bytearray(width * height * 4)
        # Only scan text rows; build a subtle dark outline for readability.
        radius = max(1, min(3, event["font_size"] // 20))
        for y in range(max(0, top - radius), min(height, top + measured + radius)):
            for x in range(max(0, margin_x - radius), min(width, width - margin_x + radius)):
                offset = (y * width + x) * 4
                alpha = bgra[offset]
                outline = alpha
                for dy, dx in ((-radius, 0), (radius, 0), (0, -radius), (0, radius)):
                    sy, sx = y + dy, x + dx
                    if 0 <= sy < height and 0 <= sx < width:
                        outline = max(outline, bgra[(sy * width + sx) * 4])
                if outline:
                    rgba[offset:offset + 4] = bytes((int(rgb[0] * alpha / outline), int(rgb[1] * alpha / outline), int(rgb[2] * alpha / outline), outline))
        return png_bytes(width, height, bytes(rgba))
    finally:
        if dc and old_font:
            gdi.SelectObject(dc, old_font)
        if dc and old_bitmap:
            gdi.SelectObject(dc, old_bitmap)
        if font:
            gdi.DeleteObject(font)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)


def write_binary(path, payload, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".shadow-asset-", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        if path.exists() and not force:
            fail("Generated asset already exists; use --force.", {"path": str(path)})
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def srt_timestamp(value):
    milliseconds = round(value * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_part:02},{milliseconds:03}"


def overlay_assets(timeline, assets_dir, force=False, dry_run=False):
    events, rendered = [], set()
    project = timeline["project"]
    for event in timeline.get("overlay_events", []):
        style = {key: event[key] for key in ("text", "font_face", "font_size", "color", "position", "bold")}
        digest = hashlib.sha256(json.dumps(dict(style, width=project["width"], height=project["height"]), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        png = assets_dir / f"text-{digest}.png"
        destination = output_guard(png, timeline["sources"], force, {".png"}) if str(png) not in rendered else png
        if not dry_run and str(png) not in rendered:
            write_binary(destination, text_png(event, project["width"], project["height"]), force)
        rendered.add(str(png))
        events.append(dict(event, asset=str(png)))
    if events and not dry_run:
        original_asset = None
        if timeline.get("subtitle_source"):
            original_source = Path(timeline["subtitle_source"])
            original_asset = assets_dir / ("original-subtitles" + original_source.suffix.lower())
            if path_key(original_asset) != path_key(original_source):
                destination = output_guard(original_asset, timeline["sources"], force, {".srt", ".ass"})
                write_binary(destination, original_source.read_bytes(), force)
        manifest = output_guard(assets_dir / "text-events.json", timeline["sources"], force, {".json"})
        save_json(manifest, {"project": project, "events": events, "subtitle_source": timeline.get("subtitle_source"), "original_subtitle_asset": str(original_asset) if original_asset else None, "text_editing": "Edit the timeline JSON/SRT and regenerate. PNGs are image layers, not native editable Premiere text."}, force)
        captions = [e for e in events if e["role"] == "subtitle"]
        if captions:
            destination = output_guard(assets_dir / "captions.srt", timeline["sources"], force, {".srt"})
            srt = "\n\n".join(f"{i}\n{srt_timestamp(e['start'])} --> {srt_timestamp(e['end'])}\n{e['text']}" for i, e in enumerate(captions, 1)) + "\n"
            atomic_write(destination, srt, force)
    return events


def ntsc_rate(fps):
    if fps.denominator == 1:
        return fps.numerator, False
    # 24000/1001 is timebase 24; 30000/1001 is timebase 30. Never infer one from the other.
    nominal = fps * Fraction(1001, 1000)
    if nominal.denominator == 1:
        return nominal.numerator, True
    fail("This frame rate cannot be represented exactly by FCP7 XML timebase/ntsc.", {"fps": fraction_string(fps), "remedy": "Use an integer rate or an exact nominal*1000/1001 rate for XML; render accepts other rational rates."})


def source_xml_fps(media, sequence_fps, warnings):
    videos = media["video_streams"]
    if not videos:
        return sequence_fps
    video = videos[0]
    avg = probe_fraction(video.get("avg_frame_rate"))
    nominal = probe_fraction(video.get("r_frame_rate"))
    fps = avg or nominal
    if fps is None:
        fail("Source video frame rate is unknown; XML export cannot determine source frame positions.", {"source": media["path"]})
    try:
        ntsc_rate(fps)
    except ShadowError:
        if nominal is None:
            raise
        ntsc_rate(nominal)
        warnings.append(f"{media['path']}: average FPS is not exactly representable in FCP7 XML; source frame positions use nominal rate {fraction_string(nominal)}. Verify timing in Premiere, especially for VFR media.")
        fps = nominal
    if avg and nominal and abs(float(avg / nominal) - 1) > 0.001:
        warnings.append(f"{media['path']}: average and nominal FPS differ; variable-frame-rate timing requires Premiere verification or prior CFR transcoding.")
    return fps


def element(parent, tag, value=None, **attributes):
    node = ET.SubElement(parent, tag, attributes)
    if value is not None:
        node.text = str(value)
    return node


def add_rate(parent, fps):
    nominal, is_ntsc = ntsc_rate(fps)
    rate = element(parent, "rate")
    element(rate, "timebase", nominal)
    element(rate, "ntsc", "TRUE" if is_ntsc else "FALSE")
    return rate


def path_url(source):
    path = Path(source).resolve().as_posix()
    return "file://localhost" + ("/" if not path.startswith("/") else "") + quote(path, safe="/")


def add_file(parent, media, fps, file_id, full=True):
    file_node = element(parent, "file", id=file_id)
    if not full:
        return file_node
    element(file_node, "name", Path(media["path"]).name)
    element(file_node, "pathurl", path_url(media["path"]))
    add_rate(file_node, fps)
    duration = media["duration_seconds"] or 0
    if media["video_streams"]:
        duration = media["video_streams"][0]["duration_seconds"] or duration
    elif media["audio_streams"]:
        duration = media["audio_streams"][0]["duration_seconds"] or duration
    element(file_node, "duration", round_frames(duration, fps))
    characteristics = element(file_node, "media")
    if media["video_streams"]:
        video = media["video_streams"][0]
        v = element(characteristics, "video")
        sample = element(v, "samplecharacteristics")
        add_rate(sample, fps)
        element(sample, "width", video["width"])
        element(sample, "height", video["height"])
        element(sample, "anamorphic", "FALSE")
        sar = video.get("sample_aspect_ratio")
        if sar not in (None, "N/A", "0:1", "1:1"):
            fail("XML export supports square-pixel sources only; arbitrary SAR is not an FCP7 pixelaspectratio enum.", {"source": media["path"], "sample_aspect_ratio": sar, "remedy": "Create a square-pixel source or perform the edit and framing manually in Premiere. Preview rendering can normalize this source."})
        element(sample, "pixelaspectratio", "square")
        order = video.get("field_order")
        element(sample, "fielddominance", "upper" if order in ("tt", "tb") else ("lower" if order in ("bb", "bt") else "none"))
        if media.get("is_still"):
            element(v, "stillframe", "TRUE")
    if media["audio_streams"]:
        audio = media["audio_streams"][0]
        a = element(characteristics, "audio")
        sample = element(a, "samplecharacteristics")
        element(sample, "depth", 16)
        element(sample, "samplerate", audio["sample_rate"] or 48000)
        element(a, "channelcount", audio["channels"] or 1)
        channels = int(audio["channels"] or 1)
        if channels in (1, 2):
            element(a, "layout", "mono" if channels == 1 else "stereo")
            for channel in range(channels):
                audiochannel = element(a, "audiochannel")
                element(audiochannel, "channellabel", "discrete" if channels == 1 else ("left" if channel == 0 else "right"))
                element(audiochannel, "sourcechannel", channel + 1)
    return file_node


def add_clipitem(track, clip, media, source_fps, sequence_fps, clip_id, file_id,
                 kind, channel=None, full_file=False, end_frame=None):
    item = element(track, "clipitem", id=clip_id)
    element(item, "name", clip["label"])
    element(item, "enabled", "TRUE")
    media_duration = (media["video_streams"][0]["duration_seconds"] if media["video_streams"] else media["audio_streams"][0]["duration_seconds"])
    element(item, "duration", round_frames(media_duration or 0, source_fps))
    add_rate(item, source_fps)
    element(item, "start", clip["start_frame"])
    element(item, "end", clip["end_frame"] if end_frame is None else end_frame)
    source_in = round_frames(clip["in"], source_fps)
    source_out = round_frames(clip["out"], source_fps)
    if source_out <= source_in:
        fail("Clip collapses below one source frame for FCP7 XML.", {"source": media["path"], "in": clip["in"], "out": clip["out"]})
    element(item, "in", source_in)
    element(item, "out", source_out)
    if kind == "video":
        element(item, "alphatype", "straight" if media.get("overlay_asset") else "none")
        element(item, "pixelaspectratio", "square")
        element(item, "anamorphic", "FALSE")
        if media.get("is_still"):
            element(item, "stillframe", "TRUE")
    add_file(item, media, source_fps, file_id, full_file)
    sourcetrack = element(item, "sourcetrack")
    element(sourcetrack, "mediatype", kind)
    # Apple's FCP7 catalog warns against a trackindex for a single video-only source.
    if kind == "audio" or media["audio_streams"] or len(media["video_streams"]) > 1:
        element(sourcetrack, "trackindex", channel if channel is not None else 1)
    return item


def add_links(item, links):
    audio_count = sum(kind == "audio" for _, kind, _, _ in links)
    for reference, kind, track_index, clip_index in links:
        link = element(item, "link")
        element(link, "linkclipref", reference)
        element(link, "mediatype", kind)
        element(link, "trackindex", track_index)
        element(link, "clipindex", clip_index)
        if kind == "audio" and audio_count == 2:
            element(link, "groupindex", 1)


def add_volume(item, db):
    # FCP7 audio-level parameter is linear amplitude (0=silent, 1=unity).
    effect = element(element(item, "filter"), "effect")
    element(effect, "name", "Audio Levels")
    element(effect, "effectid", "audiolevels")
    element(effect, "effectcategory", "audio")
    element(effect, "effecttype", "filter")
    element(effect, "mediatype", "audio")
    parameter = element(effect, "parameter")
    element(parameter, "parameterid", "level")
    element(parameter, "name", "Level")
    element(parameter, "valuemin", 0)
    element(parameter, "valuemax", 3.9810717055)
    element(parameter, "value", format(10 ** (db / 20), ".12g"))


def add_transition(track, start, end, fps, audio=False):
    item = element(track, "transitionitem")
    add_rate(item, fps)
    element(item, "start", start)
    element(item, "end", end)
    element(item, "alignment", "center")
    effect = element(item, "effect")
    name = "Cross Fade (+3dB)" if audio else "Cross Dissolve"
    element(effect, "name", name)
    element(effect, "effectid", name)
    element(effect, "effecttype", "transition")
    element(effect, "mediatype", "audio" if audio else "video")
    element(effect, "startratio", 0)
    element(effect, "endratio", 1)
    element(effect, "reverse", "FALSE")
    return item


def make_xml(timeline):
    fps = timeline["fps_fraction"]
    ntsc_rate(fps)
    warnings, xml_timing = [], []
    root = ET.Element("xmeml", {"version": "4"})
    sequence = element(root, "sequence", id="shadow-sequence-1")
    element(sequence, "name", timeline["project"]["name"])
    element(sequence, "duration", timeline["total_frames"])
    add_rate(sequence, fps)
    element(sequence, "in", 0)
    element(sequence, "out", timeline["total_frames"])
    timecode = element(sequence, "timecode")
    add_rate(timecode, fps)
    element(timecode, "string", "00:00:00:00")
    element(timecode, "frame", 0)
    element(timecode, "displayformat", "NDF")
    media = element(sequence, "media")
    video = element(media, "video")
    sample = element(element(video, "format"), "samplecharacteristics")
    add_rate(sample, fps)
    element(sample, "width", timeline["project"]["width"])
    element(sample, "height", timeline["project"]["height"])
    element(sample, "anamorphic", "FALSE")
    element(sample, "pixelaspectratio", "square")
    element(sample, "fielddominance", "none")
    video_track = element(video, "track")
    audio = element(media, "audio")
    element(audio, "numOutputChannels", 2)
    sample = element(element(audio, "format"), "samplecharacteristics")
    element(sample, "depth", 16)
    element(sample, "samplerate", timeline["project"]["sample_rate"])
    outputs = element(audio, "outputs")
    group = element(outputs, "group")
    element(group, "index", 1)
    element(group, "numchannels", 2)
    element(group, "downmix", 0)
    for channel in (1, 2):
        element(element(group, "channel"), "index", channel)
    max_channels = max((int(c["media"]["audio_streams"][0].get("channels") or 1)
                        for c in timeline["clips"] if c["audio"] and c["media"]["audio_streams"]), default=0)
    if max_channels > 8:
        fail("XML export supports up to 8 source audio channels.")
    audio_tracks = [element(audio, "track") for _ in range(max_channels)]
    track_clip_counts = [0] * max_channels
    file_ids, written_files = {}, set()
    previous_video, previous_audio = None, []
    for ordinal, clip in enumerate(timeline["clips"], 1):
        source = clip["media"]
        if len(source["video_streams"]) > 1:
            fail("XML export refuses ambiguous multiple video streams; transcode the selected stream first.", {"source": source["path"]})
        if clip["audio"] and len(source["audio_streams"]) > 1:
            fail("XML export refuses ambiguous multiple audio streams; transcode the selected stream first.", {"source": source["path"]})
        source_fps = source_xml_fps(source, fps, warnings)
        source_in_frame = round_frames(clip["in"], source_fps)
        source_out_frame = round_frames(clip["out"], source_fps)
        xml_timing.append({
            "clip_number": ordinal, "source": source["path"], "source_fps": fraction_string(source_fps),
            "source_in_frame": source_in_frame, "source_out_frame": source_out_frame,
            "sequence_start_frame": clip["start_frame"], "sequence_end_frame": clip["end_frame"],
            "source_in_seconds_after_quantization": float(Fraction(source_in_frame, 1) / source_fps),
            "source_out_seconds_after_quantization": float(Fraction(source_out_frame, 1) / source_fps),
        })
        if source_fps != fps:
            warnings.append(f"Clip {ordinal}: mixed source/sequence rates are encoded independently. Compare the imported in/out frames with xml_clip_timing before saving a Premiere project.")
        file_id = file_ids.setdefault(source["path"], f"shadow-file-{len(file_ids) + 1}")
        video_id = f"shadow-v-{ordinal}"
        transition = clip.get("transition_in")
        if transition:
            if transition["type"] != "dissolve":
                fail("Native XML currently supports dissolve only; select automatic/baked transition handoff for fadeblack.")
            previous_video.find("end").text = "-1"
            add_transition(video_track, clip["start_frame"], clip["start_frame"] + transition["frames"], fps)
        video_item = add_clipitem(video_track, clip, source, source_fps, fps, video_id, file_id, "video", full_file=file_id not in written_files)
        if transition:
            video_item.find("start").text = "-1"
        written_files.add(file_id)
        links = [(video_id, "video", 1, ordinal)]
        items = [video_item]
        channels = int(source["audio_streams"][0].get("channels") or 1) if clip["audio"] and source["audio_streams"] else 0
        current_audio = []
        next_transition = timeline["clips"][ordinal].get("transition_in") if ordinal < len(timeline["clips"]) else None
        audio_clip = dict(clip)
        if transition and transition["audio"] == "cut":
            trim = transition["frames"] // 2
            audio_clip["start_frame"] += trim
            audio_clip["in"] += float(Fraction(trim, 1) / fps)
        if next_transition and next_transition["audio"] == "cut":
            trim = next_transition["frames"] - next_transition["frames"] // 2
            audio_clip["end_frame"] -= trim
            audio_clip["out"] -= float(Fraction(trim, 1) / fps)
        for channel in range(channels):
            audio_id = f"shadow-a-{ordinal}-{channel + 1}"
            track_clip_counts[channel] += 1
            if transition and transition["audio"] == "crossfade":
                if len(previous_audio) != channels:
                    fail("Native audio crossfade needs matching adjacent source channel counts. Use --transition-mode bake.")
                previous_audio[channel].find("end").text = "-1"
                add_transition(audio_tracks[channel], clip["start_frame"], clip["start_frame"] + transition["frames"], fps, audio=True)
            item = add_clipitem(audio_tracks[channel], audio_clip, source, source_fps, fps, audio_id, file_id, "audio", channel=channel + 1)
            if transition and transition["audio"] == "crossfade":
                item.find("start").text = "-1"
            if clip.get("gain_db", 0):
                add_volume(item, clip["gain_db"])
            current_audio.append(item)
            links.append((audio_id, "audio", channel + 1, track_clip_counts[channel]))
            items.append(item)
        for item in items:
            add_links(item, links)
        previous_video, previous_audio = video_item, current_audio
    for ordinal, event in enumerate(timeline.get("resolved_overlays", []), 1):
        track = element(video, "track")
        frames = event["end_frame"] - event["start_frame"]
        duration = float(Fraction(frames, 1) / fps)
        source = {"path": event["asset"], "duration_seconds": duration, "video_streams": [{"width": timeline["project"]["width"], "height": timeline["project"]["height"], "duration_seconds": duration, "sample_aspect_ratio": "1:1", "field_order": "progressive"}], "audio_streams": [], "is_still": True, "overlay_asset": True}
        overlay_clip = {"label": event["text"].replace("\n", " ")[:80], "start_frame": event["start_frame"], "end_frame": event["end_frame"], "in": 0, "out": duration}
        add_clipitem(track, overlay_clip, source, fps, fps, f"shadow-text-{ordinal}", f"shadow-text-file-{ordinal}", "video", full_file=True)
        element(track, "enabled", "TRUE")
        element(track, "locked", "FALSE")
        if event["fade_in"] or event["fade_out"]:
            warnings.append("Title/subtitle opacity fades are applied in the preview. XML supplies timed PNG image layers; reapply those fades in Premiere or use the rendered preview as reference.")
    music = timeline["music"]
    if music:
        source = music["media"]
        if len(source["audio_streams"]) > 1:
            fail("XML export refuses ambiguous multiple music audio streams.", {"source": source["path"]})
        channels = int(source["audio_streams"][0].get("channels") or 1)
        if channels > 8:
            fail("XML export supports up to 8 music channels.")
        # Music contains audio; its in/out values use the sequence timebase even if the file contains incidental video.
        audio_only_media = dict(source, video_streams=[])
        music_frames = min(timeline["total_frames"], round_frames(music["duration_seconds"], fps))
        if music_frames < 1:
            fail("Music segment is shorter than one sequence frame for XML.")
        music_clip = {
            "label": Path(music["source"]).stem, "start_frame": 0, "end_frame": music_frames,
            "in": music["in"], "out": music["in"] + float(Fraction(music_frames, 1) / fps),
        }
        # Use a separate file definition because its timebase/selected media may differ from video usage.
        file_id = "shadow-music-file"
        music_items, music_links = [], []
        for channel in range(channels):
            track = element(audio, "track")
            audio_id = f"shadow-music-{channel + 1}"
            item = add_clipitem(track, music_clip, audio_only_media, fps, fps, audio_id, file_id, "audio", channel + 1, full_file=channel == 0)
            add_volume(item, music["gain_db"])
            music_items.append(item)
            music_links.append((audio_id, "audio", max_channels + channel + 1, 1))
        for item in music_items:
            add_links(item, music_links)
        warnings.append("Music gain is encoded as FCP7 Audio Levels. Confirm the imported gain in Premiere; preview rendering applies the gain deterministically.")
    for track in audio.findall("track"):
        element(track, "enabled", "TRUE")
        element(track, "locked", "FALSE")
    if audio.findall("track"):
        warnings.append("Source channels are separate linked audio clip items. Stereo channel labels/pair groups and a stereo output group are encoded, but Premiere panning/output routing is unverified; audition left/right, centered mono and any multichannel downmix after import.")
    element(video_track, "enabled", "TRUE")
    element(video_track, "locked", "FALSE")
    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"
    # Independently parse before writing; semantic Premiere acceptance remains a user/application check.
    ET.fromstring(xml)
    return xml, list(dict.fromkeys(warnings)), xml_timing


def encoder_options(encoder):
    if encoder == "libx264":
        return ["-preset", "veryfast", "-crf", "22"]
    if encoder == "h264_nvenc":
        return ["-preset", "p4", "-cq", "23", "-b:v", "0"]
    return ["-q:v", "3"]


def choose_encoder(ffmpeg, requested, project=None):
    result = run([ffmpeg, "-hide_banner", "-encoders"])
    available = set(re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", result.stdout, re.MULTILINE))
    if requested == "auto":
        selected = "libx264" if "libx264" in available else "mpeg4"
        if selected == "mpeg4" and "h264_nvenc" in available:
            project = project or {"width": 640, "height": 360, "fps": "25"}
            # Encoder listing alone is not proof that a driver/GPU supports the requested format.
            check = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                         f"color=black:size={project['width']}x{project['height']}:rate={project['fps']}",
                         "-frames:v", "1", "-c:v", "h264_nvenc", *encoder_options("h264_nvenc"), "-pix_fmt", "yuv420p", "-f", "null", "-"], timeout=30, check=False)
            if check.returncode == 0:
                selected = "h264_nvenc"
    else:
        selected = requested
    if selected not in available:
        fail("Requested video encoder is unavailable.", {"encoder": selected, "remedy": "Use --video-codec mpeg4 or supply a FFmpeg build with the requested encoder."}, 3)
    if "aac" not in available or "pcm_s16le" not in available:
        fail("FFmpeg needs aac and pcm_s16le encoders for the preview pipeline.", code=3)
    return selected


def seconds(value):
    return format(float(value), ".12f").rstrip("0").rstrip(".") or "0"


def render_commands(timeline, ffmpeg, encoder, work_dir, output):
    project = timeline["project"]
    fps = project["fps"]
    width, height, sample_rate = project["width"], project["height"], project["sample_rate"]
    commands, chunks = [], []
    for ordinal, clip in enumerate(timeline["clips"], 1):
        duration = seconds(clip["duration_seconds"])
        chunk = work_dir / f"clip-{ordinal:05d}.mkv"
        chunks.append(chunk)
        argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if clip.get("kind") == "image":
            argv += ["-loop", "1", "-framerate", fps, "-i", clip["source"]]
        else:
            argv += ["-ss", seconds(clip["in"]), "-i", clip["source"]]
        has_audio = clip["audio"] and clip["media"]["audio_streams"]
        if not has_audio:
            argv += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}"]
        video_index = clip["media"]["video_streams"][0]["index"]
        audio_input = f"0:{clip['media']['audio_streams'][0]['index']}" if has_audio else "1:a:0"
        # Conform display aspect, frame rate, pixel format, timestamps, channels and sample rate before concat.
        video_filter = (
            f"scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration},"
            f"trim=end_frame={clip['frames']},format=yuv420p"
        )
        audio_filter = f"aresample={sample_rate},aformat=sample_fmts=s16:channel_layouts=stereo,asetpts=PTS-STARTPTS,volume={clip.get('gain_db', 0)}dB,apad,atrim=duration={duration}"
        argv += ["-map", f"0:{video_index}", "-map", audio_input, "-vf", video_filter,
                 "-af", audio_filter, "-t", duration, "-c:v", encoder]
        argv += encoder_options(encoder)
        argv += ["-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2", str(chunk)]
        commands.append(argv)
    concat_path = work_dir / "concat.txt"
    # Only generated safe ASCII filenames are placed inside the concat list.
    concat_text = "".join("file '" + chunk.name + "'\n" for chunk in chunks)
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path)]
    music = timeline["music"]
    if music:
        argv += ["-ss", seconds(music["in"]), "-i", music["source"]]
        audio_index = music["media"]["audio_streams"][0]["index"]
        total = seconds(timeline["duration_seconds"])
        af = (
            f"[0:a:0]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,apad,atrim=duration={total}[base];"
            f"[1:{audio_index}]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,volume={music['gain_db']}dB,apad,atrim=duration={total}[music];"
            "[base][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
        )
        argv += ["-filter_complex", af, "-map", "0:v:0", "-map", "[mix]"]
    else:
        argv += ["-map", "0:v:0", "-map", "0:a:0"]
    argv += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(sample_rate), "-ac", "2", "-t", seconds(timeline["duration_seconds"])]
    if output.suffix.lower() in (".mp4", ".mov"):
        argv += ["-movflags", "+faststart"]
    argv += [str(output)]
    commands.append(argv)
    return commands, concat_path, concat_text


def extended_render_commands(timeline, ffmpeg, encoder, work_dir, output, overlays):
    normalized, _, _ = render_commands(timeline, ffmpeg, encoder, work_dir, output)
    commands = normalized[:-1]
    count = len(timeline["clips"])
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-filter_complex_threads", "1"]
    filters = []
    fps, sr = timeline["project"]["fps"], timeline["project"]["sample_rate"]
    for index, clip in enumerate(timeline["clips"]):
        argv += ["-i", str(work_dir / f"clip-{index + 1:05d}.mkv")]
        filters += [f"[{index}:v:0]setpts=PTS-STARTPTS,fps={fps},settb=AVTB,format=yuv420p[v{index}]",
                    f"[{index}:a:0]aresample={sr},asettb=1/{sr},asetpts=PTS-STARTPTS,atrim=duration={seconds(clip['duration_seconds'])}[a{index}]"]
    current_v, current_a = "v0", "a0"
    elapsed = timeline["clips"][0]["duration_seconds"]
    for index in range(1, count):
        clip, transition = timeline["clips"][index], timeline["clips"][index].get("transition_in")
        next_v, next_a = f"joinrawv{index}", f"joina{index}"
        if transition:
            duration = transition["duration_seconds"]
            if transition["type"] == "dissolve":
                effect = "transition=fade"
            else:
                # A symmetric dip through studio-range YUV black, rather than FFmpeg's asymmetric fadeblack preset.
                black = "if(eq(PLANE,0),16,128)"
                expression = f"if(gte(P,0.5),A*(2*P-1)+{black}*(2-2*P),B*(1-2*P)+{black}*2*P)"
                effect = f"transition=custom:expr='{expression}'"
            filters.append(f"[{current_v}][v{index}]xfade={effect}:duration={seconds(duration)}:offset={seconds(elapsed-duration)}[{next_v}]")
            if transition["audio"] == "crossfade":
                filters.append(f"[{current_a}][a{index}]acrossfade=d={seconds(duration)}:c1=qsin:c2=qsin[{next_a}]")
            else:
                filters += [f"[{current_a}]atrim=duration={seconds(elapsed-duration/2)},asetpts=PTS-STARTPTS[cutout{index}]",
                            f"[a{index}]atrim=start={seconds(duration/2)},asetpts=PTS-STARTPTS[cutin{index}]",
                            f"[cutout{index}][cutin{index}]concat=n=2:v=0:a=1[{next_a}]"]
            elapsed -= duration
        else:
            filters += [f"[{current_v}][v{index}]concat=n=2:v=1:a=0[{next_v}]",
                        f"[{current_a}][a{index}]concat=n=2:v=0:a=1[{next_a}]"]
        elapsed += clip["duration_seconds"]
        normalized_v = f"joinv{index}"
        filters.append(f"[{next_v}]setpts=PTS-STARTPTS,fps={fps},settb=AVTB[{normalized_v}]")
        current_v, current_a = normalized_v, next_a
    filters.append(f"[{current_v}]tpad=stop_mode=clone:stop_duration=1,fps={fps},trim=end_frame={timeline['total_frames']},setpts=PTS-STARTPTS,format=yuv420p[basevideo]")
    current_v = "basevideo"
    next_input = count
    music = timeline["music"]
    total = seconds(timeline["duration_seconds"])
    filters.append(f"[{current_a}]apad,atrim=duration={total},asetpts=PTS-STARTPTS[baseaudio]")
    current_a = "baseaudio"
    if music:
        argv += ["-ss", seconds(music["in"]), "-i", music["source"]]
        music_index = music["media"]["audio_streams"][0]["index"]
        filters += [f"[{next_input}:{music_index}]aresample={sr},aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,volume={music['gain_db']}dB,apad,atrim=duration={total}[music]",
                    "[baseaudio][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixedaudio]"]
        current_a = "mixedaudio"
        next_input += 1
    for index, event in enumerate(overlays):
        argv += ["-loop", "1", "-framerate", fps, "-i", event["asset"]]
        text_filter = f"[{next_input}:v:0]format=rgba,setpts=PTS-STARTPTS"
        if event["fade_in"]:
            text_filter += f",fade=t=in:st={seconds(event['start'])}:d={seconds(event['fade_in'])}:alpha=1"
        if event["fade_out"]:
            text_filter += f",fade=t=out:st={seconds(event['end']-event['fade_out'])}:d={seconds(event['fade_out'])}:alpha=1"
        filters.append(text_filter + f"[text{index}]")
        filters.append(f"[{current_v}][text{index}]overlay=x=0:y=0:enable='gte(t,{seconds(event['start'])})*lt(t,{seconds(event['end'])})':eof_action=pass:repeatlast=1[overlay{index}]")
        current_v = f"overlay{index}"
        next_input += 1
    filters.append(f"[{current_v}]format=yuv420p,trim=end_frame={timeline['total_frames']}[finalvideo]")
    graph_path = work_dir / "composition.ffgraph"
    argv += ["-filter_complex_script", str(graph_path), "-map", "[finalvideo]", "-map", f"[{current_a}]", "-c:v", encoder]
    argv += encoder_options(encoder)
    argv += ["-pix_fmt", "yuv420p", "-r", fps, "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-ac", "2", "-t", total]
    if output.suffix.lower() in (".mp4", ".mov"):
        argv += ["-movflags", "+faststart"]
    argv += [str(output)]
    commands.append(argv)
    return commands, graph_path, ";\n".join(filters) + "\n"


def verify_render(path, timeline, ffmpeg, ffprobe, timeout):
    actual = probe(path, ffprobe)
    expected = timeline["project"]
    if len(actual["video_streams"]) != 1 or len(actual["audio_streams"]) != 1:
        fail("Preview stream count verification failed.", {"actual": actual}, 3)
    video, audio = actual["video_streams"][0], actual["audio_streams"][0]
    fps = probe_fraction(video.get("avg_frame_rate"))
    checks = {
        "width": video["width"] == expected["width"],
        "height": video["height"] == expected["height"],
        "fps": fps == timeline["fps_fraction"],
        "audio_channels": audio["channels"] == 2,
        "audio_sample_rate": audio["sample_rate"] == expected["sample_rate"],
        "pixel_format": video["pixel_format"] == "yuv420p",
        "size_nonzero": actual["size_bytes"] > 0,
    }
    tolerance = max(2 / float(timeline["fps_fraction"]), 0.10)
    checks["duration"] = abs((actual["duration_seconds"] or 0) - timeline["duration_seconds"]) <= tolerance
    if video["frame_count"] is not None:
        checks["frame_count"] = int(video["frame_count"]) == timeline["total_frames"]
    if not all(checks.values()):
        fail("Preview media verification failed.", {"checks": checks, "actual": actual, "expected_frames": timeline["total_frames"], "expected_duration_seconds": timeline["duration_seconds"]}, 3)
    # Decode every video/audio packet; successful headers alone do not prove a usable render.
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], timeout=timeout)
    return {"passed": True, "full_decode_passed": True, "checks": checks, "duration_tolerance_seconds": tolerance, "actual_media": actual}


def cmd_doctor(args):
    findings, errors = {}, []
    for name in ("ffmpeg", "ffprobe"):
        try:
            path = find_tool(name, getattr(args, name))
            result = run([path, "-version"], timeout=15)
            findings[name] = {"path": path, "version": result.stdout.splitlines()[0] if result.stdout else result.stderr.splitlines()[0]}
        except ShadowError as exc:
            findings[name] = {"available": False, "error": str(exc), "details": exc.details}
            errors.append(name)
    findings["python"] = {"version": sys.version.split()[0], "executable": sys.executable, "supported": sys.version_info >= (3, 9)}
    if not findings["python"]["supported"]:
        errors.append("python")
    emit({"ok": not errors, "shadow_version": VERSION, "dependencies": findings, "missing_or_failed": errors})
    return 0 if not errors else 3


def cmd_inspect(args):
    ffprobe = find_tool("ffprobe", args.ffprobe)
    paths, seen = [], set()
    for value in args.paths:
        path = Path(value).resolve()
        if path.is_dir():
            items = path.rglob("*") if args.recursive else path.iterdir()
            candidates = sorted((p for p in items if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS), key=lambda p: str(p).casefold())
        elif path.is_file():
            candidates = [path]
        else:
            fail("Inspection path does not exist.", {"path": str(path)})
        for candidate in candidates:
            key = path_key(candidate)
            if key not in seen:
                seen.add(key)
                paths.append(candidate.resolve())
    if len(paths) > args.max_files:
        fail("Inspection exceeds --max-files; narrow the supplied directory.", {"count": len(paths), "max_files": args.max_files})
    result, errors = [], []
    for path in paths:
        try:
            item = probe(path, ffprobe)
            if args.sha256:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                item["sha256"] = digest.hexdigest()
            result.append(item)
        except ShadowError as exc:
            errors.append({"path": str(path), "error": str(exc), "details": exc.details})
    report = {"ok": not errors, "files": result, "errors": errors, "file_count": len(result)}
    if args.output:
        destination = output_guard(args.output, paths, args.force, {".json"})
        save_json(destination, report, args.force)
    emit(report)
    return 0 if not errors else 3


def cmd_scenes(args):
    ffmpeg, ffprobe = find_tool("ffmpeg", args.ffmpeg), find_tool("ffprobe", args.ffprobe)
    path = media_path(args.source)
    media = probe(path, ffprobe)
    if not media["video_streams"]:
        fail("Scene candidates require video.")
    if not 0 < args.threshold <= 1 or not math.isfinite(args.min_gap) or args.min_gap < 0:
        fail("Scene threshold must be in (0,1] and min-gap must be nonnegative.")
    result = run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-map", f"0:{media['video_streams'][0]['index']}", "-an", "-vf", f"setpts=PTS-STARTPTS,select=gt(scene\\,{args.threshold}),showinfo", "-f", "null", "-"], timeout=args.timeout)
    candidates = []
    for match in re.finditer(r"pts_time:([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)", result.stderr):
        timestamp = float(match.group(1))
        if timestamp > 0 and (not candidates or timestamp - candidates[-1] >= args.min_gap):
            candidates.append(timestamp)
    report = {"ok": True, "source": str(path), "threshold": args.threshold, "min_gap_seconds": args.min_gap, "candidate_seconds": candidates, "interpretation": "Pixel-change candidates only. These do not establish narrative scenes or a good edit point."}
    if args.output:
        destination = output_guard(args.output, [path], args.force, {".json"})
        save_json(destination, report, args.force)
    emit(report)
    return 0


def cmd_silence(args):
    ffmpeg, ffprobe = find_tool("ffmpeg", args.ffmpeg), find_tool("ffprobe", args.ffprobe)
    path = media_path(args.source)
    media = probe(path, ffprobe)
    if not math.isfinite(args.noise_db) or not -120 <= args.noise_db <= 0 or not math.isfinite(args.min_duration) or args.min_duration <= 0:
        fail("noise-db must be in [-120,0] and min-duration must be positive.")
    intervals = []
    if media["audio_streams"]:
        audio = media["audio_streams"][0]
        result = run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-map", f"0:{audio['index']}", "-vn", "-af", f"silencedetect=noise={args.noise_db}dB:d={args.min_duration}", "-f", "null", "-"], timeout=args.timeout)
        beginning = None
        for line in result.stderr.splitlines():
            start = re.search(r"silence_start:\s*([\d.eE+-]+)", line)
            end = re.search(r"silence_end:\s*([\d.eE+-]+)", line)
            if start:
                beginning = max(0.0, float(start.group(1)))
            if end:
                ending = float(end.group(1))
                intervals.append({"start": beginning if beginning is not None else 0.0, "end": ending})
                beginning = None
        if beginning is not None and audio["duration_seconds"] is not None:
            intervals.append({"start": beginning, "end": audio["duration_seconds"]})
    report = {"ok": True, "source": str(path), "has_audio": bool(media["audio_streams"]), "noise_db": args.noise_db, "minimum_duration_seconds": args.min_duration, "candidate_intervals": intervals, "interpretation": "Low-level audio candidates only. Listen before removing pauses, breaths, room tone, or music."}
    if args.output:
        destination = output_guard(args.output, [path], args.force, {".json"})
        save_json(destination, report, args.force)
    emit(report)
    return 0


def cmd_validate(args):
    timeline = load_timeline(args.timeline, find_tool("ffprobe", args.ffprobe))
    report = timeline_report(timeline)
    if args.report:
        destination = output_guard(args.report, timeline["sources"], args.force, {".json"})
        save_json(destination, report, args.force)
    emit(report)
    return 0


def cmd_xml(args):
    ffprobe = find_tool("ffprobe", args.ffprobe)
    timeline = load_timeline(args.timeline, ffprobe)
    output = output_guard(args.output, timeline["sources"], args.force, {".xml"})
    report_path = output_guard(args.report or output.with_suffix(".validation.json"), timeline["sources"] + [output], args.force, {".json"})
    assets_dir = output.with_suffix(".assets")
    native_possible = native_transitions_possible(timeline)
    selected_mode = args.transition_mode
    if selected_mode == "auto":
        selected_mode = "native" if native_possible else "bake"
    if selected_mode == "native" and not native_possible:
        fail("This timeline requires local baking for faithful transition/audio handoff; use --transition-mode auto or bake.")
    resolved_overlays = overlay_assets(timeline, assets_dir, args.force)
    xml_timeline = dict(timeline, resolved_overlays=resolved_overlays)
    baked_assets = []
    if selected_mode == "bake" and any(c.get("transition_in") for c in timeline["clips"]):
        xml_timeline, baked_assets = bake_transitions(xml_timeline, assets_dir, find_tool("ffmpeg", args.ffmpeg), ffprobe, args.force, args.timeout)
    xml, warnings, xml_timing = make_xml(xml_timeline)
    report = timeline_report(timeline)
    report.update({"output": str(output), "format": "FCP7 XML / xmeml version 4", "xml_parse_passed": True, "premiere_import_verified": False, "xml_clip_timing": xml_timing, "report": str(report_path)})
    report.update({"transition_handoff": selected_mode, "baked_transition_assets": baked_assets, "text_assets": resolved_overlays, "assets_directory": str(assets_dir) if resolved_overlays or baked_assets else None})
    report["warnings"] += warnings + ["XML is an interchange sequence, not a .prproj. Import it in Premiere, check linked media/timing/channels/gain, then save a native Premiere project.", "Text layers are transparent PNG assets with a JSON/SRT regeneration source, not native editable Premiere text. Legacy title/identity fields are report metadata only."]
    if baked_assets:
        report["warnings"].append("Transitions are rendered locally as short video/audio segments. Original shot bodies remain separate source clips, but a baked transition's internal effect is not a native editable Premiere transition.")
    atomic_write(output, xml, args.force)
    save_json(report_path, report, args.force)
    emit(report)
    return 0


def native_transitions_possible(timeline):
    for index, clip in enumerate(timeline["clips"]):
        transition = clip.get("transition_in")
        if not transition:
            continue
        if transition["type"] != "dissolve":
            return False
        if transition["audio"] == "crossfade":
            def channels(c):
                return int(c["media"]["audio_streams"][0].get("channels") or 1) if c["audio"] and c["media"]["audio_streams"] else 0
            if channels(timeline["clips"][index - 1]) != channels(clip):
                return False
    return True


def execute_composition(timeline, ffmpeg, encoder, work_dir, destination, overlays, timeout):
    if timeline["schema_version"] == 2 or overlays or any(c.get("transition_in") for c in timeline["clips"]):
        commands, auxiliary, content = extended_render_commands(timeline, ffmpeg, encoder, work_dir, destination, overlays)
    else:
        commands, auxiliary, content = render_commands(timeline, ffmpeg, encoder, work_dir, destination)
    with auxiliary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    for command in commands:
        run(command, timeout=timeout)


def bake_transitions(timeline, assets_dir, ffmpeg, ffprobe, force, timeout):
    encoder = choose_encoder(ffmpeg, "auto", timeline["project"])
    fps = timeline["fps_fraction"]
    source_clips, baked, replacement = timeline["clips"], [], []
    cursor = 0
    for index, original in enumerate(source_clips):
        incoming = original.get("transition_in")
        outgoing = source_clips[index + 1].get("transition_in") if index + 1 < len(source_clips) else None
        in_frames = incoming["frames"] if incoming else 0
        out_frames = outgoing["frames"] if outgoing else 0
        frames = original["frames"] - in_frames - out_frames
        clip = dict(original, transition_in=None, frames=frames, duration_seconds=float(Fraction(frames, 1) / fps), start_frame=cursor, end_frame=cursor + frames)
        if clip.get("kind") == "image":
            clip["in"], clip["out"] = 0, clip["duration_seconds"]
        else:
            clip["in"] = original["in"] + float(Fraction(in_frames, 1) / fps)
            clip["out"] = original["out"] - float(Fraction(out_frames, 1) / fps)
        replacement.append(clip)
        cursor += frames
        if not outgoing:
            continue
        target = output_guard(assets_dir / f"transition-{index + 1:04d}.mp4", timeline["sources"], force, {".mp4"})
        target.parent.mkdir(parents=True, exist_ok=True)
        duration, transition_frames = outgoing["duration_seconds"], outgoing["frames"]
        first = dict(original, transition_in=None, start_frame=0, end_frame=transition_frames, frames=transition_frames, duration_seconds=duration)
        first["in"], first["out"] = (0, duration) if first.get("kind") == "image" else (original["out"] - duration, original["out"])
        second_original = source_clips[index + 1]
        second = dict(second_original, start_frame=0, end_frame=transition_frames, frames=transition_frames, duration_seconds=duration)
        second["in"], second["out"] = (0, duration) if second.get("kind") == "image" else (second_original["in"], second_original["in"] + duration)
        pair = dict(timeline, schema_version=2, clips=[first, second], total_frames=transition_frames, duration_seconds=duration, music=None, overlay_events=[], resolved_overlays=[])
        with tempfile.TemporaryDirectory(prefix=".shadow-transition-", dir=str(assets_dir)) as temporary:
            working = Path(temporary)
            staging = working / "transition.mp4"
            execute_composition(pair, ffmpeg, encoder, working, staging, [], timeout)
            verification = verify_render(staging, pair, ffmpeg, ffprobe, timeout)
            output_guard(target, timeline["sources"], force, {".mp4"})
            os.replace(staging, target)
        media = probe(target, ffprobe)
        replacement.append({"source": str(target), "in": 0, "out": duration, "label": f"Baked {outgoing['type']} {index+1}", "audio": True, "kind": "video", "gain_db": 0, "transition_in": None, "frames": transition_frames, "duration_seconds": duration, "start_frame": cursor, "end_frame": cursor + transition_frames, "media": media})
        baked.append({"path": str(target), "type": outgoing["type"], "sequence_start_frame": cursor, "sequence_end_frame": cursor + transition_frames, "full_decode_passed": verification["full_decode_passed"]})
        cursor += transition_frames
    if cursor != timeline["total_frames"]:
        fail("Internal transition handoff frame count mismatch.", code=3)
    return dict(timeline, clips=replacement), baked


def cmd_render(args):
    ffmpeg, ffprobe = find_tool("ffmpeg", args.ffmpeg), find_tool("ffprobe", args.ffprobe)
    timeline = load_timeline(args.timeline, ffprobe)
    output = output_guard(args.output, timeline["sources"], args.force, {".mp4", ".mov", ".mkv"})
    report_path = output_guard(args.report or output.with_suffix(".render-report.json"), timeline["sources"] + [output], args.force, {".json"})
    encoder = choose_encoder(ffmpeg, args.video_codec, timeline["project"])
    report = timeline_report(timeline)
    report.update({"output": str(output), "report": str(report_path), "video_encoder": encoder, "dry_run": args.dry_run})
    if encoder == "mpeg4":
        report["warnings"].append("Software libx264 is unavailable or mpeg4 was selected. Preview uses MPEG-4 Part 2; some web players prefer H.264. The actual codec is reported after rendering.")
    assets_dir = output.with_suffix(".assets")
    overlays = overlay_assets(timeline, assets_dir, args.force, args.dry_run)
    report.update({"text_assets": overlays, "assets_directory": str(assets_dir) if overlays else None})
    if args.dry_run:
        work_dir = output.parent / ".shadow-dry-run-work"
        build = extended_render_commands if timeline["schema_version"] == 2 else render_commands
        parameters = (timeline, ffmpeg, encoder, work_dir, work_dir / ("preview" + output.suffix))
        commands, auxiliary, content = build(*parameters, overlays) if build is extended_render_commands else build(*parameters)
        report.update({"commands": commands, "auxiliary_file": {"path": str(auxiliary), "content": content}, "files_written": False, "render_verified": False})
        emit(report)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".shadow-render-", dir=str(output.parent)) as temporary:
        work_dir = Path(temporary)
        temporary_output = work_dir / ("preview" + output.suffix)
        execute_composition(timeline, ffmpeg, encoder, work_dir, temporary_output, overlays, args.timeout)
        verification = verify_render(temporary_output, timeline, ffmpeg, ffprobe, args.timeout)
        verification["actual_media"]["path"] = str(output)
        # Recheck before commit. The destination is never a timeline/source path.
        output_guard(output, timeline["sources"], args.force, {".mp4", ".mov", ".mkv"})
        os.replace(str(temporary_output), str(output))
    report.update({"render_verified": True, "verification": verification})
    save_json(report_path, report, args.force)
    emit(report)
    return 0


class JSONParser(argparse.ArgumentParser):
    def error(self, message):
        fail("Invalid CLI arguments.", {"reason": message, "help": "Run shadow.py --help or shadow.py COMMAND --help."})


def parser():
    p = JSONParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    subs = p.add_subparsers(dest="command", required=True, parser_class=JSONParser)
    def common(name, description, ffmpeg=False):
        s = subs.add_parser(name, help=description, description=description)
        s.add_argument("--ffprobe", help="Explicit ffprobe executable (otherwise SHADOW_FFPROBE / PATH / known local paths).")
        if ffmpeg:
            s.add_argument("--ffmpeg", help="Explicit ffmpeg executable (otherwise SHADOW_FFMPEG / PATH / known local paths).")
        return s
    s = common("doctor", "Read-only dependency discovery; no installation/configuration changes.", True)
    s.set_defaults(handler=cmd_doctor)
    s = common("inspect", "Inspect only supplied media paths/directories; JSON summary, optional SHA-256.")
    s.add_argument("paths", nargs="+")
    s.add_argument("--recursive", action="store_true", help="Recurse only inside the supplied directories.")
    s.add_argument("--max-files", type=int, default=1000)
    s.add_argument("--sha256", action="store_true")
    s.add_argument("--output", help="Optional .json report.")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=cmd_inspect)
    s = common("scenes", "Find pixel-change candidates, without semantic scene claims.", True)
    s.add_argument("source")
    s.add_argument("--threshold", type=float, default=0.30)
    s.add_argument("--min-gap", type=float, default=1.0)
    s.add_argument("--timeout", type=int, default=3600, help="Maximum seconds for this media operation.")
    s.add_argument("--output")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=cmd_scenes)
    s = common("silence", "Find low-level audio candidates; listen before removing anything.", True)
    s.add_argument("source")
    s.add_argument("--noise-db", type=float, default=-35)
    s.add_argument("--min-duration", type=float, default=0.4)
    s.add_argument("--timeout", type=int, default=3600)
    s.add_argument("--output")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=cmd_silence)
    s = common("validate", "Validate schema, files, source ranges and sequence frame quantization.")
    s.add_argument("timeline")
    s.add_argument("--report", help="Optional .json report.")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=cmd_validate)
    s = common("xml", "Export editable FCP7 XML, timed text assets and faithful transition handoff.", True)
    s.add_argument("timeline")
    s.add_argument("--output", required=True, help="Generated .xml sequence.")
    s.add_argument("--report", help="Defaults to OUTPUT.validation.json.")
    s.add_argument("--force", action="store_true", help="Replace generated output files; source replacement is always refused.")
    s.add_argument("--transition-mode", choices=("auto", "native", "bake"), default="auto", help="auto uses native dissolve where reliable; otherwise renders short transition segments. native refuses unsupported mappings.")
    s.add_argument("--timeout", type=int, default=3600)
    s.set_defaults(handler=cmd_xml)
    s = common("render", "Render cuts, natural transitions, still images and timed text, then probe and fully decode.", True)
    s.add_argument("timeline")
    s.add_argument("--output", required=True, help="Generated .mp4 / .mov / .mkv preview.")
    s.add_argument("--report", help="Defaults to OUTPUT.render-report.json.")
    s.add_argument("--video-codec", choices=("auto", "libx264", "h264_nvenc", "mpeg4"), default="auto")
    s.add_argument("--dry-run", action="store_true", help="Validate and print argv arrays; write no files and run no encode.")
    s.add_argument("--timeout", type=int, default=3600, help="Maximum seconds for each encode/decode command.")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=cmd_render)
    return p


def main():
    try:
        args = parser().parse_args()
        if hasattr(args, "timeout") and args.timeout <= 0:
            fail("timeout must be positive.")
        if hasattr(args, "max_files") and args.max_files <= 0:
            fail("max-files must be positive.")
        return args.handler(args)
    except ShadowError as exc:
        report = {"ok": False, "error": str(exc), "exit_code": exc.code}
        if exc.details is not None:
            report["details"] = exc.details
        emit(report)
        return exc.code
    except (OSError, UnicodeError, ValueError) as exc:
        emit({"ok": False, "error": "File/media operation failed.", "details": {"reason": str(exc)}, "exit_code": 4})
        return 4
    except KeyboardInterrupt:
        emit({"ok": False, "error": "Interrupted; source files were not modified.", "exit_code": 130})
        return 130


if __name__ == "__main__":
    sys.exit(main())
