"""Portable source/release regression tests. No FFmpeg, Whisper, or network."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_release
import validate_plugin as vp


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_fixture(root):
    """A complete minimal source tree; real repository validation is separate."""
    for rel in sorted(vp.PLUGIN_FILES | vp.RELEASE_ROOT_FILES):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            write_json(path, {})
        elif path.suffix == ".py":
            path.write_text('"""Source fixture."""\n', encoding="utf-8")
        elif path.suffix == ".svg":
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n', encoding="utf-8")
        else:
            path.write_text("Fixture\n", encoding="utf-8")
    interface = {"displayName": "shadow", "composerIcon": "./assets/shadow.svg",
                 "logo": "./assets/shadow.svg", "defaultPrompt": ["使用 $shadow 完成剪辑。"]}
    common = {"name": "shadow", "version": "2.1.0", "description": "Portable source fixture",
              "author": {"name": "shadow contributors"}, "license": "MIT"}
    native = dict(common, skills="./skills/", interface=interface)
    standard = dict(common, **{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                              "extensions": {"com.openai": {"skills": "./skills/", "interface": interface}}})
    write_json(root / "shadow/plugin.json", standard)
    write_json(root / "shadow/.codex-plugin/plugin.json", native)
    write_json(root / ".agents/plugins/marketplace.json", {"name": "shadow", "plugins": [{
        "name": "shadow", "source": {"source": "local", "path": "./shadow"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}}]})
    skill = root / vp.PLUGIN_PREFIX
    (skill / "SKILL.md").write_text("---\nname: shadow\ndescription: " + "完成通用视频剪辑并按用户素材、软件和规格保存时间线、字幕与交接文件。" * 2
                                     + "\n---\n\n[技巧](references/techniques.md)\n", encoding="utf-8")
    (skill / "agents/openai.yaml").write_text('interface:\n  display_name: "shadow"\n  short_description: "剪辑"\n'
                                             '  default_prompt: "使用 $shadow 完成剪辑"\npolicy:\n'
                                             '  allow_implicit_invocation: true\n', encoding="utf-8")
    (root / "shadow/assets/shadow.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n', encoding="utf-8")
    (skill / "scripts/shadow.py").write_text('VERSION = "2.1.0"\n', encoding="utf-8")
    (skill / "scripts/subtitles.py").write_text('VERSION = "2.1.0"\n', encoding="utf-8")
    recipes = {"schema_version": 1, "selection_policy": {"fallback_recipe": "general-story"},
               "implementation_levels": {"editorial_plan": "planning"},
               "technique_index": [{"id": "S01", "implementation": ["editorial_plan"]}],
               "recipes": [{"id": "general-story", "core_techniques": ["S01"], "optional_techniques": []}]}
    write_json(skill / "assets/recipes.json", recipes)
    (skill / "references/techniques.md").write_text("# 技巧\n\n| S01 故事 | 策划 |\n", encoding="utf-8")
    (skill / "references/workflows.md").write_text("# 工作流\n\n## general-story — 通用\n\nS01\n", encoding="utf-8")
    profile = {"id": "education", "name": "教学", "family": "education", "description": "根据知识依赖完成教学视频。",
               "base_recipe": "general-story", "reference": "references/domains/education.md",
               "query_tags": ["教学"], "negative_tags": [], "priority": 1,
               "required_inputs": ["课程讲稿"], "deliverables": ["字幕"], "quality_gates": ["步骤完整"]}
    write_json(skill / "assets/domain-profiles.json", {"schema_version": 1, "profiles": [profile]})
    domain = skill / profile["reference"]
    domain.parent.mkdir(parents=True, exist_ok=True)
    domain.write_text("# 教育\n\n[工作流](../workflows.md)\n", encoding="utf-8")
    (root / "README.md").write_text("# shadow\n\n[维护说明](docs/maintaining.md)\n", encoding="utf-8")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shadow-release-test-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "source"
        self.root.mkdir()
        make_fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def edit_json(self, rel, mutation):
        path = self.root / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        mutation(data)
        write_json(path, data)

    def assert_invalid(self, contains):
        with self.assertRaises(vp.ValidationError) as raised:
            vp.validate(self.root)
        self.assertIn(contains, str(raised.exception))

    def test_repository_source_is_valid(self):
        report = vp.validate(REPOSITORY)
        self.assertEqual("shadow", report["plugin"])
        self.assertGreaterEqual(report["recipes"], 12)
        self.assertGreaterEqual(report["techniques"], 50)
        self.assertGreaterEqual(report["domain_profiles"], 20)

    def test_valid_fixture_and_exact_tag(self):
        report = vp.validate(self.root, "v2.1.0")
        self.assertEqual("2.1.0", report["version"])
        self.assertEqual(1, report["domain_profiles"])
        self.assertEqual(sum(name.endswith('.py') for name in vp.PLUGIN_FILES), report["python_files_compiled"])

    def test_deterministic_package_contents_and_alias(self):
        first = build_release.build(self.root, self.base / "first", tag="v2.1.0")
        second = build_release.build(self.root, self.base / "second", tag="v2.1.0")
        archive = Path(first["artifacts"][0])
        self.assertEqual(archive.read_bytes(), Path(second["artifacts"][0]).read_bytes())
        self.assertEqual(archive.read_bytes(), Path(first["artifacts"][1]).read_bytes())
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), first["sha256"])
        with zipfile.ZipFile(archive) as packed:
            self.assertIsNone(packed.testzip())
            self.assertEqual(sorted(first["release_files"]), packed.namelist())
            self.assertIn("shadow/.codex-plugin/plugin.json", packed.namelist())
            self.assertIn("安装shadow.cmd", packed.namelist())
            self.assertIn("docs/maintaining.md", packed.namelist())
            self.assertIn("docs/assets/shadow-hero.svg", packed.namelist())
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in packed.infolist()))
            self.assertTrue(all(not info.filename.startswith("/") and ".." not in Path(info.filename).parts for info in packed.infolist()))
        checksums = Path(first["artifacts"][2]).read_text(encoding="ascii").splitlines()
        self.assertEqual([first["sha256"] + "  shadow-v2.1.0.zip", first["sha256"] + "  shadow.zip"], checksums)

    def test_manifest_version_mismatch(self):
        self.edit_json("shadow/.codex-plugin/plugin.json", lambda data: data.update(version="2.0.0"))
        self.assert_invalid("manifest mismatch: version")

    def test_runtime_version_must_match_release(self):
        (self.root / (vp.PLUGIN_PREFIX + "scripts/shadow.py")).write_text('VERSION = "2.0.0"\n', encoding="utf-8")
        self.assert_invalid("shadow.py VERSION must match")

    def test_subtitle_version_must_match_release(self):
        (self.root / (vp.PLUGIN_PREFIX + "scripts/subtitles.py")).write_text('VERSION = "2.0.0"\n', encoding="utf-8")
        self.assert_invalid("subtitles.py VERSION must match")

    def test_existing_release_tag_mismatch(self):
        with self.assertRaisesRegex(vp.ValidationError, "must equal"):
            vp.validate(self.root, "v2.2.0")

    def test_marketplace_does_not_escape_plugin(self):
        self.edit_json(".agents/plugins/marketplace.json", lambda data: data["plugins"][0]["source"].update(path="../private"))
        self.assert_invalid("local ./shadow")

    def test_missing_skill_reference(self):
        path = self.root / (vp.PLUGIN_PREFIX + "SKILL.md")
        path.write_text(path.read_text(encoding="utf-8") + "\n[缺失](references/missing.md)\n", encoding="utf-8")
        self.assert_invalid("missing document reference")

    def test_document_reference_cannot_escape(self):
        (self.root / "README.md").write_text("[外部](../secret.txt)\n", encoding="utf-8")
        self.assert_invalid("escapes repository")

    def test_html_images_and_links_must_exist(self):
        readme = self.root / "README.md"
        readme.write_text('<a href="docs/maintaining.md?view=1&amp;language=zh#local">'
                          '<img src="docs/assets/shadow-hero.svg" alt="shadow" /></a>\n', encoding="utf-8")
        self.assertTrue(vp.validate(self.root)["ok"])
        for html in ('<img src="docs/assets/missing.svg" alt="missing">',
                     '<a href="docs/missing.md">Missing guide</a>'):
            with self.subTest(html=html):
                readme.write_text(html, encoding="utf-8")
                self.assert_invalid("missing document reference")

    def test_html_references_cannot_escape_repository(self):
        for html in ('<img src="%2e%2e/secret.svg">',
                     '<a href="..&#47;secret.md">Outside</a>'):
            with self.subTest(html=html):
                (self.root / "README.md").write_text(html, encoding="utf-8")
                self.assert_invalid("escapes repository")

    def test_picture_source_must_exist_even_with_valid_fallback(self):
        readme = self.root / "README.md"
        picture = ('<picture><source media="(max-width: 600px)" srcset="{}">'
                   '<img src="docs/assets/shadow-hero.svg" alt="shadow"></picture>\n')
        readme.write_text(picture.format("docs/assets/shadow-hero-mobile.svg"), encoding="utf-8")
        self.assertTrue(vp.validate(self.root)["ok"])
        readme.write_text(picture.format("docs/assets/missing-mobile.svg"), encoding="utf-8")
        self.assert_invalid("missing document reference")

    def test_unknown_recipe_technique(self):
        self.edit_json(vp.PLUGIN_PREFIX + "assets/recipes.json", lambda data: data["recipes"][0]["core_techniques"].append("S99"))
        self.assert_invalid("unknown technique")

    def test_domain_base_recipe_must_exist(self):
        self.edit_json(vp.PLUGIN_PREFIX + "assets/domain-profiles.json", lambda data: data["profiles"][0].update(base_recipe="missing"))
        self.assert_invalid("unknown base_recipe")

    def test_domain_ids_are_unique(self):
        self.edit_json(vp.PLUGIN_PREFIX + "assets/domain-profiles.json", lambda data: data["profiles"].append(dict(data["profiles"][0])))
        self.assert_invalid("duplicate domain profile ID")

    def test_domain_reference_must_match_id(self):
        self.edit_json(vp.PLUGIN_PREFIX + "assets/domain-profiles.json", lambda data: data["profiles"][0].update(reference="references/domains/other.md"))
        self.assert_invalid("reference must be")

    def test_domain_keywords_can_overlap(self):
        data = vp.load_json(self.root / (vp.PLUGIN_PREFIX + "assets/domain-profiles.json"))
        data["profiles"].append(dict(data["profiles"][0], id="training", reference="references/domains/training.md"))
        write_json(self.root / (vp.PLUGIN_PREFIX + "assets/domain-profiles.json"), data)
        (self.root / (vp.PLUGIN_PREFIX + "references/domains/training.md")).write_text("# 培训\n", encoding="utf-8")
        self.assertEqual(2, vp.validate(self.root)["domain_profiles"])

    def test_domain_schema_version_required(self):
        self.edit_json(vp.PLUGIN_PREFIX + "assets/domain-profiles.json", lambda data: data.pop("schema_version"))
        self.assert_invalid("schema_version must be 1")

    def test_domain_descriptive_strings_required(self):
        rel = vp.PLUGIN_PREFIX + "assets/domain-profiles.json"
        original = vp.load_json(self.root / rel)
        for key in ("name", "family", "description"):
            for invalid in (None, "", "   ", 3):
                with self.subTest(key=key, invalid=invalid):
                    data = json.loads(json.dumps(original))
                    data["profiles"][0][key] = invalid
                    write_json(self.root / rel, data)
                    self.assert_invalid(key + " must be a nonempty string")

    def test_domain_list_fields_have_string_items(self):
        rel = vp.PLUGIN_PREFIX + "assets/domain-profiles.json"
        original = vp.load_json(self.root / rel)
        for key in ("query_tags", "negative_tags", "required_inputs", "deliverables", "quality_gates"):
            for invalid in (None, "single string", [3], ["  "]):
                with self.subTest(key=key, invalid=invalid):
                    data = json.loads(json.dumps(original))
                    data["profiles"][0][key] = invalid
                    write_json(self.root / rel, data)
                    self.assert_invalid(key + " must be a list")

    def test_domain_priority_is_numeric_and_finite(self):
        rel = vp.PLUGIN_PREFIX + "assets/domain-profiles.json"
        original = vp.load_json(self.root / rel)
        for invalid in (None, True, "3"):
            with self.subTest(invalid=invalid):
                data = json.loads(json.dumps(original))
                data["profiles"][0]["priority"] = invalid
                write_json(self.root / rel, data)
                self.assert_invalid("priority must be a finite number")
        original["profiles"][0]["priority"] = -2.5
        write_json(self.root / rel, original)
        self.assertTrue(vp.validate(self.root)["ok"])

    def test_unknown_plugin_file_fails_explicitly(self):
        (self.root / "shadow" / "extra.md").write_text("Unlisted source\n", encoding="utf-8")
        self.assert_invalid("unknown file outside the source allowlist")

    def test_unknown_directory_fails_explicitly(self):
        (self.root / "shadow" / "personal-project").mkdir()
        self.assert_invalid("unknown directory outside the source allowlist")

    def test_member_media_never_enters_package(self):
        (self.root / "shadow" / "会员原片.mp4").write_bytes(b"not allowed")
        self.assert_invalid("media/project file forbidden")
        self.assertFalse((self.base / "dist").exists())

    def test_root_media_fails_too(self):
        (self.root / "private.wav").write_bytes(b"not allowed")
        self.assert_invalid("media/project file forbidden")

    def test_explicit_python_cache_exclusion(self):
        path = self.root / (vp.PLUGIN_PREFIX + "scripts/__pycache__/shadow.cpython-310.pyc")
        path.parent.mkdir()
        path.write_bytes(b"cache")
        report = build_release.build(self.root, self.base / "out")
        self.assertIn(path.relative_to(self.root).as_posix(), report["excluded_generated_files"])
        with zipfile.ZipFile(report["artifacts"][0]) as packed:
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in packed.namelist()))

    def test_cache_directory_cannot_hide_media(self):
        path = self.root / (vp.PLUGIN_PREFIX + "scripts/__pycache__/private.mp4")
        path.parent.mkdir()
        path.write_bytes(b"media")
        self.assert_invalid("unexpected content in Python cache")

    def test_personal_paths_fail_before_build(self):
        # Construct the path rather than introducing a real account name.
        (self.root / "README.md").write_text("C:" + "\\Users\\PrivatePerson\\Documents\\secret\n", encoding="utf-8")
        self.assert_invalid("personal account path")

    def test_json_duplicate_keys_fail(self):
        (self.root / (vp.PLUGIN_PREFIX + "assets/project-template.json")).write_text('{"name":1,"name":2}', encoding="utf-8")
        self.assert_invalid("duplicate JSON key")

    def test_nonfinite_json_fails(self):
        (self.root / (vp.PLUGIN_PREFIX + "assets/project-template.json")).write_text('{"number":NaN}', encoding="utf-8")
        self.assert_invalid("non-finite JSON number")

    def test_python_compilation_does_not_write_cache(self):
        vp.validate(self.root)
        self.assertEqual([], list(self.root.rglob("*.pyc")))
        (self.root / (vp.PLUGIN_PREFIX + "scripts/shadow.py")).write_text("if broken syntax\n", encoding="utf-8")
        self.assert_invalid("Python syntax error")

    def test_existing_build_outputs_need_force(self):
        out = self.root / "dist"
        first = build_release.build(self.root, out)
        with self.assertRaisesRegex(vp.ValidationError, "output already exists"):
            build_release.build(self.root, out)
        second = build_release.build(self.root, out, force=True)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_outputs_cannot_replace_sources(self):
        with self.assertRaisesRegex(vp.ValidationError, "must not contain release sources"):
            build_release.build(self.root, self.root)
        with self.assertRaisesRegex(vp.ValidationError, "must use the dist"):
            build_release.build(self.root, self.root / "shadow" / "out")

    def test_symlink_sources_are_rejected(self):
        path = self.root / "shadow" / "extra.md"
        try:
            path.symlink_to(self.base / "private.md")
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable for this account: {exc}")
        self.assert_invalid("symlinks are not release sources")

    def test_json_cli_error_has_nonzero_exit(self):
        result = subprocess.run([sys.executable, "-X", "utf8", str(REPOSITORY / "scripts/validate_plugin.py"),
                                 "--root", str(self.root), "--tag", "v9.9.9"], text=True, encoding="utf-8", capture_output=True)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_release_workflow_only_attaches_existing_release(self):
        text = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("types: [published]", text)
        self.assertIn("--tag", text)
        self.assertIn("gh release upload", text)
        self.assertIn("dist/shadow.zip", text)
        self.assertNotIn("gh release create", text)
        self.assertNotIn("--clobber", text)
        self.assertNotIn("workflow_dispatch", text)
        self.assertNotIn("schedule:", text)


if __name__ == "__main__":
    unittest.main()
