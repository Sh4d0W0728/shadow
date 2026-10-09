#!/usr/bin/env python3
"""Run a chosen shadow timeline through rough cut, optional ASR, final video and PR XML.

This orchestrates existing local modules; media selection and editorial review remain
explicit. Outputs go to a new directory, and failure always retains a stage report.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def normalize_timeline(source):
    data = json.loads(source.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
    if not isinstance(data, dict):
        raise ValueError("Timeline root must be a JSON object")
    def resolve(item):
        if isinstance(item, dict) and "source" in item:
            if not isinstance(item["source"], str) or not item["source"].strip():
                raise ValueError("Media source must be a non-empty path string")
            p = Path(item["source"]).expanduser()
            item["source"] = str((p if p.is_absolute() else source.parent / p).resolve())
    for clip in data.get("clips", []):
        resolve(clip)
    resolve(data.get("music"))
    resolve(data.get("subtitles"))
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("timeline")
    p.add_argument("--output-dir", required=True, help="A NEW directory; existing paths are refused")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--transcribe", action="store_true", help="Recognize audio from the rendered rough cut")
    mode.add_argument("--srt", help="Existing SRT already aligned to this final sequence")
    p.add_argument("--manuscript", help="UTF-8 script reference, requires --transcribe")
    p.add_argument("--asr-python", default=sys.executable, help="Python in a local faster-whisper environment")
    p.add_argument("--model", choices=("tiny", "base", "small"), default="tiny")
    p.add_argument("--model-dir", help="Existing local CTranslate2 model")
    p.add_argument("--language", default="zh")
    p.add_argument("--ffmpeg")
    p.add_argument("--ffprobe")
    p.add_argument("--timeout", type=int, default=3600, help="Timeout seconds PER stage")
    args = p.parse_args()
    if args.manuscript and not args.transcribe:
        p.error("--manuscript requires --transcribe; it cannot supply artificial timing")
    if args.timeout <= 0:
        p.error("--timeout must be positive")
    source = Path(args.timeline).expanduser().resolve(strict=True)
    data = normalize_timeline(source)
    if data.get("subtitles") and (args.srt or args.transcribe):
        p.error("Timeline already contains subtitles. Remove them explicitly before choosing a new subtitle source")
    out = Path(args.output_dir).expanduser().resolve()
    if out.exists():
        p.error("--output-dir already exists. Use a new version directory to preserve earlier outputs")
    # Read all explicit inputs before creating any output directory.
    srt_input = Path(args.srt).expanduser().resolve(strict=True) if args.srt else None
    manuscript = Path(args.manuscript).expanduser().resolve(strict=True) if args.manuscript else None
    out.mkdir(parents=True, exist_ok=False)
    report_path = out / "pipeline-report.json"
    report = {"schema_version": 1, "tool": "shadow", "ok": False,
              "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "input_timeline": str(source), "identity": data.get("identity", {}),
              "project": data.get("project", {}), "stages": [],
              "premiere_gui_verified": False, "visual_audio_editorial_review_required": True}
    write_json(report_path, report)

    def stage(name, command):
        emit({"stage": name, "status": "running"})
        entry = {"name": name, "command": [str(x) for x in command], "ok": False}
        report["stages"].append(entry)
        write_json(report_path, report)
        log = out / (name + ".log")
        try:
            result = subprocess.run(entry["command"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=args.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            def text(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
            log.write_text(text(getattr(exc, "stdout", None)) + "\n" + text(getattr(exc, "stderr", None)) + "\n" + str(exc), encoding="utf-8")
            entry.update({"error": str(exc), "log": str(log), "timeout": isinstance(exc, subprocess.TimeoutExpired),
                          "ended_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
            write_json(report_path, report)
            raise
        log.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")
        entry.update({"returncode": result.returncode, "log": str(log), "ok": result.returncode == 0,
                      "ended_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        write_json(report_path, report)
        if result.returncode:
            raise RuntimeError(f"Stage {name} failed; see {log}")
        emit({"stage": name, "status": "passed"})
        return result

    def shadow(action, timeline, *extra):
        cmd = [sys.executable, "-X", "utf8", str(SCRIPT_DIR / "shadow.py"), action, str(timeline), *extra]
        if args.ffprobe:
            cmd += ["--ffprobe", args.ffprobe]
        if action in {"render", "xml"} and args.ffmpeg:
            cmd += ["--ffmpeg", args.ffmpeg]
        if action == "render":
            cmd += ["--timeout", str(args.timeout)]
        return cmd

    try:
        timeline = out / "timeline.json"
        write_json(timeline, data)
        stage("01_validate", shadow("validate", timeline, "--report", str(out / "timeline-validation.json")))
        subtitle_file = None
        if args.transcribe:
            rough_data = json.loads(json.dumps(data))
            rough_data.pop("titles", None)
            rough_data.pop("subtitles", None)
            rough = out / "rough-timeline.json"
            write_json(rough, rough_data)
            rough_video = out / "rough.mp4"
            stage("02_rough", shadow("render", rough, "--output", str(rough_video)))
            import shadow as runtime
            ffmpeg = runtime.find_tool("ffmpeg", args.ffmpeg)
            audio = out / "sequence-audio.wav"
            stage("03_sequence_audio", [ffmpeg, "-hide_banner", "-nostdin", "-n", "-i", str(rough_video),
                                        "-map", "0:a:0", "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio)])
            subtitle_file = out / "captions.srt"
            cmd = [args.asr_python, "-X", "utf8", str(SCRIPT_DIR / "subtitles.py"), "transcribe", str(audio),
                   "--output", str(subtitle_file), "--model", args.model, "--language", args.language]
            if args.model_dir:
                cmd += ["--model-dir", str(Path(args.model_dir).resolve()), "--local-files-only"]
            if manuscript:
                cmd += ["--manuscript", str(manuscript)]
            stage("04_transcribe", cmd)
        elif srt_input:
            subtitle_file = out / "captions.srt"
            subtitle_file.write_bytes(srt_input.read_bytes())
        if subtitle_file:
            stage("05_srt_validate", [sys.executable, "-X", "utf8", str(SCRIPT_DIR / "subtitles.py"), "validate", str(subtitle_file)])
            data["subtitles"] = {"source": str(subtitle_file), "font_face": "Microsoft YaHei", "position": "bottom"}
            data["schema_version"] = 2
            write_json(timeline, data)
            stage("05b_caption_timeline", shadow("validate", timeline))
        stage("06_final", shadow("render", timeline, "--output", str(out / "final.mp4")))
        stage("07_premiere_xml", shadow("xml", timeline, "--output", str(out / "premiere.xml")))
        report.update({"ok": True, "final_video": str(out / "final.mp4"), "timeline": str(timeline),
                       "premiere_xml": str(out / "premiere.xml"),
                       "srt": str(subtitle_file) if subtitle_file else (data.get("subtitles") or {}).get("source"),
                       "ended_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        write_json(report_path, report)
        emit({"ok": True, "report": str(report_path), "final_video": report["final_video"],
              "next": "Review cuts, titles, subtitles and audio; import XML and SRT into Premiere and save native project if requested."})
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report.update({"ok": False, "error": str(exc), "ended_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        write_json(report_path, report)
        emit({"ok": False, "error": str(exc), "report": str(report_path)})
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        emit({"ok": False, "error": str(exc), "stage": "input_validation"})
        sys.exit(2)
