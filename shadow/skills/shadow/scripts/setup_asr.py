#!/usr/bin/env python3
"""Explicit task-local ASR setup. No global pip, system env or TLS changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request

REPOSITORY = "Systran/faster-whisper-tiny"
REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
PACKAGE_VERSION = "1.2.1"
FILES = {
    "config.json": {"size": 2249, "git_sha1": "3baa18e2b321a2f489614607852a729fcd516480"},
    "tokenizer.json": {"size": 2203239, "git_sha1": "7818adb6de9fa3064d3ff81226fdd675be1f6344"},
    "vocabulary.txt": {"size": 459861, "git_sha1": "c9074644d9d1205686f16d411564729461324b75"},
    "model.bin": {"size": 75538270, "sha256": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"},
}


def note(message):
    print(message, file=sys.stderr, flush=True)


def run(argv, env, timeout=1800):
    p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {argv[0]}\n{p.stderr[-6000:]}\n{p.stdout[-2000:]}")
    return p.stdout


def file_check(p, metadata):
    if not p.is_file() or p.stat().st_size != metadata["size"]:
        return False
    if "sha256" in metadata:
        digest = hashlib.sha256()
    else:
        digest = hashlib.sha1()
        digest.update(f"blob {metadata['size']}\0".encode("ascii"))
    with p.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == metadata.get("sha256", metadata.get("git_sha1"))


def download(url, temporary, metadata, args, env):
    method, diagnostics = "urllib_https", []
    try:
        handlers = [urllib.request.HTTPSHandler(context=ssl.create_default_context())]
        if args.direct:
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, headers={"User-Agent": "Shadow-ASR-Setup/2.0"})
        with opener.open(request, timeout=30) as response, temporary.open("wb") as output:
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > metadata["size"]:
                    raise RuntimeError("Download exceeded the official pinned file size")
                output.write(block)
        if not file_check(temporary, metadata):
            raise RuntimeError("HTTPS response did not match official pinned size/hash")
    except Exception as exc:
        diagnostics.append(f"Normal HTTPS transport failed: {type(exc).__name__}: {exc}")
        if not args.curl_fallback:
            raise RuntimeError(diagnostics[-1] + "; use --curl-fallback for a TLS-verified official curl transport") from exc
        curl = shutil.which("curl.exe" if os.name == "nt" else "curl")
        if not curl:
            raise RuntimeError(diagnostics[-1] + "; curl fallback is unavailable") from exc
        note(diagnostics[-1])
        argv = [curl, "--fail", "--location", "--silent", "--show-error", "--connect-timeout", "15", "--max-time", "240", "--retry", "1"]
        if args.direct:
            argv += ["--noproxy", "*"]
        argv += [url, "--output", str(temporary)]
        run(argv, env, timeout=520)
        if not file_check(temporary, metadata):
            raise RuntimeError("Curl response did not match official pinned size/hash")
        method = "official_https_curl_fallback"
    return {"method": method, "diagnostics": diagnostics, "tls_verification_enabled": True}


def inspect_tools(directory, env):
    venv = directory / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = {"directory": str(directory), "python": str(python), "python_exists": python.is_file(), "model_dir": str(directory / "models/tiny"), "package_version": None, "venv_prefix_verified": False}
    if python.is_file():
        try:
            data = json.loads(run([python, "-c", "import sys,json,importlib.metadata; print(json.dumps({'prefix':sys.prefix,'version':importlib.metadata.version('faster-whisper')}))"], env, 30))
            result["package_version"] = data["version"]
            result["venv_prefix_verified"] = Path(data["prefix"]).resolve() == venv.resolve()
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            result["package_diagnostic"] = str(exc)
    result["model_files"] = {name: {"path": str(directory / "models/tiny" / name), "verified": file_check(directory / "models/tiny" / name, info), "expected_bytes": info["size"]} for name, info in FILES.items()}
    result["backend_model_load_verified"] = False
    basic_ready = result["venv_prefix_verified"] and result["package_version"] == PACKAGE_VERSION and all(item["verified"] for item in result["model_files"].values())
    if basic_ready:
        try:
            run([python, "-c", "import sys; from faster_whisper import WhisperModel; WhisperModel(sys.argv[1],device='cpu',compute_type='int8',local_files_only=True); print('loaded')", directory / "models/tiny"], env, 120)
            result["backend_model_load_verified"] = True
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            result["backend_load_diagnostic"] = str(exc)
    result["ready"] = basic_ready and result["backend_model_load_verified"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, help="All dependency/model files stay under this explicitly selected directory")
    parser.add_argument("--check-only", action="store_true", help="Read-only verification; do not create folders/install/download")
    parser.add_argument("--direct", action="store_true", help="Ignore proxy variables only in this command's subprocesses/HTTPS opener")
    parser.add_argument("--curl-fallback", action="store_true", help="If normal HTTPS fails, permit TLS-verified curl to the official pinned URLs")
    args = parser.parse_args()
    directory = Path(args.directory).expanduser().resolve()
    env = dict(os.environ)
    if args.direct:
        env = {key: value for key, value in env.items() if not key.casefold().endswith("_proxy")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.check_only:
        result = inspect_tools(directory, env)
        result.update({"ok": result["ready"], "check_only": True, "files_written": False})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    directory.mkdir(parents=True, exist_ok=True)
    venv = directory / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv.exists() and not (venv / "pyvenv.cfg").is_file():
        raise RuntimeError(f"Refusing to reuse a non-venv directory: {venv}")
    if not python.is_file():
        note("Creating local virtual environment: " + str(venv))
        run([sys.executable, "-m", "venv", venv], env)
    prefix = run([python, "-c", "import sys; print(sys.prefix)"], env, 30).strip()
    if Path(prefix).resolve() != venv.resolve():
        raise RuntimeError("Python prefix is outside the selected local virtual environment; refusing pip install")
    installed = inspect_tools(directory, env).get("package_version")
    if installed != PACKAGE_VERSION:
        note("Installing pinned faster-whisper into this local venv only")
        run([python, "-m", "pip", "--isolated", "install", "--no-cache-dir", "--timeout", "30", "--retries", "1", "--index-url", "https://pypi.org/simple", "faster-whisper==" + PACKAGE_VERSION], env)
    model_dir = directory / "models/tiny"
    model_dir.mkdir(parents=True, exist_ok=True)
    downloads = []
    for name, metadata in FILES.items():
        destination = model_dir / name
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
        if destination.exists():
            if not file_check(destination, metadata):
                raise RuntimeError(f"Existing model file has the wrong size/hash; inspect it before replacing: {destination}")
            downloads.append({"file": name, "method": "already_present_verified", "official_url": url})
            continue
        note(f"Downloading official tiny model file: {name} ({metadata['size']} bytes)")
        handle, temp_name = tempfile.mkstemp(prefix=".shadow-model-", dir=model_dir)
        os.close(handle)
        temporary = Path(temp_name)
        try:
            details = download(url, temporary, metadata, args, env)
            if destination.exists():
                raise RuntimeError("Model destination appeared during download; refusing replacement")
            os.replace(temporary, destination)
            downloads.append(dict(details, file=name, official_url=url))
        finally:
            if temporary.exists():
                temporary.unlink()
    result = inspect_tools(directory, env)
    result.update({"ok": result["ready"], "check_only": False, "repository": REPOSITORY, "revision": REVISION, "model_sha256": FILES["model.bin"]["sha256"], "downloads": downloads, "global_packages_modified": False, "system_environment_modified": False, "audio_uploaded": False})
    (directory / "setup-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "stage": "local_asr_setup", "error_type": type(exc).__name__, "error": str(exc), "global_install_attempted": False, "tls_verification_disabled": False}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
