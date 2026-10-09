"""Evidence accuracy and source preservation for the local reference analyzer."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shadow/skills/shadow/scripts'))
import analyze_reference as reference


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'clip.mp4'
        self.source.write_bytes(b'not a real video')

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_source_and_existing_output_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            reference.analyze(self.root / 'missing.mp4', self.root / 'new')
        for output in (self.source, self.root):
            with self.assertRaisesRegex(ValueError, 'already exists'):
                reference.analyze(self.source, output)
        self.assertEqual(self.source.read_bytes(), b'not a real video')

    def test_invalid_parameters_fail_before_output_creation(self):
        cases = ({'interval': float('nan')}, {'interval': 0}, {'scene_threshold': 2},
                 {'max_frames': True}, {'max_frames': 513}, {'pairs': -1},
                 {'pair_offset': float('inf')}, {'silence_db': 0},
                 {'silence_duration': 0}, {'times': [float('nan')]}, {'times': [-1]})
        for index, kwargs in enumerate(cases):
            out = self.root / ('bad-%s' % index)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                reference.analyze(self.source, out, **kwargs)
            self.assertFalse(out.exists())

    def test_unavailable_tools_fail_without_writing(self):
        out = self.root / 'new'
        with patch.object(reference.media.assets, 'find_tool', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Both ffmpeg and ffprobe'):
                reference.analyze(self.source, out)
        self.assertFalse(out.exists())

    def test_symlink_output_is_not_followed(self):
        target = self.root / 'real'
        target.mkdir()
        link = self.root / 'linked'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation not permitted')
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            reference.analyze(self.source, link / 'new')
        self.assertEqual(list(target.iterdir()), [])


class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = reference.assets.find_tool('ffmpeg')
        cls.ffprobe = reference.assets.find_tool('ffprobe')
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest('Real media checks require existing FFmpeg and ffprobe')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'cuts-silence.mp4'
        command = [cls.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error']
        for color in ('red', 'blue', 'green', 'white'):
            command += ['-f', 'lavfi', '-i', f'color={color}:s=320x180:r=25:d=2']
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=8',
                    '-filter_complex', "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v];[4:a]volume='if(between(t,2,4),0,1)':eval=frame[a]",
                    '-map', '[v]', '-map', '[a]', '-c:v', 'mpeg4', '-q:v', '4', '-c:a', 'aac',
                    '-shortest', str(cls.source)]
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        cls.original_hash = hashlib.sha256(cls.source.read_bytes()).hexdigest()
        cls.out = cls.root / 'analysis'
        cls.result = reference.analyze(cls.source, cls.out, ffmpeg=cls.ffmpeg, ffprobe=cls.ffprobe,
                                      interval=1, scene_threshold=0.2, pairs=3, times=[0.1, 7.8])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'):
            cls.temp.cleanup()

    def test_known_frame_changes_are_candidates_pending_review(self):
        candidates = self.result['scene_change_candidates']
        self.assertEqual([row['time_seconds'] for row in candidates['records']], [2, 4, 6])
        self.assertTrue(all(row['confirmed_cut'] is None and row['review_status'] == 'unreviewed'
                            for row in candidates['records']))
        self.assertIn('false positives', candidates['limitation'])
        self.assertIn('not shot count', candidates['limitation'])

    def test_decoded_timestamps_pairs_and_sheet_geometry(self):
        uniform = self.result['uniform_samples']
        self.assertEqual([row['time_seconds'] for row in uniform['records']], list(range(8)))
        self.assertTrue(all(row['timestamp_kind'] == 'decoded_pts' for row in uniform['records']))
        with Path(uniform['contact_sheets'][0]).open('rb') as image:
            header = image.read(24)
        self.assertEqual(header[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', header[16:24]), (8 * reference.WIDTH, reference.HEIGHT))
        detail = self.result['detail_samples']['records']
        self.assertEqual(len(detail), 8)
        self.assertAlmostEqual(detail[0]['requested_seconds'], 1.8)
        self.assertAlmostEqual(detail[1]['requested_seconds'], 2.2)
        self.assertEqual([row['requested_seconds'] for row in detail[-2:]], [0.1, 7.8])

    def test_known_silence_and_electrical_level_without_listening_claim(self):
        sound = self.result['audio_signal']
        self.assertTrue(sound['present'])
        self.assertEqual(len(sound['per_second']), 8)
        self.assertEqual(sound['per_second'][0]['start_seconds'], 0)
        self.assertAlmostEqual(sound['per_second'][-1]['end_seconds'], 8, delta=0.1)
        silent = sound['silence_intervals']
        self.assertEqual(len(silent), 1)
        self.assertAlmostEqual(silent[0]['start_seconds'], 2, delta=0.1)
        self.assertAlmostEqual(silent[0]['end_seconds'], 4, delta=0.1)
        tone = sound['per_second'][0]
        self.assertGreater(tone['sample_peak_dbfs'], tone['rms_dbfs'])
        self.assertLess(tone['sample_peak_dbfs'], 0)
        self.assertIn('not LUFS', sound['limitation'])
        self.assertIn('BPM', sound['limitation'])
        self.assertFalse(self.result['playback_performed'])

    def test_source_hash_preserved_and_existing_evidence_not_overwritten(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        self.assertEqual(self.result['sha256'], self.original_hash)
        before = (self.out / 'analysis.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            reference.analyze(self.source, self.out)
        self.assertEqual((self.out / 'analysis.json').read_bytes(), before)
        loaded = json.loads(before)
        self.assertEqual(loaded['duration_seconds'], 8)

    def test_frame_budget_covers_tail_and_no_audio_is_explicit(self):
        silent = self.root / 'video-only.mp4'
        subprocess.run([self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                        '-i', str(self.source), '-map', '0:v:0', '-an', '-c:v', 'copy', str(silent)],
                       check=True, capture_output=True, timeout=30)
        result = reference.analyze(silent, self.root / 'budget-analysis', ffmpeg=self.ffmpeg,
                                   ffprobe=self.ffprobe, interval=0.01, max_frames=4, pairs=0)
        samples = result['uniform_samples']
        self.assertEqual(samples['effective_interval_seconds'], 2)
        self.assertEqual([row['time_seconds'] for row in samples['records']], [0, 2, 4, 6])
        self.assertFalse(result['audio_signal']['present'])
        self.assertEqual(result['audio_signal']['per_second'], [])
        self.assertEqual(result['detail_samples']['records'], [])

    def test_outside_video_sample_time_fails_before_writing(self):
        out = self.root / 'outside-time'
        with self.assertRaisesRegex(ValueError, 'inside video duration'):
            reference.analyze(self.source, out, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe, times=[8])
        self.assertFalse(out.exists())

    def test_invalid_source_media_fails_before_writing(self):
        bad = self.root / 'bad.mp4'
        bad.write_bytes(b'not a film')
        out = self.root / 'invalid-media'
        with self.assertRaisesRegex(ValueError, 'Media command failed'):
            reference.analyze(bad, out, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.assertFalse(out.exists())

    def test_truncated_media_with_valid_header_is_not_reported_complete(self):
        complete = self.root / 'faststart.mp4'
        subprocess.run([self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                        '-i', str(self.source), '-c', 'copy', '-movflags', '+faststart', str(complete)],
                       check=True, capture_output=True, timeout=30)
        data = complete.read_bytes()
        truncated = self.root / 'truncated.mp4'
        truncated.write_bytes(data[:len(data) * 2 // 3])
        header = subprocess.run([self.ffprobe, '-v', 'error', '-show_format', '-of', 'json', str(truncated)],
                                capture_output=True, timeout=30)
        self.assertEqual(header.returncode, 0, 'Fixture must have a readable container header')
        out = self.root / 'truncated-analysis'
        with self.assertRaisesRegex(ValueError, 'Media command failed|Audio decode failed'):
            reference.analyze(truncated, out, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.assertFalse((out / 'analysis.json').exists())

    def _offset_fixture(self, name, absolute_offset=0):
        delayed = self.root / (name + '-delayed.mp4')
        subprocess.run([self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                        '-i', str(self.source), '-f', 'lavfi', '-i', 'sine=sample_rate=16000:duration=10',
                        '-filter_complex', '[0:v]setpts=PTS+2/TB[v]', '-map', '[v]', '-map', '1:a',
                        '-vsync', '0', '-c:v', 'mpeg4', '-c:a', 'aac', str(delayed)],
                       check=True, capture_output=True, timeout=60)
        if not absolute_offset:
            return delayed
        shifted = self.root / (name + '-absolute.mp4')
        subprocess.run([self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                        '-i', str(delayed), '-c', 'copy', '-output_ts_offset', str(absolute_offset), str(shifted)],
                       check=True, capture_output=True, timeout=30)
        return shifted

    def _check_offset_analysis(self, name, absolute_offset):
        source = self._offset_fixture(name, absolute_offset)
        probe = json.loads(subprocess.check_output([self.ffprobe, '-v', 'error', '-show_format',
                                                   '-show_streams', '-of', 'json', str(source)]))
        origin = float(probe['format']['start_time'])
        video = next(row for row in probe['streams'] if row['codec_type'] == 'video')
        expected_start = float(video['start_time']) - origin
        # Both requests are later than the 7.96s video.stream duration. The final
        # request lies inside the final frame's display interval.
        times = [expected_start + 7.0, expected_start + 7.94]
        result = reference.analyze(source, self.root / (name + '-analysis'),
                                   ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
                                   interval=0.01, max_frames=4, scene_threshold=0.2, pairs=3, times=times)
        timeline = result['timeline']
        self.assertAlmostEqual(timeline['format_start_time_seconds'], origin)
        self.assertAlmostEqual(timeline['video_start_offset_seconds'], expected_start, delta=0.0001)
        # MPEG-4/MP4 edit lists on some FFmpeg builds expose 199 instead of 200
        # decoded frames; the analyzer must use the actual decoded span.
        self.assertGreaterEqual(timeline['decoded_frame_count'], 199)
        self.assertLessEqual(timeline['decoded_frame_count'], 200)
        self.assertAlmostEqual(timeline['video_end_seconds'],
                               expected_start + timeline['decoded_frame_count'] / 25, delta=0.001)
        self.assertAlmostEqual(result['duration_seconds'], float(probe['format']['duration']), delta=0.001)
        stamps = [row['time_seconds'] for row in result['uniform_samples']['records']]
        for actual, delta in zip(stamps, (0, 2, 4, 6)):
            self.assertAlmostEqual(actual, expected_start + delta, delta=0.001)
        candidates = result['scene_change_candidates']['records']
        for actual, delta in zip(candidates, (2, 4, 6)):
            self.assertAlmostEqual(actual['time_seconds'], expected_start + delta, delta=0.001)
        details = result['detail_samples']['records']
        self.assertGreater(details[5]['requested_seconds'], expected_start + 6.1)
        for row in details:
            self.assertGreaterEqual(row['decoded_seconds'] + 0.001, expected_start)
            self.assertLess(row['decoded_seconds'], timeline['video_end_seconds'])
            self.assertAlmostEqual(row['decoded_seconds'], row['seek_seconds'], delta=0.041)
        self.assertEqual([row['requested_seconds'] for row in details[-2:]], times)
        self.assertAlmostEqual(details[-1]['decoded_seconds'], timeline['video_last_frame_seconds'], delta=0.001)
        audio = result['audio_signal']
        self.assertLessEqual(audio['per_second'][-1]['end_seconds'], result['duration_seconds'] + 1e-6)
        self.assertAlmostEqual(audio['audio_end_seconds'], result['duration_seconds'], delta=0.001)
        self.assertGreater(audio['tail_padding_excluded_seconds'], 0)
        # Asking for the audio-only lead-in must not silently substitute a later frame.
        out = self.root / (name + '-before-video')
        with self.assertRaisesRegex(ValueError, 'inside video duration'):
            reference.analyze(source, out, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
                              times=[expected_start - 0.5])
        self.assertFalse(out.exists())

    def test_delayed_video_and_audio_share_media_relative_timeline(self):
        self._check_offset_analysis('delayed', 0)

    def test_absolute_nonzero_container_origin_and_aac_padding(self):
        self._check_offset_analysis('absolute', 100)


if __name__ == '__main__':
    unittest.main()
