#!/usr/bin/env python3
"""Validate Shadow's source tree without optional media or ASR dependencies."""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$")
PLUGIN_PREFIX = "shadow/skills/shadow/"
PLUGIN_FILES = frozenset({
    "shadow/plugin.json", "shadow/.codex-plugin/plugin.json", "shadow/LICENSE",
    "shadow/assets/shadow.svg", PLUGIN_PREFIX + "SKILL.md",
    PLUGIN_PREFIX + "agents/openai.yaml",
    *[PLUGIN_PREFIX + "scripts/" + n + ".py" for n in
      ("assets", "pipeline", "profiles", "recipes", "setup_asr", "shadow", "subtitles")],
    *[PLUGIN_PREFIX + "assets/" + n + ".json" for n in
      ("domain-profiles", "project-template", "recipes")],
    *[PLUGIN_PREFIX + "references/" + n + ".md" for n in
      ("asset-library", "baotu", "commands", "domains", "pipeline", "premiere",
       "setup", "subtitles", "techniques", "timeline-format", "timeline-v2", "workflows")],
})
RELEASE_ROOT_FILES = frozenset({
    "install-shadow.ps1", "update-shadow.ps1", "安装shadow.cmd", "更新shadow.cmd",
    "README.md", "CHANGELOG.md", "LICENSE", ".agents/plugins/marketplace.json",
    "docs/maintaining.md",
    *["docs/assets/" + name + ".svg" for name in
      ("shadow-hero", "shadow-hero-mobile", "shadow-workflow", "shadow-workflow-mobile",
       "download", "quickstart", "release-notes")],
})
MAINTENANCE_FILES = frozenset({
    ".gitignore", ".gitattributes", "scripts/build_release.py", "scripts/validate_plugin.py",
    "tests/test_release.py", "tests/test_profiles.py", ".github/workflows/validate.yml", ".github/workflows/release.yml",
})
MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mp3", ".wav", ".flac",
    ".m4a", ".aac", ".ogg", ".prproj", ".aep", ".psd", ".rar", ".7z", ".sqlite",
    ".sqlite3", ".db", ".ncm", ".lrc", ".srt", ".ass", ".png", ".jpg", ".jpeg",
})
PRIVATE_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]|/)(?:Users|home)[\\/][^\\/\s<>\"'$]+[\\/]")
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


class ValidationError(ValueError):
    pass


