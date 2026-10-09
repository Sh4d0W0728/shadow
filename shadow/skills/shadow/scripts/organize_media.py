#!/usr/bin/env python3
"""Read-only source scanning and verifiable visual evidence for shadow organizer.

No semantic category is inferred here. A person or vision-capable agent must
actually inspect the generated samples before recording a content review.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

try:
    import assets
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assets


MARKER = '.shadow-organizer.json'
LOCK = '.shadow-organizer.lock'
WIDTH, IMAGE_HEIGHT, LABEL_HEIGHT = 640, 360, 24
HEIGHT = IMAGE_HEIGHT + LABEL_HEIGHT
PREVIEW_VERSION = 1
NOTE = ('Actual decoded visual samples; no semantic review is implied. '
        'Requested seek times may differ from the nearest available decoded frame.')
FONT = {
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '10000', '11110', '00001', '00001', '11110'],
    '6': ['01110', '10000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00001', '01110'],
    'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
    ':': ['00000', '00100', '00100', '00000', '00100', '00100', '00000'],
    '.': ['00000', '00000', '00000', '00000', '00000', '00100', '00100'],
    ' ': ['00000'] * 7,
}


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _reject_links(path):
    """Check before resolve(), which would hide symlinks and Windows junctions."""
    current = Path(os.path.abspath(os.path.expanduser(str(path))))
    for part in [current, *current.parents]:
        if part.exists() or part.is_symlink():
            if _is_link(part):
                raise ValueError('Symlinks and reparse points are not accepted: ' + str(part))
    return current.resolve()


def _within(path, parent):
    return path == parent or parent in path.parents


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path, data):
    _reject_links(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _workspace(source, workspace):
    source, workspace = _reject_links(source), _reject_links(workspace)
    if not source.is_dir():
        raise ValueError('Source folder does not exist: ' + str(source))
    if _within(source, workspace) or _within(workspace, source):
        raise ValueError('Source and workspace must be separate, non-nested folders')
    if workspace.exists() and not workspace.is_dir():
        raise ValueError('Workspace must be a folder')
    marker = workspace / MARKER
    expected = {'schema_version': 1, 'kind': 'shadow-organizer',
                'source_root': str(source), 'workspace_root': str(workspace)}
    if marker.exists():
        _reject_links(marker)
        if _json(marker) != expected:
            raise ValueError('Workspace marker does not match this source and workspace')
    else:
        if workspace.exists() and any(workspace.iterdir()):
            raise ValueError('Refusing an existing nonempty folder without a shadow organizer marker')
        workspace.mkdir(parents=True, exist_ok=True)
        with marker.open('x', encoding='utf-8') as handle:
            json.dump(expected, handle, ensure_ascii=False, indent=2)
    return source, workspace


def load_inventory(workspace):
    workspace = _reject_links(workspace)
    marker = _json(workspace / MARKER)
    source, workspace = _workspace(marker['source_root'], workspace)
    inventory = _json(workspace / 'inventory.json')
    if (inventory.get('schema_version') != 1 or inventory.get('source_root') != str(source)
            or inventory.get('workspace_root') != str(workspace)):
        raise ValueError('Inventory does not belong to this workspace')
    if inventory.get('inventory_id') != inventory_id(inventory):
        raise ValueError('Inventory checksum does not match; preserve this workspace and prepare into a new one')
    return inventory


@contextmanager
def _lock(workspace):
    path = workspace / LOCK
    try:
        with path.open('x', encoding='utf-8') as handle:
            json.dump({'pid': os.getpid()}, handle)
    except FileExistsError:
        raise ValueError('Organizer is already running, or a previous run left a lock: ' + str(path))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _tools(ffmpeg, ffprobe):
    encoder = assets.find_tool('ffmpeg', ffmpeg)
    prober = assets.find_tool('ffprobe', ffprobe)
    if not encoder or not prober:
        raise ValueError('Both ffmpeg and ffprobe are required; install them or supply --ffmpeg and --ffprobe')
    return encoder, prober


def _config(encoder, sample_count):
    info = Path(encoder).stat()
    return {'preview_version': PREVIEW_VERSION, 'sample_count': sample_count,
            'width': WIDTH, 'image_height': IMAGE_HEIGHT, 'label_height': LABEL_HEIGHT,
            'ffmpeg': str(Path(encoder).resolve()), 'ffmpeg_size': info.st_size,
            'ffmpeg_mtime_ns': info.st_mtime_ns}


def _scan(source):
    videos, skipped = [], []
    def skip(path, reason):
        skipped.append({'path': str(path), 'relative_path': str(path.relative_to(source)), 'reason': reason})
    def visit(folder):
        try:
            children = sorted(folder.iterdir(), key=lambda p: (p.name.casefold(), p.name))
        except OSError as exc:
            skip(folder, 'unreadable-directory: ' + str(exc))
            return
        for path in children:
            try:
                if _is_link(path):
                    skip(path, 'symlink-or-reparse-point')
                elif path.is_dir():
                    visit(path)
                elif not path.is_file():
                    skip(path, 'not-a-regular-file')
                elif any(path.name.lower().endswith(ext) for ext in assets.INCOMPLETE):
                    skip(path, 'incomplete-file')
                elif path.suffix.lower() not in assets.VIDEO:
                    skip(path, 'unsupported-non-video')
                elif path.stat().st_size == 0:
                    skip(path, 'empty-file')
                else:
                    videos.append(path)
            except OSError as exc:
                skip(path, 'unreadable: ' + str(exc))
    visit(source)
    return videos, skipped


def _duration(metadata):
    # Sampling maps 0:v:0. A longer audio track must not schedule images after
    # that video ends; other video streams also must not supply its duration.
    video = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'), {})
    values = [video.get('duration'), metadata.get('duration_seconds')]
    for value in values:
        try:
            number = float(value)
            if math.isfinite(number) and number > 0:
                return number
        except (ValueError, TypeError):
            pass
    raise ValueError('Video duration is unknown or invalid; visual sampling cannot be safely scheduled')


def sample_times(duration, sample_count, fps=25):
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or not 1 <= sample_count <= 30:
        raise ValueError('sample_count must be an integer from 1 to 30')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Duration must be finite and positive')
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        fps = 25
    last = max(0.0, duration - max(1 / fps, 0.001))
    if sample_count == 1:
        return [last / 2]
    return [last * index / (sample_count - 1) for index in range(sample_count)]


def _timestamp(seconds):
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return '%02d:%02d:%02d.%03d' % (hours, minutes, seconds, ms)


def _label(rgb, text):
    output = bytearray(rgb + bytes([20, 24, 30]) * WIDTH * LABEL_HEIGHT)
    for column, char in enumerate(text):
        for y, row in enumerate(FONT[char]):
            for x, lit in enumerate(row):
                if lit == '1':
                    for dy in range(2):
                        for dx in range(2):
                            px, py = 8 + column * 12 + x * 2 + dx, IMAGE_HEIGHT + 5 + y * 2 + dy
                            if px < WIDTH:
                                offset = (py * WIDTH + px) * 3
                                output[offset:offset + 3] = bytes([230, 235, 242])
    return bytes(output)


def _png(path, width, height, rgb):
    if len(rgb) != width * height * 3:
        raise ValueError('Unexpected RGB image size')
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
    raw = b''.join(b'\0' + rgb[row * width * 3:(row + 1) * width * 3] for row in range(height))
    content = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    with path.open('xb') as handle:
        handle.write(content)


def _extract(encoder, source, seconds):
    # dar includes source sample aspect ratio; FFmpeg's default autorotation is retained.
    vf = ("scale=w='if(gt(dar,16/9),640,max(2,trunc(360*dar/2)*2))':"
          "h='if(gt(dar,16/9),max(2,trunc(640/dar/2)*2),360)',"
          'pad=640:360:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1')
    command = [encoder, '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-ss', '%.9f' % seconds, '-i', str(source), '-map', '0:v:0',
               '-frames:v', '1', '-an', '-sn', '-vf', vf, '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode or len(result.stdout) != WIDTH * IMAGE_HEIGHT * 3:
        raise ValueError('Frame extraction failed at %.6fs: %s' %
                         (seconds, result.stderr.decode('utf-8', errors='replace')[-1500:]))
    return result.stdout


def _make_previews(workspace, entry, encoder, times, start_index=1):
    preview_root = _reject_links(workspace / 'previews')
    preview_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.building-', dir=preview_root) as temp:
        directory = Path(temp)
        frames, samples = [], []
        for index, stamp in enumerate(times, start_index):
            sample_id = 'S%03d' % index
            frame = _label(_extract(encoder, Path(entry['source_paths'][0]), stamp), sample_id + ' ' + _timestamp(stamp))
            name = sample_id + '.png'
            _png(directory / name, WIDTH, HEIGHT, frame)
            samples.append({'sample_id': sample_id, 'requested_seconds': stamp,
                            'path': name, 'sha256': assets.digest(directory / name)})
            frames.append(frame)
        columns, rows = min(2, len(frames)), math.ceil(len(frames) / 2)
        sheet_width, sheet_height = columns * WIDTH, rows * HEIGHT
        sheet = bytearray(sheet_width * sheet_height * 3)
        for index, frame in enumerate(frames):
            left, top = (index % columns) * WIDTH, (index // columns) * HEIGHT
            for row in range(HEIGHT):
                offset = ((top + row) * sheet_width + left) * 3
                sheet[offset:offset + WIDTH * 3] = frame[row * WIDTH * 3:(row + 1) * WIDTH * 3]
        _png(directory / 'contact-sheet.png', sheet_width, sheet_height, bytes(sheet))
        destination = preview_root / (entry['asset_id'] + '-' + uuid.uuid4().hex[:12])
        directory.rename(destination)
    for row in samples:
        row['path'] = str(destination / row['path'])
    return {'samples': samples, 'contact_sheet': str(destination / 'contact-sheet.png'),
            'contact_sheet_sha256': assets.digest(destination / 'contact-sheet.png')}


def _valid_cached(entry, config, workspace):
    if entry.get('preview_status') != 'ready' or entry.get('preview_config') != config:
        return False
    samples = entry.get('samples', [])
    if len(samples) < config['sample_count']:
        return False
    files = [(s.get('path'), s.get('sha256')) for s in samples]
    files.append((entry.get('contact_sheet'), entry.get('contact_sheet_sha256')))
    files.extend((sheet.get('path'), sheet.get('sha256'))
                 for sheet in entry.get('supplemental_contact_sheets', []))
    for path, digest in files:
        if not path or not digest:
            return False
        try:
            target = _reject_links(path)
            if not _within(target, workspace / 'previews') or not target.is_file() or assets.digest(target) != digest:
                return False
        except (OSError, ValueError):
            return False
    return True


def inventory_id(inventory):
    """Changing media, evidence, source locations or sampling invalidates reviews."""
    identity = {'source_root': inventory['source_root'], 'preview_config': inventory.get('preview_config'),
                'entries': [{k: e.get(k) for k in ('asset_id', 'sha256', 'byte_size', 'source_paths',
                    'relative_paths', 'duration_seconds', 'preview_status', 'samples',
                    'contact_sheet', 'contact_sheet_sha256', 'supplemental_contact_sheets')}
                    for e in inventory['entries']],
                'skipped_files': inventory.get('skipped_files', [])}
    return 'inv_' + hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _save_inventory(workspace, inventory):
    inventory['inventory_id'] = inventory_id(inventory)
    _write_json(workspace / 'inventory.json', inventory)


def prepare(source, workspace, ffmpeg=None, ffprobe=None, sample_count=8):
    sample_times(1.0, sample_count)
    encoder, prober = _tools(ffmpeg, ffprobe)
    source, workspace = _workspace(source, workspace)
    with _lock(workspace):
        config = _config(encoder, sample_count)
        old = load_inventory(workspace) if (workspace / 'inventory.json').exists() else {'entries': []}
        cached = {e['sha256']: e for e in old['entries']}
        paths, skipped = _scan(source)
        inventory = {'schema_version': 1, 'source_root': str(source), 'workspace_root': str(workspace),
                     'preview_config': config, 'status': 'preparing', 'entries': [], 'skipped_files': skipped,
                     'review_required': True, 'note': NOTE}
        groups, identities = {}, {}
        for path in paths:
            try:
                if not _within(_reject_links(path), source):
                    raise ValueError('Scanned source no longer belongs to the source folder')
                before = path.stat()
                sha = assets.digest(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    skipped.append({'path': str(path), 'relative_path': str(path.relative_to(source)),
                                    'reason': 'source-changed-while-hashing'})
                    continue
                aid = 'v_' + sha[:16]
                if aid in identities and identities[aid] != sha:
                    raise ValueError('Asset ID hash-prefix collision: ' + aid)
                identities[aid] = sha
                if sha not in groups:
                    groups[sha] = {'asset_id': aid, 'sha256': sha, 'source_paths': [], 'relative_paths': [],
                                   'byte_size': after.st_size, 'metadata': {}, 'duration_seconds': None,
                                   'preview_status': 'failed', 'samples': [], 'contact_sheet': None}
                row = groups[sha]
                if row['byte_size'] != after.st_size:
                    raise ValueError('Full SHA-256 collision or changing input: ' + str(path))
                row['source_paths'].append(str(path))
                row['relative_paths'].append(str(path.relative_to(source)))
            except OSError as exc:
                skipped.append({'path': str(path), 'relative_path': str(path.relative_to(source)),
                                'reason': 'unreadable: ' + str(exc)})
        for sha, row in groups.items():
            previous = cached.get(sha)
            if previous and _valid_cached(previous, config, workspace):
                row.update({k: v for k, v in previous.items() if k not in ('source_paths', 'relative_paths')})
                try:
                    source_path = _reject_links(row['source_paths'][0])
                    if assets.digest(source_path) != sha:
                        raise ValueError('Source changed during cache verification; rerun prepare')
                except (OSError, ValueError) as exc:
                    row['preview_status'] = 'failed'
                    row['error'] = str(exc)
                    row['samples'] = []
                    row['contact_sheet'] = None
                    row.pop('contact_sheet_sha256', None)
            else:
                try:
                    metadata = assets.probe(Path(row['source_paths'][0]), prober)
                    row['metadata'] = metadata
                    if metadata.get('status') != 'ok':
                        raise ValueError('ffprobe failed: ' + metadata.get('reason', 'unknown error'))
                    video = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'), None)
                    if not video:
                        raise ValueError('No video stream exists in this file')
                    duration = _duration(metadata)
                    row['duration_seconds'] = duration
                    times = sample_times(duration, sample_count, video.get('fps', 25))
                    row.update(_make_previews(workspace, row, encoder, times))
                    if assets.digest(Path(row['source_paths'][0])) != sha:
                        raise ValueError('Source changed during visual sampling; rerun prepare')
                    row['preview_status'] = 'ready'
                    row['preview_config'] = config
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    row['preview_status'] = 'failed'
                    row['error'] = str(exc)
                    row['samples'] = []
                    row['contact_sheet'] = None
                    row.pop('contact_sheet_sha256', None)
            inventory['entries'].append(row)
            _save_inventory(workspace, inventory)
        inventory['status'] = 'prepared'
        inventory['counts'] = {'unique_videos': len(inventory['entries']),
            'source_files': sum(len(e['source_paths']) for e in inventory['entries']),
            'ready': sum(e['preview_status'] == 'ready' for e in inventory['entries']),
            'failed': sum(e['preview_status'] == 'failed' for e in inventory['entries']), 'skipped': len(skipped)}
        _save_inventory(workspace, inventory)
        return inventory


def sample_asset(workspace, asset_id, times, ffmpeg=None, ffprobe=None):
    """Append explicitly requested visual checks; return the updated inventory."""
    encoder, _ = _tools(ffmpeg, ffprobe)
    inventory = load_inventory(workspace)
    workspace = Path(inventory['workspace_root'])
    with _lock(workspace):
        row = next((e for e in inventory['entries'] if e['asset_id'] == asset_id), None)
        if row is None or row.get('preview_status') != 'ready':
            raise ValueError('Asset is missing or has no usable video preview: ' + asset_id)
        duration = row['duration_seconds']
        times = list(times)
        if not times or len(times) > 30 or any(isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) or t < 0 or t >= duration for t in times):
            raise ValueError('Supply 1..30 finite times, each at least zero and strictly before duration')
        source = _reject_links(row['source_paths'][0])
        if not _within(source, Path(inventory['source_root'])) or assets.digest(source) != row['sha256']:
            raise ValueError('Source changed or no longer belongs to inventory; rerun prepare')
        start = max(int(s['sample_id'][1:]) for s in row['samples']) + 1
        extra = _make_previews(workspace, row, encoder, times, start)
        if assets.digest(source) != row['sha256']:
            raise ValueError('Source changed during supplemental sampling; rerun prepare')
        row['samples'].extend(extra['samples'])
        row.setdefault('supplemental_contact_sheets', []).append({
            'path': extra['contact_sheet'], 'sha256': extra['contact_sheet_sha256'],
            'sample_ids': [s['sample_id'] for s in extra['samples']]})
        _save_inventory(workspace, inventory)
        return inventory


def main(argv=None):
    parser = assets.JsonArgumentParser(description='Generate evidence for visually organizing video files')
    commands = parser.add_subparsers(dest='action', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('source'); prep.add_argument('workspace')
    prep.add_argument('--sample-count', type=int, default=8)
    extra = commands.add_parser('sample')
    extra.add_argument('workspace'); extra.add_argument('asset_id')
    extra.add_argument('--time', type=float, action='append', required=True)
    for command in (prep, extra):
        command.add_argument('--ffmpeg'); command.add_argument('--ffprobe')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        result = prepare(args.source, args.workspace, args.ffmpeg, args.ffprobe, args.sample_count)
    else:
        result = sample_asset(args.workspace, args.asset_id, args.time, args.ffmpeg, args.ffprobe)
    assets.emit(result)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        assets.emit({'error': str(exc)})
        sys.exit(2)
