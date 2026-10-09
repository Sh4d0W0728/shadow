"""Filesystem safety and review-evidence contract tests; no FFmpeg required."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('shadow_organize', ROOT / 'shadow/skills/shadow/scripts/organize.py')
organize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(organize)


def sha(value):
    return hashlib.sha256(value).hexdigest()


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source, self.workspace, self.destination = [self.root / name for name in ('source', 'workspace', 'destination')]
        self.source.mkdir()
        self.workspace.mkdir()
        self.original = self.source / '001 原片.mov'
        self.original.write_bytes(b'original-video-bytes')
        self.duplicate = self.source / 'backup.mov'
        self.duplicate.write_bytes(self.original.read_bytes())
        self.sample = self.workspace / 'sample.png'
        self.sample.write_bytes(b'actual-frame-placeholder-for-filesystem-tests')
        self.sheet = self.workspace / 'sheet.png'
        self.sheet.write_bytes(b'contact-sheet-placeholder')
        digest = sha(self.original.read_bytes())
        self.entry = {'asset_id': 'v_' + digest[:16], 'sha256': digest, 'byte_size': self.original.stat().st_size,
                      'source_paths': [str(self.original), str(self.duplicate)],
                      'relative_paths': [self.original.name, self.duplicate.name],
                      'metadata': {'status': 'ok'}, 'duration_seconds': 5.0, 'preview_status': 'ready',
                      'samples': [{'sample_id': 'S001', 'requested_seconds': 0.0, 'path': str(self.sample), 'sha256': sha(self.sample.read_bytes())}],
                      'contact_sheet': str(self.sheet), 'contact_sheet_sha256': sha(self.sheet.read_bytes())}
        self.inventory = {'schema_version': 1, 'source_root': str(self.source), 'workspace_root': str(self.workspace),
                          'preview_config': {}, 'entries': [self.entry], 'skipped_files': []}
        self.save_inventory()
        self.write(self.workspace / '.shadow-organizer.json', {'schema_version': 1, 'kind': 'shadow-organizer',
                   'source_root': str(self.source), 'workspace_root': str(self.workspace)})
        self.review = {'asset_id': self.entry['asset_id'], 'source_sha256': digest, 'category_id': '04',
                       'title': '水杯', 'summary': '看见桌面上的水杯', 'tags': ['静物'], 'confidence': 'high',
                       'evidence': {'method': 'sampled_frames', 'sample_ids': ['S001'], 'note': 'S001 显示水杯'}}
        self.reviews = self.workspace / 'reviews.json'
        self.save_reviews()
        self.plan_path = self.workspace / 'plan.json'

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def save_inventory(self):
        self.inventory['inventory_id'] = organize.media.inventory_id(self.inventory)
        self.write(self.workspace / 'inventory.json', self.inventory)

    def save_reviews(self, entries=None):
        self.write(self.reviews, {'schema_version': 1, 'inventory_id': self.inventory['inventory_id'],
                                'reviews': [self.review] if entries is None else entries})

    def plan(self):
        organize.make_plan(self.workspace, self.reviews, self.destination)
        return organize.json_read(self.plan_path)

    def test_copies_deduplicates_maps_and_repeats_without_touching_source(self):
        before = (self.original.read_bytes(), self.original.stat().st_mtime_ns, self.duplicate.stat().st_mtime_ns)
        plan = self.plan()
        first = organize.apply_plan(self.plan_path)
        second = organize.apply_plan(self.plan_path)
        self.assertEqual(first['verification']['status'], 'verified')
        self.assertEqual(second['copied_files_including_previews'], 0)
        self.assertEqual(first['counts']['unique_videos'], 1)
        self.assertEqual(first['counts']['source_files'], 2)
        self.assertEqual(first['counts']['duplicate_files'], 1)
        target = self.destination / plan['entries'][0]['destination_relative']
        self.assertEqual(target.read_bytes(), before[0])
        self.assertEqual((self.original.read_bytes(), self.original.stat().st_mtime_ns, self.duplicate.stat().st_mtime_ns), before)
        meta = self.destination / '.shadow-organizer'
        self.assertTrue((meta / 'mapping.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertEqual(len(organize.json_read(meta / 'mapping.json')), 2)
        self.assertIn('previews/', (meta / 'catalog.html').read_text(encoding='utf-8'))

    def test_existing_equal_bytes_are_still_unowned(self):
        plan = self.plan()
        target = self.destination / plan['entries'][0]['destination_relative']
        target.parent.mkdir(parents=True)
        target.write_bytes(self.original.read_bytes())
        with self.assertRaisesRegex(ValueError, 'unowned'):
            organize.apply_plan(self.plan_path)
        self.assertEqual(target.read_bytes(), self.original.read_bytes())

    def test_changed_duplicate_source_fails_before_any_media_copy(self):
        self.plan()
        self.duplicate.write_bytes(b'changed duplicate')
        with self.assertRaisesRegex(ValueError, 'changed'):
            organize.apply_plan(self.plan_path)
        self.assertFalse(self.destination.exists())

    def test_new_source_video_invalidates_plan_instead_of_silently_omitting_it(self):
        self.plan()
        (self.source / 'new-camera-clip.mp4').write_bytes(b'new video')
        with self.assertRaisesRegex(ValueError, 'membership changed'):
            organize.apply_plan(self.plan_path)
        self.assertFalse(self.destination.exists())

    def test_destination_race_after_preflight_is_not_reported_as_complete(self):
        plan = self.plan()
        target = self.destination / plan['entries'][0]['destination_relative']
        original_write = organize.atomic_owned
        calls = []
        def create_conflict_after_manifest(*args):
            result = original_write(*args)
            calls.append(args)
            if len(calls) == 1:
                target.parent.mkdir(parents=True)
                target.write_bytes(b'unowned racing writer')
            return result
        with patch.object(organize, 'atomic_owned', side_effect=create_conflict_after_manifest):
            with self.assertRaisesRegex(ValueError, 'refusing reuse'):
                organize.apply_plan(self.plan_path)
        self.assertEqual(target.read_bytes(), b'unowned racing writer')
        self.assertEqual(organize.json_read(self.destination / '.shadow-organizer' / 'manifest.json')['status'], 'incomplete')

    def test_missing_and_low_reviews_go_to_review_bucket(self):
        self.review['confidence'] = 'low'
        self.save_reviews()
        plan = self.plan()
        self.assertEqual(plan['entries'][0]['category_id'], '99')
        self.plan_path.unlink()
        self.save_reviews([])
        plan = self.plan()
        self.assertEqual(plan['entries'][0]['review_status'], 'needs_review')

    def test_failed_preview_goes_to_unreadable_bucket(self):
        self.entry.update(preview_status='failed', samples=[], contact_sheet=None)
        self.entry.pop('contact_sheet_sha256')
        self.save_inventory()
        self.save_reviews([])
        self.assertEqual(self.plan()['entries'][0]['category_id'], '98')

    def test_high_confidence_without_observed_sample_is_rejected(self):
        self.review['evidence']['sample_ids'] = []
        self.save_reviews()
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.plan()

    def test_unknown_sample_id_is_rejected(self):
        self.review['evidence']['sample_ids'] = ['S999']
        self.save_reviews()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.plan()

    def test_changed_reviews_invalidate_saved_plan(self):
        self.plan()
        self.review['title'] = '另一个标题'
        self.save_reviews()
        with self.assertRaisesRegex(ValueError, 'changed'):
            organize.apply_plan(self.plan_path)

    def test_stale_review_inventory_id_is_rejected(self):
        self.entry['duration_seconds'] = 6.0
        self.save_inventory()
        with self.assertRaisesRegex(ValueError, 'inventory_id'):
            self.plan()

    def test_changed_contact_sheet_cannot_be_accepted_as_new_evidence(self):
        self.sheet.write_bytes(b'wrong contact sheet')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.plan()

    def test_changed_supplemental_sheet_is_rejected(self):
        extra = self.workspace / 'extra.png'
        extra.write_bytes(b'extra')
        self.entry['supplemental_contact_sheets'] = [{'path': str(extra), 'sha256': sha(b'extra'), 'sample_ids': ['S001']}]
        self.save_inventory()
        self.save_reviews()
        extra.write_bytes(b'substituted')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.plan()

    def test_tampered_category_even_with_recomputed_checksum_is_rejected(self):
        plan = self.plan()
        plan['entries'][0]['category_id'] = '99'
        plan['entries'][0]['review_status'] = 'needs_review'
        plan['entries'][0]['destination_relative'] = plan['entries'][0]['destination_relative'].replace('04_产品物件', '99_待复核')
        plan['plan_id'] = organize.content_id(plan, 'plan')
        self.write(self.plan_path, plan)
        with self.assertRaisesRegex(ValueError, 'differ'):
            organize.apply_plan(self.plan_path)

    def test_path_traversal_even_with_recomputed_checksum_is_rejected(self):
        plan = self.plan()
        plan['entries'][0]['destination_relative'] = '../escape.mov'
        plan['plan_id'] = organize.content_id(plan, 'plan')
        self.write(self.plan_path, plan)
        with self.assertRaises(ValueError):
            organize.apply_plan(self.plan_path)
        self.assertFalse((self.root / 'escape.mov').exists())

    def test_destination_cannot_contain_source_or_be_inside_source(self):
        for destination in (self.source, self.source / 'sorted', self.root):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                organize.make_plan(self.workspace, self.reviews, destination)

    def test_review_and_plan_outputs_cannot_write_into_originals(self):
        with self.assertRaisesRegex(ValueError, 'original source'):
            organize.review_template(self.workspace, self.source / 'reviews.json')
        with self.assertRaisesRegex(ValueError, 'original source'):
            organize.make_plan(self.workspace, self.reviews, self.destination, output=self.source / 'plan.json')
        self.assertFalse((self.source / 'reviews.json').exists())
        self.assertFalse((self.source / 'plan.json').exists())

    def test_custom_category_cannot_overlap_evidence_workspace(self):
        value = organize.json_read(organize.DEFAULT_TAXONOMY)
        value['categories'][0]['folder'] = 'workspace'
        taxonomy = self.workspace / 'overlap-taxonomy.json'
        self.write(taxonomy, value)
        # Use a separate source so the parent destination is otherwise valid.
        other_source = self.root.parent / (self.root.name + '-source')
        other_source.mkdir()
        self.addCleanup(other_source.rmdir)
        # The original source/destination nesting check already protects this
        # layout; the direct helper check exercises the category rule via a mock.
        inventory = dict(self.inventory, source_root=str(other_source))
        with patch.object(organize, 'inventory_read', return_value=inventory):
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                organize.make_plan(self.workspace, self.reviews, self.root, taxonomy)

    def test_changed_copied_file_fails_verify_and_rerun(self):
        plan = self.plan()
        organize.apply_plan(self.plan_path)
        target = self.destination / plan['entries'][0]['destination_relative']
        target.write_bytes(b'changed by user')
        self.assertEqual(organize.verify_destination(self.destination)['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            organize.apply_plan(self.plan_path)
        self.assertEqual(target.read_bytes(), b'changed by user')

    def test_standalone_verification_refreshes_its_owned_report(self):
        plan = self.plan()
        organize.apply_plan(self.plan_path)
        (self.destination / plan['entries'][0]['destination_relative']).write_bytes(b'corrupted')
        report = organize.verify_destination(self.destination, write=True)
        saved = organize.json_read(self.destination / '.shadow-organizer' / 'verify.json')
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(saved, report)

    def test_copy_failure_can_resume_same_plan(self):
        self.plan()
        original_copy = organize.copy_exclusive
        calls = []
        def fail_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise OSError('simulated media copy failure')
            return original_copy(*args)
        with patch.object(organize, 'copy_exclusive', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'simulated'):
                organize.apply_plan(self.plan_path)
        result = organize.apply_plan(self.plan_path)
        self.assertEqual(result['verification']['status'], 'verified')
        self.assertEqual(result['reused_files_including_previews'], 1)

    def test_html_and_csv_escape_review_input(self):
        self.review.update(title='<script>alert(1)</script>', summary='=1+1 <script>', tags=['<img src=x>'])
        self.save_reviews()
        self.plan()
        organize.apply_plan(self.plan_path)
        meta = self.destination / '.shadow-organizer'
        page = (meta / 'catalog.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn("'=1+1", (meta / 'mapping.csv').read_text(encoding='utf-8-sig'))

    def test_windows_reserved_taxonomy_name_is_rejected(self):
        value = organize.json_read(organize.DEFAULT_TAXONOMY)
        value['categories'][0]['folder'] = 'CON'
        taxonomy = self.workspace / 'unsafe-taxonomy.json'
        self.write(taxonomy, value)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            organize.make_plan(self.workspace, self.reviews, self.destination, taxonomy)

    def test_unowned_metadata_is_not_overwritten(self):
        self.plan()
        meta = self.destination / '.shadow-organizer'
        meta.mkdir(parents=True)
        (meta / 'private.txt').write_bytes(b'unrelated')
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            organize.apply_plan(self.plan_path)
        self.assertEqual((meta / 'private.txt').read_bytes(), b'unrelated')

    def test_changed_owned_report_is_not_overwritten(self):
        self.plan()
        organize.apply_plan(self.plan_path)
        report = self.destination / '.shadow-organizer' / 'mapping.csv'
        report.write_bytes(b'user edits')
        with self.assertRaisesRegex(ValueError, 'report'):
            organize.apply_plan(self.plan_path)
        self.assertEqual(report.read_bytes(), b'user edits')

    def test_lock_refuses_concurrent_apply(self):
        plan = self.plan()
        _, meta = organize.destination_state(plan)
        with organize.destination_lock(meta), self.assertRaisesRegex(ValueError, 'Another organizer'):
            organize.apply_plan(self.plan_path)

    def test_symlink_destination_is_rejected(self):
        other = self.root / 'elsewhere'
        other.mkdir()
        try:
            self.destination.symlink_to(other, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            self.plan()


if __name__ == '__main__':
    unittest.main()
