#!/usr/bin/env python3
"""Build a validated, deterministic Shadow release ZIP and SHA-256 checksums."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from validate_plugin import ValidationError, validate


def build(root: Path | str, output_dir: Path | str, tag: str | None = None, force=False):
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    report = validate(root, tag)
    # A destination inside plugin sources would become an unknown source file,
    # and must never replace the user's checked-in content.
    for source in report["release_files"]:
        require_path = root / source
        if require_path == output_dir or require_path.is_relative_to(output_dir):
            raise ValidationError("output directory must not contain release sources")
    if output_dir.is_relative_to(root) and output_dir != root / "dist":
        raise ValidationError("inside the repository, build outputs must use the dist directory")
    names = [f"shadow-v{report['version']}.zip", "shadow.zip", "SHA256SUMS"]
    destinations = [output_dir / name for name in names]
    for path in destinations:
        if path.is_symlink():
            raise ValidationError(f"refusing symlink output: {path.name}")
        if path.exists() and (not path.is_file() or not force):
            raise ValidationError(f"output already exists: {path.name}; pass --force to replace build outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        handle = tempfile.NamedTemporaryFile(prefix=".shadow-build-", suffix=".zip", dir=output_dir, delete=False)
        temp_zip = Path(handle.name)
        handle.close()
        temporary.append(temp_zip)
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for rel in report["release_files"]:
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, (root / rel).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        data = temp_zip.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        # Verify exactly what was serialized before exposing the artifacts.
        with zipfile.ZipFile(temp_zip) as archive:
            if archive.testzip() is not None or archive.namelist() != report["release_files"]:
                raise ValidationError("ZIP integrity or allowlist verification failed")
        handle = tempfile.NamedTemporaryFile(prefix=".shadow-build-", suffix=".zip", dir=output_dir, delete=False)
        temp_alias = Path(handle.name)
        temporary.append(temp_alias)
        with handle:
            handle.write(data)
        handle = tempfile.NamedTemporaryFile(prefix=".shadow-build-", suffix=".txt", dir=output_dir, delete=False)
        temp_sums = Path(handle.name)
        temporary.append(temp_sums)
        with handle:
            handle.write((f"{digest}  {names[0]}\n{digest}  shadow.zip\n").encode("ascii"))
        for source, destination in zip((temp_zip, temp_alias, temp_sums), destinations):
            if force:
                os.replace(source, destination)
            else:
                # Exclusive creation protects an output appearing during the build.
                with destination.open("xb") as stream:
                    stream.write(source.read_bytes())
                source.unlink()
        return {"ok": True, "version": report["version"], "tag": tag,
                "artifacts": [str(path) for path in destinations], "sha256": digest,
                "zip_bytes": len(data), "release_files": report["release_files"],
                "excluded_generated_files": report["excluded_generated_files"],
                "excluded_maintenance_files": report["excluded_maintenance_files"],
                "deterministic_metadata": "sorted names, 1980-01-01 timestamp, regular mode 0644",
                "note": "shadow.zip and the versioned ZIP are byte-identical"}
    finally:
        for path in temporary:
            if path.exists():
                path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--output-dir", type=Path, help="artifact directory; defaults to <root>/dist")
    parser.add_argument("--tag", help="existing release tag; must equal v<manifest-version>")
    parser.add_argument("--force", action="store_true", help="replace generated ZIP/checksum outputs only")
    args = parser.parse_args(argv)
    try:
        result = build(args.root, args.output_dir or args.root / "dist", args.tag, args.force)
    except (ValidationError, OSError, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
