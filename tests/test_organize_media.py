"""Safety, real decoded samples and resume behavior for source-video preparation."""
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shadow/skills/shadow/scripts'))
import organize_media as media


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.workspace = self.root / 'workspace'

    def tearDown(self):
        self.temporary.cleanup()

    def test_sampling_covers_beginning_and_tail_without_crossing_end(self):
        for duration in (0.001, 0.04, 0.05, 15, 86400):
            times = media.sample_times(duration, 8, 25)
            self.assertEqual(len(times), 8)
            self.assertEqual(times[0], 0)
            self.assertEqual(times, sorted(times))
            self.assertTrue(all(0 <= stamp < duration for stamp in times))
        self.assertGreater(media.sample_times(15, 8)[-1], 14.9)

    def test_invalid_sampling_configuration_fails(self):
        for count in (0, 31, True, 1.5):
            with self.assertRaises(ValueError):
                media.sample_times(1, count)
        for duration in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                media.sample_times(duration, 8)

    def test_sampling_duration_prefers_first_video_over_longer_audio(self):
        metadata = {'duration_seconds': 10, 'streams': [
            {'codec_type': 'audio', 'duration': '10'},
            {'codec_type': 'video', 'duration': '4'},
            {'codec_type': 'video', 'duration': '8'}]}
        self.assertEqual(media._duration(metadata), 4)

    def test_nested_folders_are_rejected_before_writing(self):
        for destination in (self.source, self.source / 'organizer', self.root):
            with self.assertRaisesRegex(ValueError, 'non-nested'):
                media._workspace(self.source, destination)
        self.assertEqual(list(self.source.iterdir()), [])

    def test_unrelated_nonempty_workspace_is_preserved(self):
        self.workspace.mkdir()
        note = self.workspace / 'important.txt'
        note.write_text('keep this', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            media._workspace(self.source, self.workspace)
        self.assertEqual(note.read_text(encoding='utf-8'), 'keep this')
        self.assertEqual(len(list(self.workspace.iterdir())), 1)

    def test_workspace_cannot_be_reused_for_other_source(self):
        media._workspace(self.source, self.workspace)
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            media._workspace(other, self.workspace)

    def test_unavailable_tools_fail_before_creating_workspace(self):
        with patch.object(media.assets, 'find_tool', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Both ffmpeg and ffprobe'):
                media.prepare(self.source, self.workspace)
        self.assertFalse(self.workspace.exists())

    def test_scan_skips_incomplete_nonvideo_and_empty_files(self):
        for name, data in [('clip.mp4.part', b'download'), ('notes.txt', b'note'), ('empty.mp4', b'')]:
            (self.source / name).write_bytes(data)
        videos, skipped = media._scan(self.source)
        self.assertEqual(videos, [])
        self.assertEqual({row['reason'] for row in skipped}, {'incomplete-file', 'unsupported-non-video', 'empty-file'})

    def test_scan_does_not_follow_directory_symlinks(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret.mp4').write_bytes(b'outside data')
        try:
            (self.source / 'linked').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is not permitted on this system')
        videos, skipped = media._scan(self.source)
        self.assertEqual(videos, [])
        self.assertEqual(skipped[0]['reason'], 'symlink-or-reparse-point')
        with self.assertRaisesRegex(ValueError, 'reparse'):
            media._workspace(self.source / 'linked', self.workspace)

    def test_running_prepare_lock_is_not_overwritten(self):
        media._workspace(self.source, self.workspace)
        lock = self.workspace / media.LOCK
        lock.write_text('owned by another process', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already running'):
            with media._lock(self.workspace):
                self.fail('Conflicting lock was entered')
        self.assertEqual(lock.read_text(encoding='utf-8'), 'owned by another process')

    def test_png_writer_creates_decodable_rgb_pixels(self):
        output = self.root / 'frame.png'
        media._png(output, 2, 1, bytes([255, 0, 0, 0, 0, 255]))
        width, height, pixels = decode_png(output)
        self.assertEqual((width, height), (2, 1))
        self.assertEqual(pixels, bytes([255, 0, 0, 0, 0, 255]))

    def test_asset_id_prefix_collision_is_explicit(self):
        for name in ('a.mp4', 'b.mp4'):
            (self.source / name).write_bytes(b'x')
        tools = (str(Path(sys.executable)), str(Path(sys.executable)))
        with patch.object(media, '_tools', return_value=tools), patch.object(
                media.assets, 'digest', side_effect=['a' * 64, 'a' * 16 + 'b' * 48]):
            with self.assertRaisesRegex(ValueError, 'hash-prefix collision'):
                media.prepare(self.source, self.workspace)


def decode_png(path):
    """Independent decoder for the module's RGB/filter-0 PNG output."""
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError('Not a PNG')
    offset, payload, dimensions = 8, [], None
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        kind, content = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        actual_crc = struct.unpack('>I', data[offset + 8 + length:offset + 12 + length])[0]
        if zlib.crc32(kind + content) & 0xffffffff != actual_crc:
            raise AssertionError('PNG checksum mismatch')
        if kind == b'IHDR':
            width, height, depth, color, _, _, _ = struct.unpack('>IIBBBBB', content)
            if depth != 8 or color != 2:
                raise AssertionError('Expected RGB8 PNG')
            dimensions = (width, height)
        elif kind == b'IDAT':
            payload.append(content)
        offset += 12 + length
    width, height = dimensions
    raw = zlib.decompress(b''.join(payload))
    stride = width * 3 + 1
    if len(raw) != height * stride or any(raw[row * stride] != 0 for row in range(height)):
        raise AssertionError('Unexpected PNG scanlines')
    return width, height, b''.join(raw[row * stride + 1:(row + 1) * stride] for row in range(height))


FFMPEG = media.assets.find_tool('ffmpeg')
FFPROBE = media.assets.find_tool('ffprobe')


@unittest.skipUnless(FFMPEG and FFPROBE, 'Real media integration requires ffmpeg and ffprobe')
class RealMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / '原始素材'
        self.source.mkdir()
        self.workspace = self.root / '查看工作区'

    def tearDown(self):
        self.temporary.cleanup()

    def video(self, name='clip.mp4', duration=1, color='red'):
        path = self.source / name
        result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', 'color=c=%s:s=160x90:r=25:d=%s' % (color, duration),
            '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', str(path)], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return path

    def test_real_frames_dedup_failures_and_read_only_sources(self):
        original = self.video('正常.mp4')
        nested = self.source / '第二机位'
        nested.mkdir()
        duplicate = nested / '相同.mov'
        shutil.copyfile(original, duplicate)
        (self.source / '坏视频.mp4').write_bytes(b'not a video')
        (self.source / '下载.mp4.part').write_bytes(b'incomplete')
        before = {str(p): media.assets.digest(p) for p in self.source.rglob('*') if p.is_file()}
        result = media.prepare(self.source, self.workspace, sample_count=8)
        self.assertEqual(result['counts'], {'unique_videos': 2, 'source_files': 3, 'ready': 1, 'failed': 1, 'skipped': 1})
        good = next(e for e in result['entries'] if e['preview_status'] == 'ready')
        self.assertEqual(len(good['source_paths']), 2)
        self.assertEqual(len(good['samples']), 8)
        self.assertEqual(good['samples'][0]['requested_seconds'], 0)
        self.assertGreater(good['samples'][-1]['requested_seconds'], .9)
        for sample in good['samples']:
            path = Path(sample['path'])
            width, height, rgb = decode_png(path)
            self.assertEqual((width, height), (640, 384))
            center = ((180 * width) + 320) * 3
            r, g, b = rgb[center:center + 3]
            self.assertGreater(r, 200)
            self.assertLess(max(g, b), 20)
            self.assertEqual(media.assets.digest(path), sample['sha256'])
        sw, sh, _ = decode_png(Path(good['contact_sheet']))
        self.assertEqual((sw, sh), (1280, 1536))
        after = {str(p): media.assets.digest(p) for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(result['review_required'])
        self.assertNotIn('category', good)

    def test_valid_cache_reused_and_corrupted_preview_regenerated(self):
        self.video()
        first = media.prepare(self.source, self.workspace, sample_count=2)
        with patch.object(media, '_extract', side_effect=AssertionError('Cache did not resume')):
            second = media.prepare(self.source, self.workspace, sample_count=2)
        self.assertEqual(first['inventory_id'], second['inventory_id'])
        path = Path(first['entries'][0]['samples'][0]['path'])
        path.write_bytes(b'corrupt image')
        third = media.prepare(self.source, self.workspace, sample_count=2)
        self.assertNotEqual(third['inventory_id'], first['inventory_id'])
        self.assertEqual(third['entries'][0]['preview_status'], 'ready')
        decode_png(Path(third['entries'][0]['samples'][0]['path']))

    def test_changed_source_never_reuses_stale_evidence(self):
        original = self.video(color='red')
        first = media.prepare(self.source, self.workspace, sample_count=1)
        replacement = self.video('replacement.mp4', color='blue')
        original.write_bytes(replacement.read_bytes())
        replacement.unlink()
        second = media.prepare(self.source, self.workspace, sample_count=1)
        self.assertNotEqual(first['entries'][0]['asset_id'], second['entries'][0]['asset_id'])
        self.assertNotEqual(first['inventory_id'], second['inventory_id'])
        _, _, rgb = decode_png(Path(second['entries'][0]['samples'][0]['path']))
        center = (180 * 640 + 320) * 3
        self.assertGreater(rgb[center + 2], 200)

    def test_shortest_clip_and_configuration_change_are_supported(self):
        self.video(duration=.04)
        first = media.prepare(self.source, self.workspace, sample_count=8)
        self.assertEqual(first['entries'][0]['preview_status'], 'ready', first['entries'][0].get('error'))
        self.assertEqual(len(first['entries'][0]['samples']), 8)
        second = media.prepare(self.source, self.workspace, sample_count=3)
        self.assertEqual(len(second['entries'][0]['samples']), 3)
        self.assertNotEqual(first['inventory_id'], second['inventory_id'])

    def test_supplemental_samples_change_inventory_and_survive_resume(self):
        self.video()
        first = media.prepare(self.source, self.workspace, sample_count=2)
        row = first['entries'][0]
        second = media.sample_asset(self.workspace, row['asset_id'], [.2, .7])
        self.assertNotEqual(first['inventory_id'], second['inventory_id'])
        self.assertEqual([s['sample_id'] for s in second['entries'][0]['samples']], ['S001', 'S002', 'S003', 'S004'])
        self.assertEqual(second['entries'][0]['samples'][2]['requested_seconds'], .2)
        with patch.object(media, '_extract', side_effect=AssertionError('Supplemental cache was lost')):
            third = media.prepare(self.source, self.workspace, sample_count=2)
        self.assertEqual(second['inventory_id'], third['inventory_id'])
        for bad_times in ([], [-1], [1], [float('nan')], [float('inf')], [True]):
            with self.assertRaises(ValueError):
                media.sample_asset(self.workspace, row['asset_id'], bad_times)

    def test_samples_follow_actual_red_green_blue_timeline(self):
        path = self.source / 'timeline.mp4'
        graph = ('color=c=red:s=160x90:r=25:d=1[r];color=c=lime:s=160x90:r=25:d=1[g];'
                 'color=c=blue:s=160x90:r=25:d=1[b];[r][g][b]concat=n=3:v=1:a=0')
        result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'lavfi', '-i', graph, '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', str(path)],
            capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        prepared = media.prepare(self.source, self.workspace, sample_count=3)
        samples = prepared['entries'][0]['samples']
        for index, sample in enumerate(samples):
            _, _, rgb = decode_png(Path(sample['path']))
            center = (180 * 640 + 320) * 3
            channels = rgb[center:center + 3]
            self.assertGreater(channels[index], 200)
            self.assertTrue(all(c < 30 for i, c in enumerate(channels) if i != index))

    def test_rotation_metadata_is_applied_to_preview(self):
        original = self.video()
        rotated = self.source / 'rotated.mp4'
        result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-display_rotation', '90', '-i', str(original), '-c', 'copy', str(rotated)],
            capture_output=True, timeout=60)
        if result.returncode:
            result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-i', str(original), '-c', 'copy', '-metadata:s:v:0', 'rotate=90', str(rotated)],
                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        rotation_data = media.assets.probe(rotated)['streams'][0]
        self.assertTrue(rotation_data.get('rotate_tag') or rotation_data.get('side_data'),
                        'Fixture must actually contain rotation metadata')
        original.unlink()
        prepared = media.prepare(self.source, self.workspace, sample_count=1)
        entry = prepared['entries'][0]
        self.assertEqual(entry['preview_status'], 'ready', entry.get('error'))
        _, _, rgb = decode_png(Path(entry['samples'][0]['path']))
        center = (180 * 640 + 320) * 3
        side = (180 * 640 + 100) * 3
        self.assertGreater(rgb[center], 200)
        self.assertLess(max(rgb[side:side + 3]), 10)

    def test_inventory_edit_is_detected_before_cache_reuse(self):
        self.video()
        prepared = media.prepare(self.source, self.workspace, sample_count=1)
        prepared['entries'][0]['duration_seconds'] = 999
        (self.workspace / 'inventory.json').write_text(json.dumps(prepared), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            media.prepare(self.source, self.workspace, sample_count=1)

    def test_audio_tail_does_not_fail_a_valid_shorter_video_stream(self):
        video = self.video(duration=1)
        combined = self.source / 'audio-tail.mp4'
        result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', str(video), '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', str(combined)],
            capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        video.unlink()
        prepared = media.prepare(self.source, self.workspace, sample_count=8)
        row = prepared['entries'][0]
        self.assertEqual(row['preview_status'], 'ready', row.get('error'))
        self.assertAlmostEqual(row['duration_seconds'], 1, places=2)
        self.assertGreater(row['metadata']['duration_seconds'], 1.9)
        self.assertTrue(all(s['requested_seconds'] < 1 for s in row['samples']))


if __name__ == '__main__':
    unittest.main()
