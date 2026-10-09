#!/usr/bin/env python3
"""Prepare silent, local evidence from a reference film using existing FFmpeg.

Produces source metadata, timestamped contact sheets, frame-change candidates,
targeted frames, per-second electrical audio levels and silence intervals.
No playback, network requests, content judgments or confirmed cut detection.
Python standard library only; FFmpeg and ffprobe must already be available.
"""
from __future__ import annotations

import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import re
import subprocess
import sys

try:
    import assets
    import organize_media as media
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assets
    import organize_media as media

WIDTH, IMAGE_HEIGHT, LABEL_HEIGHT = 320, 180, 24
HEIGHT = IMAGE_HEIGHT + LABEL_HEIGHT
FRAME_BYTES = WIDTH * IMAGE_HEIGHT * 3
SCALE = ("scale=w='if(gt(dar,16/9),320,max(2,trunc(180*dar/2)*2))':"
         "h='if(gt(dar,16/9),max(2,trunc(320/dar/2)*2),180)',"
         'pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1')
CANDIDATE_NOTE = ('Frame-change candidates only: motion, flashes and graphics can be false positives; '
                  'subtle cuts, dissolves and short shots can be missed. Candidate count is not shot '
                  'count, average shot length or a measure of editing rhythm.')
AUDIO_NOTE = ('Electrical signal levels only; not LUFS, true-peak, BPM, instrumentation, song '
              'identity, emotion or listening verification. Null dBFS means digital zero. '
              'Channels are combined for RMS without a mono downmix. Values use decoded '
              '16-bit samples resampled to 16 kHz, so high-frequency / true-peak analysis is unavailable.')


def _run(command, timeout=1800):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError('Media command failed: ' + result.stderr.decode('utf-8', errors='replace')[-3000:])
    return result


def _write_json(path, data):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def _label(rgb, text):
    output = bytearray(rgb + bytes([20, 24, 30]) * WIDTH * LABEL_HEIGHT)
    for column, char in enumerate(text):
        for y, row in enumerate(media.FONT[char]):
            for x, lit in enumerate(row):
                if lit == '1':
                    for dy in range(2):
                        for dx in range(2):
                            px, py = 6 + column * 12 + x * 2 + dx, IMAGE_HEIGHT + 5 + y * 2 + dy
                            if px < WIDTH:
                                offset = (py * WIDTH + px) * 3
                                output[offset:offset + 3] = bytes([230, 235, 242])
    return bytes(output)


