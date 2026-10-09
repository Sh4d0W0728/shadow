#!/usr/bin/env python3
"""Local, timestamp-anchored ASR/SRT tools. Optional faster-whisper, otherwise stdlib."""
import argparse
import ctypes
import difflib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

VERSION = "2.2.0"


class SubtitleError(Exception):
    pass


def fail(message):
    raise SubtitleError(message)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def path(value, existing=True):
    p = Path(value).expanduser().resolve()
    if existing and not p.is_file():
        fail(f"Input file does not exist: {p}")
    return p


def guard(destination, sources, force, suffix):
    p = path(destination, False)
    if p.suffix.lower() != suffix:
        fail(f"Output must have the {suffix} extension: {p}")
    if os.path.normcase(str(p)) in {os.path.normcase(str(s)) for s in sources}:
        fail(f"Refusing to overwrite an input or another output: {p}")
    if p.exists() and not force:
        fail(f"Output exists; use --force only for generated outputs: {p}")
    return p


def write_text(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", prefix=".shadow-subtitles-", dir=p.parent, delete=False) as f:
        temp = Path(f.name)
        f.write(text)
    try:
        os.replace(temp, p)
    finally:
        if temp.exists():
            temp.unlink()


def write_json(p, value):
    write_text(p, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def finite(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        fail(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        fail(f"{name} must be at least {minimum}")
    return float(value)


def ms(seconds):
    return int(math.floor(seconds * 1000 + 0.5))


def time_srt(value):
    value = int(value)
    return f"{value // 3600000:02d}:{value // 60000 % 60:02d}:{value // 1000 % 60:02d},{value % 1000:03d}"


def parse_srt_time(value):
    match = re.fullmatch(r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        fail(f"Invalid SRT time: {value}")
    h, m, s, milli = map(int, match.groups())
    if m > 59 or s > 59:
        fail(f"Invalid SRT minute/second: {value}")
    return ((h * 60 + m) * 60 + s) * 1000 + milli


def validate_cues(cues, allow_overlap=False):
    if not cues:
        fail("No timed subtitle cues were produced")
    previous_start, previous_end = -1, -1
    warnings = []
    for i, cue in enumerate(cues, 1):
        if type(cue["start_ms"]) is not int or type(cue["end_ms"]) is not int:
            fail(f"Cue {i} timestamps must be integer milliseconds")
        if cue["start_ms"] < 0 or cue["end_ms"] <= cue["start_ms"]:
            fail(f"Cue {i} has a negative or empty time range")
        if not isinstance(cue["text"], str) or not cue["text"].strip() or "\x00" in cue["text"]:
            fail(f"Cue {i} text is empty or contains NUL")
        if cue["start_ms"] < previous_start:
            fail(f"Cue {i} is out of chronological order")
        if cue["start_ms"] < previous_end:
            if not allow_overlap:
                fail(f"Cue {i} overlaps the preceding cue; review timestamps or explicitly allow overlap")
            warnings.append(f"Cue {i} overlaps the preceding cue")
        previous_start, previous_end = cue["start_ms"], cue["end_ms"]
    return warnings


def read_srt(source, allow_overlap=False):
    raw = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.splitlines()
        if len(lines) < 3 or not re.fullmatch(r"\d+", lines[0].strip()):
            fail("SRT blocks require an integer index, time range and text")
        pair = re.fullmatch(r"(.+?)\s*-->\s*(.+?)", lines[1].strip())
        if not pair:
            fail(f"Invalid SRT time range in block {lines[0]}")
        cues.append({"start_ms": parse_srt_time(pair[1]), "end_ms": parse_srt_time(pair[2]), "text": "\n".join(lines[2:]).strip()})
    return cues, validate_cues(cues, allow_overlap)


def srt_text(cues):
    return "\n\n".join(f"{i}\n{time_srt(c['start_ms'])} --> {time_srt(c['end_ms'])}\n{c['text']}" for i, c in enumerate(cues, 1)) + "\n"


def display_len(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def chinese_script_form(text):
    """Script equivalence for alignment only; never substitute pronunciation or words."""
    if sys.platform == "win32" and text:
        mapper = ctypes.windll.kernel32.LCMapStringEx
        mapper.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        mapper.restype = ctypes.c_int
        required = mapper("zh-CN", 0x02000000, text, -1, None, 0, None, None, None)
        if required:
            output = ctypes.create_unicode_buffer(required)
            if mapper("zh-CN", 0x02000000, text, -1, output, required, None, None, None):
                mapped = output.value
                return mapped if len(mapped) == len(text) else text
    return text


def normalize(text, chinese_equivalence=False):
    if chinese_equivalence:
        text = chinese_script_form(text)
    chars, positions = [], []
    for i, c in enumerate(text):
        if c.isalnum():
            for lowered in c.casefold():
                chars.append(lowered)
                positions.append(i)
    return "".join(chars), positions


def script_fragment(text, positions, start, end):
    a, b = positions[start], positions[end - 1] + 1
    while b < len(text) and not text[b].isalnum() and text[b] not in "\r\n":
        b += 1
    return text[a:b].strip()


def critical_terms(text):
    english = re.findall(r"\b(?:no|not|never|none|without|cannot|can't|don't|didn't|isn't|won't)\b", text.casefold())
    chinese = [c for c in text if c in "不没无未否"]
    numbers = re.findall(r"\d+(?:[.,]\d+)*", text)
    return {"negation": english + chinese, "numbers": numbers}


def manuscript_candidate(recognized, reference, ref_normalized, positions, cursor, chinese_equivalence=False):
    norm, _ = normalize(recognized, chinese_equivalence)
    if not norm or cursor >= len(ref_normalized):
        return None
    tail = ref_normalized[cursor:cursor + max(2000, len(norm) * 8)]
    exact = tail.find(norm)
    if exact >= 0:
        start, end = cursor + exact, cursor + exact + len(norm)
        return {"text": script_fragment(reference, positions, start, end), "similarity": 1.0, "start": start, "end": end}
    matcher = difflib.SequenceMatcher(None, norm, tail, autojunk=False)
    blocks = sorted(matcher.get_matching_blocks(), key=lambda b: -b.size)[:4]
    candidates = set()
    wiggle = max(3, min(16, math.ceil(len(norm) * 0.15)))
    for block in blocks:
        if block.size < 2:
            continue
        nominal_start = max(0, block.b - block.a)
        for offset in range(-2, 3):
            a = max(0, nominal_start + offset)
            for length in range(max(1, len(norm) - wiggle), len(norm) + wiggle + 1):
                b = min(len(tail), a + length)
                if b > a:
                    candidates.add((a, b))
    if not candidates:
        return None
    score, a, b = max((difflib.SequenceMatcher(None, norm, tail[a:b], autojunk=False).ratio(), -a, b) for a, b in candidates)
    a = -a
    start, end = cursor + a, cursor + b
    return {"text": script_fragment(reference, positions, start, end), "similarity": score, "start": start, "end": end}


def assist_manuscript(cues, reference, lexical=False, chinese_equivalence=False):
    ref_normalized, positions = normalize(reference, chinese_equivalence)
    if not ref_normalized:
        fail("Manuscript contains no readable text")
    cursor, details = 0, []
    for i, cue in enumerate(cues, 1):
        original = cue["text"]
        candidate = manuscript_candidate(original, reference, ref_normalized, positions, cursor, chinese_equivalence)
        detail = {"cue": i, "start_ms": cue["start_ms"], "end_ms": cue["end_ms"], "recognized_text": original, "subtitle_text": original, "mean_word_probability": cue.get("mean_word_probability"), "timing_anchor": cue["timing_anchor"]}
        if candidate is None:
            detail.update({"status": "unmatched_keep_recognition", "alignment_similarity": 0.0})
        else:
            score = candidate["similarity"]
            detail.update({"manuscript_candidate": candidate["text"], "alignment_similarity": round(score, 6), "manuscript_normalized_range": [candidate["start"], candidate["end"]]})
            if candidate["start"] > cursor:
                detail["skipped_manuscript_text"] = script_fragment(reference, positions, cursor, candidate["start"])
            norm, _ = normalize(original, chinese_equivalence)
            candidate_norm, _ = normalize(candidate["text"], chinese_equivalence)
            if norm == candidate_norm:
                cue["text"] = candidate["text"]
                detail["status"] = "punctuation_case_or_script_variant_corrected" if cue["text"] != original else "exact_match"
                cursor = candidate["end"]
            elif lexical and score >= 0.94 and critical_terms(original) == critical_terms(candidate["text"]):
                cue["text"] = candidate["text"]
                detail["status"] = "high_similarity_lexical_correction_review_required"
                cursor = candidate["end"]
            else:
                detail["status"] = "mismatch_keep_recognition_review_required"
                if score >= 0.65:
                    cursor = candidate["end"]
            detail["subtitle_text"] = cue["text"]
        details.append(detail)
    return {"method": "monotonic_text_alignment_to_recognition_cues", "timestamps_from_manuscript": False, "lexical_corrections_enabled": lexical, "chinese_script_equivalence_requested": chinese_equivalence, "chinese_script_equivalence_backend": "Windows LCMapStringEx" if chinese_equivalence and sys.platform == "win32" else None, "cues": details, "review_required_count": sum("review_required" in d["status"] or "unmatched" in d["status"] for d in details), "unmatched_manuscript_tail": script_fragment(reference, positions, cursor, len(positions)) if cursor < len(positions) else ""}


def normalize_segments(data):
    if isinstance(data, list):
        segments = data
    elif isinstance(data, dict) and isinstance(data.get("segments"), list):
        segments = data["segments"]
    else:
        fail("Segments JSON must be a Whisper-style segment list or an object containing segments")
    if not segments:
        fail("Recognition data contains no segments")
    normalized = []
    for i, item in enumerate(segments):
        if not isinstance(item, dict):
            fail(f"Segment {i} must be an object")
        start, end = finite(item.get("start"), f"segment {i}.start", 0), finite(item.get("end"), f"segment {i}.end", 0)
        text = item.get("text")
        if end <= start or not isinstance(text, str) or not text.strip():
            fail(f"Segment {i} requires nonempty text and end > start")
        words = []
        word_items = item.get("words") or []
        if not isinstance(word_items, list):
            fail(f"Segment {i}.words must be an array")
        for j, word in enumerate(word_items):
            if not isinstance(word, dict):
                fail(f"Word {i}:{j} must be an object")
            a, b = finite(word.get("start"), f"word {i}:{j}.start", 0), finite(word.get("end"), f"word {i}:{j}.end", 0)
            t = word.get("word", word.get("text"))
            if b < a or a < start - 0.02 or b > end + 0.02 or not isinstance(t, str) or not t:
                fail(f"Word {i}:{j} has invalid text or lies outside its recognition segment")
            p = word.get("probability")
            if p is not None:
                p = finite(p, f"word {i}:{j}.probability", 0)
                if p > 1:
                    fail("Word probability cannot exceed 1")
            words.append({"start": a, "end": b, "word": t, "probability": p})
        normalized.append(dict(item, start=start, end=end, text=text.strip(), words=words))
    return normalized


def build_cues(segments, max_chars, max_duration):
    cues = []
    for segment_index, segment in enumerate(segments):
        words = segment["words"]
        if not words:
            cues.append({"start_ms": ms(segment["start"]), "end_ms": ms(segment["end"]), "text": segment["text"], "timing_anchor": "recognized_segment", "segment_index": segment_index, "mean_word_probability": None})
            continue
        groups, current = [], []
        for word in words:
            proposed = "".join(w["word"] for w in current + [word]).strip()
            if current and (display_len(proposed) > max_chars or word["end"] - current[0]["start"] > max_duration or word["start"] - current[-1]["end"] > 0.8):
                groups.append(current)
                current = []
            current.append(word)
            if re.search(r"[。！？!?]$", word["word"].strip()):
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        for group in groups:
            probabilities = [w["probability"] for w in group if w["probability"] is not None]
            start, end = ms(group[0]["start"]), ms(max(w["end"] for w in group))
            if end <= start:
                fail("Recognition produced a zero-duration word group; inspect original timestamps rather than invent timing")
            cues.append({"start_ms": start, "end_ms": end, "text": "".join(w["word"] for w in group).strip(), "timing_anchor": "recognized_words", "segment_index": segment_index, "mean_word_probability": sum(probabilities) / len(probabilities) if probabilities else None})
    return cues


def get_outputs(args, sources, segments=False):
    output = guard(args.output, sources, args.force, ".srt")
    report = guard(args.report or output.with_suffix(".subtitle-report.json"), sources + [output], args.force, ".json")
    segment_output = None
    if segments:
        segment_output = guard(args.segments or output.with_suffix(".segments.json"), sources + [output, report], args.force, ".json")
    return output, report, segment_output


def finish_recognition(args, data, outputs, recognition_ran, source):
    output, report_path, segments_path = outputs
    segments = normalize_segments(data)
    cues = build_cues(segments, args.max_chars, args.max_duration)
    report = {"ok": True, "version": VERSION, "source": str(source), "output": str(output), "cue_count": len(cues), "recognition_ran_in_this_command": recognition_ran, "timing_method": "actual_recognition_word_or_segment_timestamps", "duration_end_seconds": max(c["end_ms"] for c in cues) / 1000, "backend": data.get("backend") if isinstance(data, dict) else None, "language": data.get("language") if isinstance(data, dict) else None, "confidence_note": "Word probabilities are backend scores, not calibrated accuracy; text-alignment similarity is a separate score.", "warnings": []}
    if args.manuscript:
        manuscript = path(args.manuscript)
        report["manuscript"] = str(manuscript)
        chinese_equivalence = args.chinese_script_equivalence or (isinstance(data, dict) and data.get("language") == "zh")
        report["manuscript_assistance"] = assist_manuscript(cues, manuscript.read_text(encoding="utf-8-sig"), args.allow_lexical_corrections, chinese_equivalence)
    else:
        report["recognized_cues"] = cues
    report["warnings"] += validate_cues(cues, args.allow_overlap)
    if any(c["timing_anchor"] == "recognized_segment" and (display_len(c["text"]) > args.max_chars or (c["end_ms"] - c["start_ms"]) / 1000 > args.max_duration) for c in cues):
        report["warnings"].append("Some long cues have segment timestamps only; they are kept intact. Supply word timestamps or review them in Premiere; no character-based timing was fabricated.")
    report["report"] = str(report_path)
    if segments_path:
        raw = dict(data, segments=segments) if isinstance(data, dict) else {"segments": segments}
        write_json(segments_path, raw)
        report["segments_output"] = str(segments_path)
    write_text(output, srt_text(cues))
    write_json(report_path, report)
    emit(report)


def cmd_transcribe(args):
    audio = path(args.audio)
    manuscript = path(args.manuscript) if args.manuscript else None
    outputs = get_outputs(args, [audio] + ([manuscript] if manuscript else []), True)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        fail("Local ASR backend unavailable: install faster-whisper into a task-local Python venv and invoke this script with that venv's Python. No package is installed by this script. Details: " + str(exc))
    download_root = path(args.download_root, False) if args.download_root else outputs[0].parent / ".shadow-asr-models"
    model = args.model
    if args.model_dir:
        model_path = Path(args.model_dir).expanduser().resolve()
        if not model_path.is_dir() or not (model_path / "model.bin").is_file():
            fail(f"--model-dir must be a local CTranslate2 Whisper model directory containing model.bin: {model_path}")
        model = str(model_path)
    try:
        engine = WhisperModel(model, device=args.device, compute_type=args.compute_type, download_root=str(download_root), local_files_only=args.local_files_only)
        recognized, info = engine.transcribe(str(audio), language=args.language, beam_size=args.beam_size, word_timestamps=True, vad_filter=args.vad_filter)
        segments = []
        for segment in recognized:
            segments.append({"id": segment.id, "start": segment.start, "end": segment.end, "text": segment.text, "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob, "compression_ratio": segment.compression_ratio, "words": [{"start": word.start, "end": word.end, "word": word.word, "probability": word.probability} for word in segment.words or []]})
    except Exception as exc:
        fail(f"Local ASR failed during model loading/decoding: {type(exc).__name__}: {exc}")
    data = {"schema_version": 1, "backend": "faster-whisper", "model": model, "device": args.device, "compute_type": args.compute_type, "audio_source": str(audio), "language": info.language, "language_probability": info.language_probability, "duration_seconds": info.duration, "recognition_ran_in_this_command": True, "segments": segments}
    finish_recognition(args, data, outputs, True, audio)


def cmd_from_segments(args):
    source = path(args.source)
    manuscript = path(args.manuscript) if args.manuscript else None
    outputs = get_outputs(args, [source] + ([manuscript] if manuscript else []))
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    finish_recognition(args, data, outputs, False, source)


def cmd_validate(args):
    source = path(args.source)
    cues, warnings = read_srt(source, args.allow_overlap)
    emit({"ok": True, "source": str(source), "cue_count": len(cues), "first_start_seconds": cues[0]["start_ms"] / 1000, "last_end_seconds": cues[-1]["end_ms"] / 1000, "utf8_srt_parse_passed": True, "warnings": warnings})


def cmd_shift(args):
    source = path(args.source)
    output = guard(args.output, [source], args.force, ".srt")
    cues, warnings = read_srt(source, args.allow_overlap)
    shift = ms(finite(args.seconds, "seconds"))
    for cue in cues:
        cue["start_ms"] += shift
        cue["end_ms"] += shift
    validate_cues(cues, args.allow_overlap)
    write_text(output, srt_text(cues))
    emit({"ok": True, "source": str(source), "output": str(output), "shift_ms": shift, "cue_count": len(cues), "warnings": warnings})


def time_ass(milliseconds):
    centi = int(math.floor(milliseconds / 10 + 0.5))
    return f"{centi // 360000}:{centi // 6000 % 60:02d}:{centi // 100 % 60:02d}.{centi % 100:02d}"


def cmd_ass(args):
    source = path(args.source)
    output = guard(args.output, [source], args.force, ".ass")
    cues, warnings = read_srt(source, args.allow_overlap)
    if not args.font.strip() or any(c in args.font for c in ",\r\n"):
        fail("ASS font name cannot be empty or contain comma/newline")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.color):
        fail("ASS --color must be #RRGGBB")
    if args.width < 16 or args.height < 16 or not 4 <= args.font_size <= 300:
        fail("ASS dimensions/font size are outside the supported range")
    rgb = args.color[1:]
    color = f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"
    alignment = {"top": 8, "center": 5, "bottom": 2}[args.position]
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {args.width}", f"PlayResY: {args.height}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding", f"Style: Default,{args.font},{args.font_size},{color},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,{alignment},64,64,64,1", "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for cue in cues:
        a, b = time_ass(cue["start_ms"]), time_ass(cue["end_ms"])
        if a == b:
            fail("A cue is shorter than ASS centisecond precision; adjust it explicitly before conversion")
        text = cue["text"].replace("\\", "\\\\").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{a},{b},Default,,0,0,0,,{text}")
    write_text(output, "\n".join(lines) + "\n")
    emit({"ok": True, "source": str(source), "output": str(output), "cue_count": len(cues), "font": args.font, "size": [args.width, args.height], "position": args.position, "timing_quantization": "nearest 10 ms for ASS; SRT retained separately", "editable_subtitles": "Import the SRT into Premiere as captions. ASS is a styled preview exchange, not an editable Premiere title/MOGRT.", "warnings": warnings})


def recognition_options(parser):
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", help="Defaults to OUTPUT.subtitle-report.json")
    parser.add_argument("--manuscript", help="UTF-8 plain text reference; never supplies fabricated timestamps")
    parser.add_argument("--allow-lexical-corrections", action="store_true", help="Permit >=0.94 similarity corrections except changed numbers/negation; every lexical change still requires review")
    parser.add_argument("--chinese-script-equivalence", action="store_true", help="Treat simplified/traditional character variants as equivalent using Windows NLS; also selected automatically for ASR language zh")
    parser.add_argument("--max-chars", type=int, default=42, help="Max display units per word-timestamp cue (CJK counts as 2)")
    parser.add_argument("--max-duration", type=float, default=6.0)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--force", action="store_true")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("transcribe", help="Recognize real local audio with optional faster-whisper")
    t.add_argument("audio")
    recognition_options(t)
    t.add_argument("--segments", help="Raw timed recognition data; defaults to OUTPUT.segments.json")
    t.add_argument("--model", choices=("tiny", "base", "small"), default="tiny")
    t.add_argument("--model-dir", help="Existing local CTranslate2 Whisper model directory")
    t.add_argument("--download-root", help="Task-local cache; default OUTPUT parent/.shadow-asr-models")
    t.add_argument("--local-files-only", action="store_true")
    t.add_argument("--language", help="Whisper language code, e.g. zh/en; omitted means detection")
    t.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    t.add_argument("--compute-type", choices=("int8", "float32", "float16", "int8_float16"), default="int8")
    t.add_argument("--beam-size", type=int, default=5)
    t.add_argument("--vad-filter", action="store_true")
    t.set_defaults(func=cmd_transcribe)
    t = sub.add_parser("from-segments", help="Use existing Whisper-format timestamps; this action runs no recognition")
    t.add_argument("source")
    recognition_options(t)
    t.set_defaults(func=cmd_from_segments)
    t = sub.add_parser("validate", help="Parse and validate UTF-8 SRT")
    t.add_argument("source")
    t.add_argument("--allow-overlap", action="store_true")
    t.set_defaults(func=cmd_validate)
    t = sub.add_parser("shift", help="Shift all existing cues by an explicit number of seconds")
    t.add_argument("source")
    t.add_argument("--seconds", type=float, required=True)
    t.add_argument("--output", required=True)
    t.add_argument("--allow-overlap", action="store_true")
    t.add_argument("--force", action="store_true")
    t.set_defaults(func=cmd_shift)
    t = sub.add_parser("ass", help="Generate a basic styled ASS preview; retain SRT for Premiere captions")
    t.add_argument("source")
    t.add_argument("--output", required=True)
    t.add_argument("--font", default="Microsoft YaHei")
    t.add_argument("--font-size", type=int, default=48)
    t.add_argument("--width", type=int, default=1920)
    t.add_argument("--height", type=int, default=1080)
    t.add_argument("--color", default="#FFFFFF")
    t.add_argument("--position", choices=("top", "center", "bottom"), default="bottom")
    t.add_argument("--allow-overlap", action="store_true")
    t.add_argument("--force", action="store_true")
    t.set_defaults(func=cmd_ass)
    args = p.parse_args()
    if hasattr(args, "max_chars") and not 4 <= args.max_chars <= 200:
        fail("--max-chars must be between 4 and 200")
    if hasattr(args, "max_duration") and (not math.isfinite(args.max_duration) or args.max_duration <= 0):
        fail("--max-duration must be finite and positive")
    if hasattr(args, "beam_size") and not 1 <= args.beam_size <= 20:
        fail("--beam-size must be 1 to 20")
    args.func(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SubtitleError, OSError, ValueError, KeyError, TypeError) as exc:
        emit({"ok": False, "error": str(exc), "stage": "subtitle_processing"})
        raise SystemExit(2)