class DocumentHTMLLinks(HTMLParser):
    """Read HTML navigation and images embedded in GitHub Markdown."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.targets = []

    def handle_starttag(self, tag, attrs):
        # Responsive picture sources use one URL per srcset, without descriptors.
        attribute = {"a": "href", "img": "src", "source": "srcset"}.get(tag)
        if attribute:
            self.targets.extend(value for name, value in attrs
                                if name == attribute and value is not None)


def relative_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def load_json(path: Path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              ValidationError(f"non-finite JSON number in {path.name}: {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON {path.name}: {exc}") from exc


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def safe_relative(value, label):
    require(isinstance(value, str) and value, f"{label}: expected a relative path")
    require("\\" not in value, f"{label}: use forward slashes")
    path = PurePosixPath(value)
    require(not path.is_absolute() and not re.match(r"^[A-Za-z]:", value)
            and ".." not in path.parts, f"{label}: path must stay inside the repository")
    return path.as_posix().removeprefix("./")


def domain_references(data):
    """Catalog references form an explicit, checked allowlist for domain pages."""
    require(isinstance(data, dict) and type(data.get("schema_version")) is int
            and data["schema_version"] == 1, "domain-profiles.json: schema_version must be 1")
    profiles = data.get("profiles")
    require(isinstance(profiles, list) and profiles, "domain-profiles.json: profiles must be a nonempty list")
    paths = set()
    for profile in profiles:
        require(isinstance(profile, dict), "domain-profiles.json: every profile must be an object")
        ident = profile.get("id")
        require(isinstance(ident, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ident),
                f"invalid domain profile id: {ident!r}")
        for key in ("name", "family", "description"):
            require(isinstance(profile.get(key), str) and profile[key].strip(),
                    f"domain {ident}: {key} must be a nonempty string")
        for key in ("query_tags", "negative_tags", "required_inputs", "deliverables", "quality_gates"):
            require(isinstance(profile.get(key), list)
                    and all(isinstance(value, str) and value.strip() for value in profile[key]),
                    f"domain {ident}: {key} must be a list of nonempty strings")
        priority = profile.get("priority")
        require(type(priority) in (int, float) and math.isfinite(priority),
                f"domain {ident}: priority must be a finite number")
        reference = profile.get("reference")
        rel = safe_relative(reference, f"domain {ident} reference")
        require(rel == f"references/domains/{ident}.md",
                f"domain {ident}: reference must be references/domains/{ident}.md")
        paths.add(PLUGIN_PREFIX + rel)
    return profiles, paths


def source_allowlist(root: Path):
    catalog = root / (PLUGIN_PREFIX + "assets/domain-profiles.json")
    require(catalog.is_file(), "missing required file: " + relative_name(catalog, root))
    profiles, domain_paths = domain_references(load_json(catalog))
    return set(PLUGIN_FILES | RELEASE_ROOT_FILES) | domain_paths, profiles


def inventory(root: Path, allowed: set[str]):
    """Refuse unknown sources, symlinks and media; list explicit cache exclusions."""
    permitted = allowed | set(MAINTENANCE_FILES)
    directories = {part for name in permitted for part in
                   [str(PurePosixPath(name).parent)] if part != "."}
    directories |= {str(parent) for name in permitted for parent in
                    PurePosixPath(name).parents if str(parent) != "."}
    excluded = []
    actual = set()
    stack = [root]
    while stack:
        parent = stack.pop()
        for path in sorted(parent.iterdir(), key=lambda p: p.name):
            rel = relative_name(path, root)
            require(not path.is_symlink(), f"symlinks are not release sources: {rel}")
            # Windows junctions/reparse points must not introduce external files.
            if hasattr(path, "is_junction"):
                require(not path.is_junction(), f"junctions are not release sources: {rel}")
            require(path.resolve().is_relative_to(root), f"path escapes repository: {rel}")
            if rel == ".git":
                excluded.append(rel + "/")
                continue
            if path.is_dir():
                if path.name == "__pycache__":
                    cache_files = list(path.iterdir())
                    require(all(p.is_file() and not p.is_symlink() and p.suffix == ".pyc"
                                for p in cache_files), f"unexpected content in Python cache: {rel}")
                    excluded.extend(relative_name(p, root) for p in cache_files)
                    continue
                if rel == "dist":
                    for artifact in path.iterdir():
                        require(artifact.is_file() and not artifact.is_symlink()
                                and (artifact.name in ("SHA256SUMS", "shadow.zip") or re.fullmatch(
                                    r"shadow-v[0-9A-Za-z.-]+\.zip", artifact.name)),
                                f"unknown build output in dist: {relative_name(artifact, root)}")
                        excluded.append(relative_name(artifact, root))
                    continue
                require(rel in directories, f"unknown directory outside the source allowlist: {rel}")
                stack.append(path)
            elif path.is_file():
                if path.suffix == ".pyc" and path.with_suffix(".py").is_file():
                    excluded.append(rel)
                    continue
                require(path.suffix.lower() not in MEDIA_EXTENSIONS,
                        f"media/project file forbidden in release sources: {rel}")
                require(rel in permitted, f"unknown file outside the source allowlist: {rel}")
                actual.add(rel)
            else:
                raise ValidationError(f"unsupported filesystem entry: {rel}")
    missing = sorted(allowed - actual)
    require(not missing, "missing required release files: " + ", ".join(missing))
    return sorted(actual & allowed), sorted(excluded), sorted(actual & set(MAINTENANCE_FILES))


def manifest_checks(root: Path, tag: str | None):
    standard = load_json(root / "shadow/plugin.json")
    native = load_json(root / "shadow/.codex-plugin/plugin.json")
    require(isinstance(standard, dict) and isinstance(native, dict), "manifests must be JSON objects")
    for name in ("name", "version", "description", "author", "license"):
        require(standard.get(name) == native.get(name), f"manifest mismatch: {name}")
    require(standard.get("name") == "shadow", "plugin name must be shadow")
    version = standard.get("version")
    require(isinstance(version, str) and SEMVER.fullmatch(version), "manifest version must be semver")
    require(standard.get("license") == "MIT", "manifest license must be MIT")
    require(standard.get("$schema") == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "unexpected agent plugin schema URL")
    require(tag is None or tag == "v" + version,
            f"release tag {tag!r} must equal manifest version v{version}")
    for filename in ("shadow.py", "subtitles.py"):
        runtime = root / (PLUGIN_PREFIX + "scripts/" + filename)
        try:
            tree = ast.parse(runtime.read_text(encoding="utf-8-sig"), filename=filename)
        except SyntaxError as exc:
            raise ValidationError(f"Python syntax error: {filename}:{exc.lineno}: {exc.msg}") from exc
        versions = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
                    and isinstance(node.value, ast.Constant)]
        require(versions == [version], f"{filename} VERSION must match both plugin manifests")
    extension = standard.get("extensions", {}).get("com.openai", {})
    require(extension.get("skills") == native.get("skills") == "./skills/",
            "both manifests must reference ./skills/")
    require(extension.get("interface") == native.get("interface"), "manifest mismatch: interface")
    interface = native.get("interface") or {}
    require(interface.get("displayName") == "shadow", "plugin interface displayName must be shadow")
    for key in ("composerIcon", "logo"):
        rel = safe_relative(interface.get(key), "manifest " + key)
        require((root / "shadow" / rel).is_file(), f"manifest {key} references missing file: {rel}")
        require(rel == "assets/shadow.svg", f"manifest {key} must reference assets/shadow.svg")
    prompts = interface.get("defaultPrompt")
    require(isinstance(prompts, list) and prompts and all(isinstance(p, str) and "$shadow" in p for p in prompts),
            "manifest defaultPrompt must invoke $shadow")
    catalog = load_json(root / ".agents/plugins/marketplace.json")
    require(isinstance(catalog, dict) and isinstance(catalog.get("plugins"), list),
            "marketplace must contain a plugins list")
    entries = catalog["plugins"]
    require(len(entries) == 1 and entries[0].get("name") == "shadow",
            "marketplace must contain exactly the shadow plugin")
    source = entries[0].get("source")
    require(isinstance(source, dict) and source.get("source") == "local"
            and source.get("path") == "./shadow", "marketplace shadow source must be local ./shadow")
    policy = entries[0].get("policy", {})
    require(policy.get("installation") == "AVAILABLE" and policy.get("authentication") == "ON_USE",
            "marketplace policy must be AVAILABLE / ON_USE")
    return version


def skill_checks(root: Path):
    skill = root / (PLUGIN_PREFIX + "SKILL.md")
    text = skill.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    require(match is not None, "SKILL.md requires YAML frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        require(":" in line, "SKILL.md frontmatter uses name/description scalar fields")
        key, value = line.split(":", 1)
        require(key not in fields, f"duplicate skill frontmatter field: {key}")
        fields[key] = value.strip().strip('"\'')
    require(fields.get("name") == "shadow", "skill frontmatter name must be shadow")
    require(len(fields.get("description", "")) >= 30, "skill description must explain scope and invocation")
    agent = (root / (PLUGIN_PREFIX + "agents/openai.yaml")).read_text(encoding="utf-8-sig")
    for key in ("display_name", "short_description", "default_prompt"):
        match = re.search(rf"^\s+{key}:\s*(.+)$", agent, re.M)
        require(match is not None and match.group(1).strip(), f"agents/openai.yaml missing {key}")
        if key == "display_name":
            require(match.group(1).strip().strip('"\'') == "shadow", "agent display_name must be shadow")
        if key == "default_prompt":
            require("$shadow" in match.group(1), "agent default_prompt must invoke $shadow")
    require(re.search(r"^\s+allow_implicit_invocation:\s*true\s*$", agent, re.M),
            "agent policy must enable implicit invocation")


def recipe_checks(root: Path, profiles):
    recipes = load_json(root / (PLUGIN_PREFIX + "assets/recipes.json"))
    require(isinstance(recipes, dict) and recipes.get("schema_version") == 1, "recipes schema_version must be 1")
    techniques = recipes.get("technique_index")
    items = recipes.get("recipes")
    require(isinstance(techniques, list) and techniques and isinstance(items, list) and items,
            "recipes must contain techniques and workflows")
    technique_ids = [t.get("id") for t in techniques]
    recipe_ids = [r.get("id") for r in items]
    require(all(isinstance(t, str) and re.fullmatch(r"[SCRAVTD]\d{2}", t) for t in technique_ids),
            "invalid technique ID")
    require(len(technique_ids) == len(set(technique_ids)), "duplicate technique ID")
    require(all(isinstance(r, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", r) for r in recipe_ids),
            "invalid recipe ID")
    require(len(recipe_ids) == len(set(recipe_ids)), "duplicate recipe ID")
    levels = recipes.get("implementation_levels", {})
    for technique in techniques:
        implementations = technique.get("implementation", [])
        require(isinstance(implementations, list) and implementations,
                f"technique {technique['id']}: missing implementation levels")
        require(all(level in levels for level in implementations),
                f"technique {technique['id']}: unknown implementation level")
    for recipe in items:
        for key in ("core_techniques", "optional_techniques"):
            values = recipe.get(key, [])
            require(isinstance(values, list), f"recipe {recipe['id']}: {key} must be a list")
            require(all(v in technique_ids for v in values), f"recipe {recipe['id']}: unknown technique in {key}")
            require(len(values) == len(set(values)), f"recipe {recipe['id']}: duplicate technique in {key}")
    require(recipes.get("selection_policy", {}).get("fallback_recipe") in recipe_ids, "unknown fallback_recipe")
    techniques_doc = (root / (PLUGIN_PREFIX + "references/techniques.md")).read_text(encoding="utf-8-sig")
    doc_ids = re.findall(r"^\|\s*([SCRAVTD]\d{2})\b", techniques_doc, re.M)
    require(len(doc_ids) == len(set(doc_ids)), "duplicate technique card in techniques.md")
    require(set(doc_ids) == set(technique_ids), "technique cards and recipes.json technique_index disagree")
    workflows_doc = (root / (PLUGIN_PREFIX + "references/workflows.md")).read_text(encoding="utf-8-sig")
    workflow_ids = re.findall(r"^##\s+([a-z0-9]+(?:-[a-z0-9]+)*)\s+—", workflows_doc, re.M)
    require(len(workflow_ids) == len(set(workflow_ids)), "duplicate workflow section")
    require(set(workflow_ids) == set(recipe_ids), "workflow sections and recipe IDs disagree")
    referenced = set(re.findall(r"\b[SCRAVTD]\d{2}\b", workflows_doc))
    require(referenced <= set(technique_ids), "workflows.md refers to unknown technique IDs")
    domain_ids = [p["id"] for p in profiles]
    require(len(domain_ids) == len(set(domain_ids)), "duplicate domain profile ID")
    for profile in profiles:
        require(profile.get("base_recipe") in recipe_ids, f"domain {profile['id']}: unknown base_recipe")
        for key in ("core_techniques", "optional_techniques", "techniques"):
            if key in profile:
                require(isinstance(profile[key], list) and all(t in technique_ids for t in profile[key]),
                        f"domain {profile['id']}: unknown technique in {key}")
    return len(techniques), len(items), len(profiles)


def document_checks(root: Path, files):
    compiled = 0
    links = 0
    domains = set()
    for rel in files:
        path = root / rel
        require(path.stat().st_size <= 2_000_000, f"release source is unexpectedly large: {rel}")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise ValidationError(f"release source must be UTF-8 text, not binary media: {rel}") from exc
        require("\0" not in text, f"NUL/binary content in release source: {rel}")
        normalized = text.replace("\\\\", "\\")
        require(PRIVATE_PATH.search(normalized) is None, f"personal account path in release source: {rel}")
        require(TOKEN.search(text) is None, f"credential-like content in release source: {rel}")
        if path.suffix == ".json":
            load_json(path)
        if path.suffix == ".py":
            try:
                compile(text, rel, "exec")
                compiled += 1
            except SyntaxError as exc:
                raise ValidationError(f"Python syntax error: {rel}:{exc.lineno}: {exc.msg}") from exc
        if path.suffix == ".svg":
            try:
                require(ET.fromstring(text).tag == "{http://www.w3.org/2000/svg}svg", f"invalid SVG icon: {rel}")
            except ET.ParseError as exc:
                raise ValidationError(f"invalid SVG: {rel}: {exc}") from exc
        if path.suffix == ".md":
            # Code fences contain examples, not document navigation.
            prose = re.sub(r"^```.*?^```\s*$", "", text, flags=re.M | re.S)
            html_links = DocumentHTMLLinks()
            html_links.feed(prose)
            targets = re.findall(r"!?\[[^\]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", prose)
            for target in targets + html_links.targets:
                target = target.strip().strip("<>")
                url = urlsplit(target)
                if url.scheme in ("http", "https"):
                    require(bool(url.netloc) and url.username is None and url.password is None,
                            f"invalid external link in {rel}: {target}")
                    domains.add(url.hostname)
                    continue
                if url.scheme in ("mailto", "app", "codex") or not url.path:
                    continue
                require(not url.scheme and not target.startswith("/"), f"nonportable document link in {rel}: {target}")
                linked = (path.parent / unquote(url.path)).resolve()
                require(linked.is_relative_to(root), f"document link escapes repository in {rel}: {target}")
                require(linked.exists(), f"missing document reference in {rel}: {target}")
                require(linked.is_dir() or relative_name(linked, root) in files,
                        f"document link targets a file absent from the release in {rel}: {target}")
                links += 1
    return compiled, links, sorted(domains)


def validate(root: Path | str, tag: str | None = None):
    root = Path(root).resolve()
    require(root.is_dir(), "repository root does not exist")
    allowed, profiles = source_allowlist(root)
    files, excluded, maintenance = inventory(root, allowed)
    version = manifest_checks(root, tag)
    skill_checks(root)
    techniques, recipes, domain_count = recipe_checks(root, profiles)
    compiled, links, external_domains = document_checks(root, files)
    for rel in maintenance:
        if rel.endswith(".py"):
            try:
                compile((root / rel).read_text(encoding="utf-8-sig"), rel, "exec")
                compiled += 1
            except SyntaxError as exc:
                raise ValidationError(f"Python syntax error: {rel}:{exc.lineno}: {exc.msg}") from exc
    return {"ok": True, "plugin": "shadow", "version": version, "tag": tag,
            "release_files": files, "excluded_generated_files": excluded,
            "excluded_maintenance_files": maintenance, "python_files_compiled": compiled,
            "local_document_links": links, "external_reference_domains": external_domains,
            "techniques": techniques, "recipes": recipes, "domain_profiles": domain_count,
            "verification_scope": "source/package only; no FFmpeg, ASR, network or Premiere execution"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--tag", help="optional existing release tag; must equal v<manifest-version>")
    args = parser.parse_args(argv)
    try:
        report = validate(args.root, args.tag)
    except (ValidationError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