def _sheets(frames, out, stem):
    """At most 8 x 8 frames per page; no image-library or system-font dependency."""
    pages = []
    for page_start in range(0, len(frames), 64):
        page = frames[page_start:page_start + 64]
        columns, rows = 8, math.ceil(len(page) / 8)
        width, height = columns * WIDTH, rows * HEIGHT
        canvas = bytearray(width * height * 3)
        for index, frame in enumerate(page):
            left, top = (index % columns) * WIDTH, (index // columns) * HEIGHT
            for row in range(HEIGHT):
                start = ((top + row) * width + left) * 3
                canvas[start:start + WIDTH * 3] = frame[row * WIDTH * 3:(row + 1) * WIDTH * 3]
        path = out / ('%s-%02d.png' % (stem, 1 + page_start // 64))
        media._png(path, width, height, bytes(canvas))
        pages.append(str(path))
    return pages


def _uniform(ffmpeg, source, out, duration, interval, maximum, origin):
    directory = out / 'uniform-frames'
    directory.mkdir()
    step = max(interval, duration / maximum)
    vf = f'setpts=PTS-({origin:.9f})/TB,select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step:.9f}),{SCALE},showinfo'
    result = _run([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-copyts', '-loglevel', 'info', '-i', str(source),
                   '-map', '0:v:0', '-an', '-sn', '-vf', vf, '-vsync', 'vfr',
                   '-frames:v', str(maximum), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
    log = result.stderr.decode('utf-8', errors='replace')
    (out / 'uniform-ffmpeg.log').write_text(log, encoding='utf-8')
    times = [float(value) for value in re.findall(r'Parsed_showinfo[^\n]*pts_time:([-+\d.eE]+)', log)]
    count, remainder = divmod(len(result.stdout), FRAME_BYTES)
    if remainder or count != len(times):
        raise ValueError('Decoded frame count does not match actual timestamps; preserve diagnostics')
    records, frames = [], []
    for index, stamp in enumerate(times, 1):
        frame = _label(result.stdout[(index - 1) * FRAME_BYTES:index * FRAME_BYTES], media._timestamp(stamp))
        path = directory / ('frame-%05d.png' % index)
        media._png(path, WIDTH, HEIGHT, frame)
        frames.append(frame)
        records.append({'path': str(path), 'time_seconds': stamp, 'timestamp_kind': 'decoded_pts'})
    return {'requested_interval_seconds': interval, 'effective_interval_seconds': step,
            'sampling_note': 'Interval grows if needed to cover the entire film within the frame budget; sparse frames cannot establish what happened between them.',
            'records': records, 'contact_sheets': _sheets(frames, out, 'contact')}


def _candidates(ffmpeg, source, threshold, origin, fallback_frame_duration):
    # showinfo precedes select so timing covers every decoded frame, including
    # films with no scene-change candidates and inaccurate stream.duration.
    vf = f'setpts=PTS-({origin:.9f})/TB,showinfo=checksum=0,select=gt(scene\\,{threshold}),metadata=print'
    result = _run([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-copyts', '-loglevel', 'info', '-i', str(source),
                   '-map', '0:v:0', '-an', '-sn', '-vf', vf,
                   '-vsync', 'vfr', '-f', 'null', '-'])
    log = result.stderr.decode('utf-8', errors='replace')
    records, decoded, stamp = [], [], None
    for line in log.splitlines():
        if 'Parsed_showinfo' in line:
            found = re.search(r'pts_time:([-+\d.eE]+)', line)
            if found:
                duration = re.search(r'duration_time:([-+\d.eE]+)', line)
                decoded.append((float(found.group(1)), float(duration.group(1)) if duration else 0))
        found = re.search(r'frame:\s*\d+.*pts_time:([-+\d.eE]+)', line)
        if found:
            stamp = float(found.group(1))
        found = re.search(r'lavfi.scene_score=([-+\d.eE]+)', line)
        if found and stamp is not None:
            records.append({'time_seconds': stamp, 'score': float(found.group(1)),
                            'review_status': 'unreviewed', 'confirmed_cut': None})
            stamp = None
    if not decoded or any(not math.isfinite(t) or not math.isfinite(d) for t, d in decoded):
        raise ValueError('Cannot establish finite decoded video timestamps')
    decoded.sort()
    start, last = decoded[0][0], decoded[-1][0]
    last_duration = decoded[-1][1]
    duration_source = 'decoded_frame_duration'
    if last_duration <= 0:
        previous = next((t for t, _ in reversed(decoded[:-1]) if t < last), None)
        last_duration = last - previous if previous is not None else fallback_frame_duration
        duration_source = 'previous_frame_interval' if previous is not None else 'nominal_frame_rate'
    _number(last_duration, 'last frame duration', 0.000001)
    timing = {'video_start_offset_seconds': start, 'video_last_frame_seconds': last,
              'video_end_seconds': last + last_duration, 'decoded_frame_count': len(decoded),
              'last_frame_duration_seconds': last_duration, 'last_frame_duration_source': duration_source}
    candidates = {'threshold': threshold, 'algorithm': 'FFmpeg select(scene) frame-difference score',
                  'limitation': CANDIDATE_NOTE, 'records': records}
    return candidates, timing, log


def _details(ffmpeg, source, out, video_start, video_last, candidates, count, offset, custom_times, origin):
    directory = out / 'detail-frames'
    directory.mkdir()
    records = []
    if count and candidates:
        amount = min(count, len(candidates))
        indexes = sorted(set(round(i * (len(candidates) - 1) / max(1, amount - 1)) for i in range(amount)))
        for index in indexes:
            candidate = candidates[index]['time_seconds']
            for side, delta in (('before', -offset), ('after', offset)):
                records.append({'requested_seconds': max(video_start, min(video_last, candidate + delta)),
                                'candidate_index': index, 'candidate_seconds': candidate, 'side': side})
    records.extend({'requested_seconds': stamp, 'side': 'custom'} for stamp in custom_times)

    def extract(record):
        stamp = record['requested_seconds']
        seek = min(video_last, max(video_start, stamp))
        result = _run([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-copyts', '-loglevel', 'info',
                       '-ss', str(seek), '-i', str(source),
                       '-map', '0:v:0', '-an', '-sn', '-vsync', 'vfr', '-frames:v', '1',
                       '-vf', f'setpts=PTS-({origin:.9f})/TB,{SCALE},showinfo',
                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], 120)
        if len(result.stdout) != FRAME_BYTES:
            raise ValueError('No complete decoded frame at requested time %.6f' % stamp)
        pts = re.search(r'Parsed_showinfo[^\n]*pts_time:([-+\d.eE]+)', result.stderr.decode('utf-8', errors='replace'))
        if not pts:
            raise ValueError('Targeted decoded frame has no timestamp')
        record['seek_seconds'] = seek
        record['decoded_seconds'] = float(pts.group(1))
        return _label(result.stdout, media._timestamp(record['decoded_seconds']))

    with ThreadPoolExecutor(max_workers=2) as pool:
        frames = list(pool.map(extract, records))
    for index, (record, frame) in enumerate(zip(records, frames), 1):
        path = directory / ('frame-%05d.png' % index)
        media._png(path, WIDTH, HEIGHT, frame)
        record['path'] = str(path)
    return {'timestamp_kind': 'labels_are_decoded_pts; requested_and_seek_times_are_recorded_separately', 'records': records,
            'limitation': 'Pairs are review aids, not cut confirmation; more than one cut may lie between the pair.',
            'contact_sheets': _sheets(frames, out, 'detail')}


def _dbfs(value):
    return round(20 * math.log10(value / 32768), 3) if value else None


def _audio(ffmpeg, source, out, stream, start_offset, duration_limit, silence_db, silence_duration):
    if stream is None:
        return {'present': False, 'per_second': [], 'silence_intervals': [], 'limitation': AUDIO_NOTE}
    channels = int(stream.get('channels', 0))
    if channels <= 0 or channels > 64:
        raise ValueError('Audio channel count is unsupported')
    rate, elapsed, records = 16000, 0, []
    log_path = out / 'audio-ffmpeg.log'
    with log_path.open('xb') as error_log:
        process = subprocess.Popen([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-loglevel', 'info',
                                    '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-af',
                                    f'asetpts=PTS-STARTPTS,silencedetect=noise={silence_db}dB:d={silence_duration}',
                                    '-ar', str(rate), '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'],
                                   stdout=subprocess.PIPE, stderr=error_log)
        try:
            while True:
                raw = process.stdout.read(rate * channels * 2)
                if not raw:
                    break
                if len(raw) % (2 * channels):
                    raise ValueError('Incomplete PCM audio frame')
                decoded_seconds = len(raw) / (2 * channels * rate)
                remaining = max(0, round((duration_limit - elapsed) * rate))
                raw = raw[:remaining * channels * 2]
                if not raw:
                    elapsed += decoded_seconds
                    continue
                samples = array('h')
                samples.frombytes(raw)
                if sys.byteorder != 'little':
                    samples.byteswap()
                rms = math.sqrt(sum(value * value for value in samples) / len(samples))
                peak = max(abs(value) for value in samples)
                seconds = len(samples) / channels / rate
                records.append({'start_seconds': start_offset + elapsed, 'end_seconds': start_offset + elapsed + seconds,
                                'rms_dbfs': _dbfs(rms), 'sample_peak_dbfs': _dbfs(peak)})
                elapsed += decoded_seconds
            if process.wait(timeout=120):
                raise ValueError('Audio decode failed; inspect audio-ffmpeg.log')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    measured_duration = min(elapsed, duration_limit)
    intervals, start = [], None
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        begin = re.search(r'silence_start:\s*([-+\d.eE]+)', line)
        end = re.search(r'silence_end:\s*([-+\d.eE]+)', line)
        if begin:
            start = max(0, float(begin.group(1)))
        if end and start is not None:
            finish = min(measured_duration, float(end.group(1)))
            if start < finish:
                intervals.append({'start_seconds': start_offset + start,
                                  'end_seconds': start_offset + finish})
            start = None
    if start is not None and start < measured_duration:
        intervals.append({'start_seconds': start_offset + start, 'end_seconds': start_offset + measured_duration})
    return {'present': True, 'channels_combined': channels, 'measurement_sample_rate': rate,
            'audio_start_offset_seconds': start_offset, 'audio_end_seconds': start_offset + measured_duration,
            'decoded_duration_seconds': elapsed, 'tail_padding_excluded_seconds': max(0, elapsed - measured_duration),
            'silence_threshold_dbfs': silence_db,
            'silence_minimum_seconds': silence_duration, 'limitation': AUDIO_NOTE,
            'per_second': records, 'silence_intervals': intervals}


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(name + ' is outside the supported range')
    return value


def analyze(source, out, ffmpeg=None, ffprobe=None, interval=2.0, max_frames=256,
            scene_threshold=0.3, pairs=24, pair_offset=0.2, times=(), silence_db=-45,
            silence_duration=0.3):
    _number(interval, 'interval', 0.001)
    _number(scene_threshold, 'scene_threshold', 0.000001, 1)
    _number(pair_offset, 'pair_offset', 0.001)
    _number(silence_db, 'silence_db', -120, -0.001)
    _number(silence_duration, 'silence_duration', 0.001)
    for value, name, minimum, maximum in ((max_frames, 'max_frames', 1, 512), (pairs, 'pairs', 0, 128)):
        if type(value) is not int:
            raise ValueError(name + ' must be an integer')
        _number(value, name, minimum, maximum)
    times = list(times)
    if len(times) > 512:
        raise ValueError('At most 512 targeted sample times are supported')
    for stamp in times:
        _number(stamp, 'sample time', 0)
    source, out = media._reject_links(source), media._reject_links(out)
    if not source.is_file():
        raise ValueError('Source video does not exist')
    if out.exists():
        raise ValueError('Output path already exists; choose a fresh directory to preserve evidence')
    if source == out or out in source.parents:
        raise ValueError('Output directory cannot contain the source')
    encoder, prober = media._tools(ffmpeg, ffprobe)
    before = source.stat()
    probe = json.loads(_run([prober, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(source)]).stdout)
    video = next((stream for stream in probe.get('streams', []) if stream.get('codec_type') == 'video'), None)
    if video is None:
        raise ValueError('Source has no video stream')
    audio_stream = next((stream for stream in probe.get('streams', []) if stream.get('codec_type') == 'audio'), None)
    format_start = float(probe.get('format', {}).get('start_time', 0))
    _number(format_start, 'format start time')
    container_duration = float(probe.get('format', {}).get('duration') or 0)
    _number(container_duration, 'container duration', 0)
    try:
        numerator, denominator = video.get('avg_frame_rate', '0/1').split('/')
        frame_duration = float(denominator) / float(numerator)
        _number(frame_duration, 'nominal frame duration', 0.000001)
    except (ValueError, ZeroDivisionError):
        frame_duration = 0.04
    audio_start = float(audio_stream.get('start_time', format_start)) - format_start if audio_stream else 0
    _number(audio_start, 'audio start offset')
    audio_duration = float(audio_stream.get('duration') or max(0, container_duration - audio_start)) if audio_stream else 0
    _number(audio_duration, 'audio duration', 0)
    identity = assets.digest(source)
    candidates, timing, scene_log = _candidates(encoder, source, scene_threshold, format_start, frame_duration)
    for stamp in times:
        if not timing['video_start_offset_seconds'] - 1e-6 <= stamp < timing['video_end_seconds']:
            raise ValueError('Targeted time must be inside video duration on the media-relative timeline')
    duration = max(container_duration, timing['video_end_seconds'], audio_start + audio_duration)
    _number(duration, 'media duration', 0.000001)
    out.mkdir(parents=True)
    _write_json(out / 'ffprobe.json', probe)
    (out / 'scene-ffmpeg.log').write_text(scene_log, encoding='utf-8')
    video_duration = timing['video_end_seconds'] - timing['video_start_offset_seconds']
    uniform = _uniform(encoder, source, out, video_duration, interval, max_frames, format_start)
    details = _details(encoder, source, out, timing['video_start_offset_seconds'], timing['video_last_frame_seconds'],
                       candidates['records'], pairs, pair_offset, times, format_start)
    sound = _audio(encoder, source, out, audio_stream, audio_start, min(audio_duration, duration - audio_start),
                   silence_db, silence_duration)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during analysis; preserve diagnostics and rerun on a stable file')
    result = {'schema_version': 1, 'source': str(source), 'sha256': identity, 'source_size_bytes': before.st_size,
              'duration_seconds': duration, 'width': video['width'], 'height': video['height'],
              'timeline': {'basis': 'media-relative seconds; raw timestamps minus format.start_time',
                           'format_start_time_seconds': format_start, **timing},
              'average_frame_rate': video.get('avg_frame_rate'), 'playback_performed': False,
              'tools': {'ffmpeg': encoder, 'ffprobe': prober},
              'decode_verification': {'first_video_full_pass': True,
                                      'first_audio_full_pass': True if audio_stream else None,
                                      'other_streams': 'not decoded'},
              'uniform_samples': uniform,
              'scene_change_candidates': candidates, 'detail_samples': details, 'audio_signal': sound,
              'interpretation_status': 'Evidence preparation only. Visual / listening analysis and editorial transfer require separate review.'}
    _write_json(out / 'analysis.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Existing local video; kept unchanged')
    parser.add_argument('--out', required=True, help='New analysis directory; existing paths are rejected')
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--max-frames', type=int, default=256)
    parser.add_argument('--scene-threshold', type=float, default=0.3)
    parser.add_argument('--pairs', type=int, default=24)
    parser.add_argument('--pair-offset', type=float, default=0.2)
    parser.add_argument('--times', type=float, nargs='*', default=[])
    parser.add_argument('--silence-db', type=float, default=-45)
    parser.add_argument('--silence-duration', type=float, default=0.3)
    args = parser.parse_args()
    try:
        result = analyze(**vars(args))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, 'Reference analysis failed: ' + str(exc) + '\n')
    print(json.dumps({'analysis': str(Path(args.out).resolve() / 'analysis.json'),
                      'duration_seconds': result['duration_seconds'],
                      'sampled_frames': len(result['uniform_samples']['records']),
                      'unreviewed_scene_candidates': len(result['scene_change_candidates']['records']),
                      'contact_sheets': result['uniform_samples']['contact_sheets'],
                      'detail_sheets': result['detail_samples']['contact_sheets'],
                      'playback_performed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
