#!/usr/bin/env python3
"""Evidence-backed video organization. Semantic reviews come from viewed frames.

Only copies are supported. Originals are never renamed, moved, or modified.
"""
import argparse
from contextlib import contextmanager
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import quote
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assets
import organize_media as media

META = '.shadow-organizer'
KIND = 'shadow-organized-copies'
NOTE = ('Categories describe reviewed visual samples, not a full-video or audio review. '
        'Uncertain and unreviewed clips remain in category 99; unreadable clips in 98. '
        'Source files are preserved; identical bytes produce one destination copy.')
DEFAULT_TAXONOMY = Path(__file__).resolve().parents[1] / 'assets' / 'organize-categories.json'
RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def content_id(value, prefix):
    value = {k: v for k, v in value.items() if k != prefix + '_id'}
    return prefix + '_' + hashlib.sha256(canonical(value)).hexdigest()


def safe_path(path):
    return media._reject_links(path)


def within(path, root):
    return path == root or root in path.parents


def require_file(path, digest=None):
    path = safe_path(path)
    if not path.is_file():
        raise ValueError('Required file is missing: ' + str(path))
    if digest is not None and assets.digest(path) != digest:
        raise ValueError('File changed; prepare and review again: ' + str(path))
    return path


def safe_component(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or len(value) > 100 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.rstrip(' .') != value or RESERVED.match(value)):
        raise ValueError('Unsafe Windows-compatible filename component: ' + repr(value))
    return value


def title_slug(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:42].strip(' .') or '未命名'
    return '_' + value if RESERVED.match(value) else value


def ensure_no_case_collision(path):
    """Reject a differently-cased peer even on case-sensitive test filesystems."""
    path = safe_path(path)
    for part in reversed([path, *path.parents]):
        if part.parent.is_dir():
            for peer in part.parent.iterdir():
                if peer.name.casefold() == part.name.casefold() and peer.name != part.name:
                    raise ValueError('Case-insensitive path collision: ' + str(peer))
    return path


def write_new(path, data):
    path = ensure_no_case_collision(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)


def write_json_new(path, value):
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') + b'\n')


def atomic_owned(path, value):
    path = safe_path(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        write_json_new(temp, value)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def taxonomy_read(path):
    path = require_file(path)
    value = json_read(path)
    if set(value) != {'schema_version', 'categories'} or value['schema_version'] != 1:
        raise ValueError('Taxonomy requires schema_version=1 and categories')
    categories = value['categories']
    if not isinstance(categories, list) or not categories:
        raise ValueError('Taxonomy categories must be a nonempty list')
    found, folders = {}, set()
    for category in categories:
        if set(category) != {'id', 'folder', 'label', 'description'}:
            raise ValueError('Category fields must be id, folder, label, description')
        key = category['id']
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', key) or key in found:
            raise ValueError('Category IDs must be unique safe strings')
        folder = safe_component(category['folder'])
        if folder.casefold() in folders or folder.casefold() == META.casefold():
            raise ValueError('Category folder collision')
        for field in ('label', 'description'):
            if not isinstance(category[field], str) or not category[field].strip():
                raise ValueError('Category ' + field + ' must be a nonempty string')
        found[key], folders = category, folders | {folder.casefold()}
    if not {'98', '99'} <= set(found):
        raise ValueError('Taxonomy must retain reserved categories 98 and 99')
    return path, value, found


def inventory_read(workspace, check_files=True):
    inventory = media.load_inventory(workspace)
    if inventory.get('inventory_id') != media.inventory_id(inventory):
        raise ValueError('Inventory digest does not match; run prepare again')
    source, workspace = safe_path(inventory['source_root']), safe_path(inventory['workspace_root'])
    if check_files:
        scanned, skipped = media._scan(source)
        expected = {str(path) for entry in inventory['entries'] for path in entry['source_paths']}
        if {str(path) for path in scanned} != expected or skipped != inventory.get('skipped_files', []):
            raise ValueError('Source folder membership changed; run prepare and review again')
    seen = set()
    for entry in inventory['entries']:
        sha = entry['sha256']
        key = entry['asset_id']
        if not re.fullmatch(r'[0-9a-f]{64}', sha) or key != 'v_' + sha[:16] or key in seen:
            raise ValueError('Invalid or duplicate asset identity')
        seen.add(key)
        if not entry['source_paths'] or len(entry['source_paths']) != len(entry['relative_paths']):
            raise ValueError('Source mappings are incomplete')
        for raw, relative in zip(entry['source_paths'], entry['relative_paths']):
            path = safe_path(raw)
            if not within(path, source) or path == source or str(path.relative_to(source)) != relative:
                raise ValueError('Source mapping escapes its source root')
            if check_files:
                require_file(path, sha)
                if path.stat().st_size != entry['byte_size']:
                    raise ValueError('Source size changed: ' + str(path))
        ids = set()
        for sample in entry['samples']:
            if not isinstance(sample.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', sample['sha256']):
                raise ValueError('Sample evidence requires a SHA-256 digest')
            if sample['sample_id'] in ids:
                raise ValueError('Duplicate sample ID')
            ids.add(sample['sample_id'])
            path = safe_path(sample['path'])
            if not within(path, workspace) or path == workspace:
                raise ValueError('Sample escapes workspace')
            require_file(path, sample['sha256'])
        if entry['preview_status'] not in ('ready', 'failed'):
            raise ValueError('Invalid preview status')
        sheets = list(entry.get('supplemental_contact_sheets', []))
        if entry.get('contact_sheet'):
            sheets.append({'path': entry['contact_sheet'], 'sha256': entry.get('contact_sheet_sha256')})
        for sheet in sheets:
            path = safe_path(sheet['path'])
            if not within(path, workspace) or not sheet.get('sha256'):
                raise ValueError('Contact sheet lacks verifiable workspace evidence')
            require_file(path, sheet['sha256'])
    return inventory


def review_template(workspace, output):
    inventory = inventory_read(workspace, check_files=False)
    if within(safe_path(output), safe_path(inventory['source_root'])):
        raise ValueError('Review output cannot be inside the original source folder')
    value = {'schema_version': 1, 'inventory_id': inventory['inventory_id'], 'reviews': []}
    for entry in inventory['entries']:
        value['reviews'].append({'asset_id': entry['asset_id'], 'source_sha256': entry['sha256'],
                                'category_id': '99', 'title': '待复核', 'summary': '', 'tags': [],
                                'confidence': 'low', 'evidence': {'method': 'sampled_frames',
                                'sample_ids': [], 'note': ''}})
    write_json_new(output, value)
    return {'status': 'template_created', 'path': str(safe_path(output)), 'assets': len(value['reviews']),
            'note': 'View actual images before filling reviews. This template is not a content review.'}


def reviews_read(path, inventory, categories):
    path = require_file(path)
    value = json_read(path)
    if (set(value) != {'schema_version', 'inventory_id', 'reviews'} or value['schema_version'] != 1
            or value['inventory_id'] != inventory['inventory_id'] or not isinstance(value['reviews'], list)):
        raise ValueError('Reviews must bind the current inventory_id and schema_version=1')
    assets_by_id = {e['asset_id']: e for e in inventory['entries']}
    found = {}
    fields = {'asset_id', 'source_sha256', 'category_id', 'title', 'summary', 'tags', 'confidence', 'evidence'}
    for review in value['reviews']:
        if not isinstance(review, dict) or set(review) != fields:
            raise ValueError('Invalid review fields; use review-template')
        key = review['asset_id']
        if key not in assets_by_id or key in found:
            raise ValueError('Unknown or repeated review asset_id')
        entry = assets_by_id[key]
        if review['source_sha256'] != entry['sha256'] or review['category_id'] not in categories:
            raise ValueError('Review SHA or category does not match')
        if review['confidence'] not in ('high', 'medium', 'low'):
            raise ValueError('Review confidence must be high, medium, or low')
        for field in ('title', 'summary'):
            if not isinstance(review[field], str) or len(review[field]) > 2000:
                raise ValueError('Review ' + field + ' must be text up to 2000 characters')
        if not review['title'].strip():
            raise ValueError('Review title must not be empty')
        if (not isinstance(review['tags'], list) or len(review['tags']) > 50
                or any(not isinstance(t, str) or not t.strip() or len(t) > 80 for t in review['tags'])):
            raise ValueError('Tags must be a list of short nonempty strings')
        evidence = review['evidence']
        if (not isinstance(evidence, dict) or set(evidence) != {'method', 'sample_ids', 'note'}
                or evidence['method'] != 'sampled_frames' or not isinstance(evidence['note'], str)
                or not isinstance(evidence['sample_ids'], list)
                or any(not isinstance(s, str) for s in evidence['sample_ids'])):
            raise ValueError('Evidence must reference sampled_frames and actual sample_ids')
        ids = evidence['sample_ids']
        if len(ids) != len(set(ids)) or not set(ids) <= {s['sample_id'] for s in entry['samples']}:
            raise ValueError('Evidence references missing or repeated sample IDs')
        if review['confidence'] in ('high', 'medium') and (not ids or not review['summary'].strip() or not evidence['note'].strip()):
            raise ValueError('High/medium confidence requires sampled evidence, summary, and observation note')
        if review['category_id'] == '98' and entry['preview_status'] != 'failed':
            raise ValueError('Category 98 is reserved for unreadable previews')
        found[key] = review
    return path, value, found


def build_entries(inventory, reviewed, categories, workspace):
    entries = []
    ordered = sorted(inventory['entries'], key=lambda e: (min(p.casefold() for p in e['relative_paths']), e['asset_id']))
    for number, entry in enumerate(ordered, 1):
        review = reviewed.get(entry['asset_id'])
        state = 'reviewed' if review and review['confidence'] in ('high', 'medium') else 'needs_review'
        category_id = review['category_id'] if state == 'reviewed' else '99'
        if entry['preview_status'] == 'failed':
            category_id, state = '98', 'unreadable'
        if category_id == '99':
            state = 'needs_review'
        category = categories[category_id]
        title = review['title'] if review else '待复核'
        suffix = Path(entry['source_paths'][0]).suffix.lower()
        name = '{:06d}_{}_{}{}'.format(number, title_slug(title), entry['sha256'][:8], suffix)
        relative = category['folder'] + '/' + name
        preview = entry.get('contact_sheet')
        if preview:
            preview_path = require_file(preview)
            if not within(preview_path, workspace):
                raise ValueError('Contact sheet escapes workspace')
            preview = {'source': str(preview_path), 'sha256': assets.digest(preview_path),
                       'relative': META + '/previews/' + entry['asset_id'] + preview_path.suffix.lower()}
        entries.append({'asset_id': entry['asset_id'], 'sha256': entry['sha256'], 'byte_size': entry['byte_size'],
                        'number': number, 'source_paths': entry['source_paths'], 'relative_paths': entry['relative_paths'],
                        'destination_relative': relative, 'category_id': category_id, 'category_label': category['label'],
                        'title': title, 'summary': review['summary'] if review else '', 'tags': review['tags'] if review else [],
                        'review_status': state, 'confidence': review['confidence'] if review else 'low',
                        'evidence': review['evidence'] if review else None, 'duration_seconds': entry.get('duration_seconds'),
                        'preview': preview, 'preview_error': entry.get('error')})
    return entries


def make_plan(workspace, reviews, destination, taxonomy=None, output=None):
    inventory = inventory_read(workspace)
    taxonomy_path, taxonomy_value, categories = taxonomy_read(taxonomy or DEFAULT_TAXONOMY)
    reviews_path, reviews_value, reviewed = reviews_read(reviews, inventory, categories)
    destination = ensure_no_case_collision(destination)
    source = safe_path(inventory['source_root'])
    workspace = safe_path(inventory['workspace_root'])
    if within(source, destination) or within(destination, source):
        raise ValueError('Source and destination must be separate, non-nested folders')
    if destination == workspace or within(destination, workspace):
        raise ValueError('Destination cannot be the workspace or a child of it')
    if destination.exists() and not destination.is_dir():
        raise ValueError('Destination must be a directory')
    for category in categories.values():
        category_path = safe_path(destination / category['folder'])
        if within(category_path, workspace) or within(workspace, category_path):
            raise ValueError('A category output folder overlaps the evidence workspace')
    entries = build_entries(inventory, reviewed, categories, workspace)
    plan = {'schema_version': 1, 'kind': KIND, 'mode': 'copy', 'inventory_id': inventory['inventory_id'],
            'workspace_root': str(workspace), 'source_root': str(source), 'destination_root': str(destination),
            'inventory_path': str(workspace / 'inventory.json'),
            'reviews_path': str(reviews_path), 'reviews_sha256': assets.digest(reviews_path),
            'taxonomy_path': str(taxonomy_path), 'taxonomy_sha256': assets.digest(taxonomy_path),
            'entries': entries, 'skipped_files': inventory.get('skipped_files', []), 'note': NOTE}
    plan['plan_id'] = content_id(plan, 'plan')
    path = safe_path(output or workspace / 'plan.json')
    if within(path, source):
        raise ValueError('Plan output cannot be inside the original source folder')
    write_json_new(path, plan)
    return {'status': 'planned', 'plan_path': str(path), 'plan_id': plan['plan_id'],
            'destination': str(destination), 'counts': counts(entries), 'note': NOTE}


def counts(entries):
    return {'unique_videos': len(entries), 'source_files': sum(len(e['source_paths']) for e in entries),
            'duplicate_files': sum(len(e['source_paths']) - 1 for e in entries),
            'reviewed': sum(e['review_status'] == 'reviewed' for e in entries),
            'needs_review': sum(e['review_status'] == 'needs_review' for e in entries),
            'unreadable': sum(e['review_status'] == 'unreadable' for e in entries)}


def plan_read(path):
    plan = json_read(require_file(path))
    if (plan.get('schema_version') != 1 or plan.get('kind') != KIND or plan.get('mode') != 'copy'
            or plan.get('plan_id') != content_id(plan, 'plan')):
        raise ValueError('Invalid or changed plan; generate a fresh plan')
    inventory = inventory_read(plan['workspace_root'])
    if inventory['inventory_id'] != plan['inventory_id']:
        raise ValueError('Inventory changed after planning; review and plan again')
    require_file(plan['reviews_path'], plan['reviews_sha256'])
    require_file(plan['taxonomy_path'], plan['taxonomy_sha256'])
    # Rebuild in memory to prevent a modified plan with a recomputed checksum
    # from changing classifications or escaping the destination.
    _, _, categories = taxonomy_read(plan['taxonomy_path'])
    _, _, reviewed = reviews_read(plan['reviews_path'], inventory, categories)
    if plan['entries'] != build_entries(inventory, reviewed, categories, safe_path(plan['workspace_root'])):
        raise ValueError('Plan entries differ from reviewed evidence; regenerate the plan')
    source, destination = safe_path(plan['source_root']), safe_path(plan['destination_root'])
    if source != safe_path(inventory['source_root']) or within(source, destination) or within(destination, source):
        raise ValueError('Invalid source/destination relationship')
    by_id = {entry['asset_id']: entry for entry in inventory['entries']}
    if len(plan['entries']) != len(by_id) or {e['asset_id'] for e in plan['entries']} != set(by_id):
        raise ValueError('Plan asset set changed')
    seen = set()
    for entry in plan['entries']:
        original = by_id[entry['asset_id']]
        for field in ('sha256', 'byte_size', 'source_paths', 'relative_paths'):
            if entry[field] != original[field]:
                raise ValueError('Plan source identity changed')
        relative = entry['destination_relative']
        parts = relative.split('/')
        if len(parts) != 2 or '\\' in relative:
            raise ValueError('Invalid destination relative path')
        for part in parts:
            safe_component(part)
        if parts[0] != categories[entry['category_id']]['folder'] or relative.casefold() in seen:
            raise ValueError('Destination category mismatch or collision')
        seen.add(relative.casefold())
        preview = entry.get('preview')
        if preview:
            preview_path = require_file(preview['source'], preview['sha256'])
            expected = META + '/previews/' + entry['asset_id'] + preview_path.suffix.lower()
            if preview['relative'] != expected or not within(preview_path, safe_path(plan['workspace_root'])):
                raise ValueError('Invalid preview destination')
    return plan


@contextmanager
def destination_lock(meta):
    path = safe_path(meta / 'run.lock')
    try:
        if path.exists() and path.read_bytes() != b'0':
            raise ValueError('Unrecognized organizer lock file')
    except PermissionError as exc:
        raise ValueError('Another organizer process is using this destination') from exc
    try:
        with path.open('xb') as first:
            first.write(b'0')
    except FileExistsError:
        pass
    handle = path.open('r+b')
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except (OSError, BlockingIOError) as exc:
        if not locked:
            raise ValueError('Another organizer process is using this destination') from exc
        raise
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def destination_state(plan):
    root = ensure_no_case_collision(plan['destination_root'])
    root.mkdir(parents=True, exist_ok=True)
    meta = ensure_no_case_collision(root / META)
    marker = meta / 'owner.json'
    expected = {'schema_version': 1, 'kind': KIND, 'plan_id': plan['plan_id'], 'destination_root': str(root)}
    if meta.exists():
        if not meta.is_dir() or not marker.is_file() or json_read(require_file(marker)) != expected:
            raise ValueError('Destination metadata belongs to another plan or is unrecognized; use a new destination')
    else:
        meta.mkdir()
        write_json_new(marker, expected)
    return root, meta


def output_specs(plan):
    for entry in plan['entries']:
        yield entry['destination_relative'], entry['source_paths'][0], entry['sha256']
        if entry.get('preview'):
            p = entry['preview']
            yield p['relative'], p['source'], p['sha256']


def copy_exclusive(source, destination, meta, sha):
    safe_path(source)
    destination = ensure_no_case_collision(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = meta / ('copy-' + uuid.uuid4().hex + '.part')
    try:
        with temp.open('xb') as writer, Path(source).open('rb') as reader:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if assets.digest(temp) != sha:
            raise ValueError('Source changed during copy: ' + str(source))
        safe_path(destination)
        if os.name == 'nt':
            os.rename(temp, destination)  # Windows refuses an existing destination.
        else:
            os.link(temp, destination)  # Atomic no-replace on POSIX.
            temp.unlink()
    finally:
        temp.unlink(missing_ok=True)


def report_bytes(plan):
    mapping = []
    for entry in plan['entries']:
        for source, relative in zip(entry['source_paths'], entry['relative_paths']):
            mapping.append({'number': entry['number'], 'asset_id': entry['asset_id'], 'source_path': source,
                            'source_relative': relative, 'destination_relative': entry['destination_relative'],
                            'category': entry['category_label'], 'review_status': entry['review_status'],
                            'title': entry['title'], 'summary': entry['summary'], 'sha256': entry['sha256']})
    stream = io.StringIO(newline='')
    fields = ['number', 'asset_id', 'source_path', 'source_relative', 'destination_relative', 'category',
              'review_status', 'title', 'summary', 'sha256']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in mapping:
        # Protect Excel from formulas embedded in source names and review prose.
        writer.writerow({key: ("'" + value if isinstance(value, str) and value[:1] in '=+-@\t\r' else value)
                         for key, value in row.items()})
    rows, navigation = [], []
    esc = lambda value: html.escape(str(value), quote=True)
    labels = {'reviewed': '已按画面归类', 'needs_review': '待复核', 'unreadable': '无法读取画面'}
    seen_categories = set()
    for entry in plan['entries']:
        preview = entry.get('preview')
        image = ('<img loading="lazy" alt="抽帧接触表" src="' + quote(preview['relative'][len(META) + 1:], safe='/') + '">' if preview else '')
        anchor = ''
        if entry['category_id'] not in seen_categories:
            seen_categories.add(entry['category_id'])
            anchor = ' id="category-' + esc(entry['category_id']) + '"'
            navigation.append('<a href="#category-' + esc(entry['category_id']) + '">' + esc(entry['category_label']) + '</a>')
        duration = entry.get('duration_seconds')
        duration_text = '{:02d}:{:05.2f}'.format(int(duration // 60), duration % 60) if isinstance(duration, (int, float)) and duration >= 0 else '时长未知'
        error = '<details><summary>读取问题</summary><p>' + esc(entry['preview_error']) + '</p></details>' if entry.get('preview_error') else ''
        rows.append('<article' + anchor + '>' + (image or '<div class="placeholder">暂不可预览</div>') + '<div><span>'
                    + esc(entry['category_label']) + ' · ' + esc(labels[entry['review_status']])
                    + '</span><h2>' + esc('{:06d} · {}'.format(entry['number'], entry['title'])) + '</h2><p>'
                    + esc(entry['summary'] or '尚无画面内容复核记录') + '</p><p>' + esc(' / '.join(entry['tags']))
                    + '</p><p class="timing">' + esc(duration_text) + ' · ' + str(len(entry['source_paths'])) + ' 个来源文件</p>'
                    + '<a href="../' + quote(entry['destination_relative'], safe='/') + '">打开视频 ↗</a><small>'
                    + esc('；'.join(entry['relative_paths'])) + '</small>' + error + '</div></article>')
    tally = counts(plan['entries'])
    metrics = ''.join('<div><strong>' + str(tally[key]) + '</strong><span>' + label + '</span></div>'
                      for key, label in [('source_files', '来源文件'), ('unique_videos', '独立视频'), ('reviewed', '已归类'),
                                         ('needs_review', '待复核'), ('unreadable', '读取异常'), ('duplicate_files', '重复副本')])
    skip_labels = {'unsupported-non-video': '非视频文件，保留在原目录', 'incomplete-file': '下载未完成',
                   'empty-file': '空文件', 'symlink-or-reparse-point': '链接或重解析点，未跟随'}
    skipped = ''.join('<li><b>' + esc(row.get('relative_path', row.get('path', ''))) + '</b><p>'
                      + esc(skip_labels.get(row.get('reason'), row.get('reason', '未处理'))) + '</p></li>' for row in plan['skipped_files'])
    skipped = ('<details class="skipped"><summary>未复制项目 · ' + str(len(plan['skipped_files']))
               + ' 个</summary><p>以下项目仍保留在原始素材目录。</p><ul>' + skipped + '</ul></details>') if skipped else ''
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>shadow · 素材目录</title><style>html{scroll-behavior:smooth}body{margin:0;background:#101214;color:#eee;font:16px system-ui,sans-serif}'
            'main{max-width:1100px;margin:auto;padding:44px 22px}h1{font-size:48px;letter-spacing:-2px;margin-bottom:14px}p{line-height:1.7;color:#bfc7c4}'
            'article{display:grid;grid-template-columns:minmax(200px,40%) 1fr;gap:24px;border-top:1px solid #39413e;padding:28px 0}'
            'img{width:100%;height:auto;border-radius:8px}h2{font-size:22px}a,span{color:#a998fa}a{text-underline-offset:4px}'
            'small{display:block;margin-top:16px;color:#aaa;overflow-wrap:anywhere}.eyebrow{font-size:12px;letter-spacing:4px;color:#a998fa}'
            '.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:32px 0}.metrics div{padding:20px 16px;background:#1b1a25;border-radius:10px}'
            '.metrics strong{display:block;font-size:30px}.metrics span{font-size:13px;color:#bfbacb}nav{display:flex;gap:12px;flex-wrap:wrap;margin:26px 0}'
            'nav a{padding:8px 14px;border:1px solid #3e3855;border-radius:25px;font-size:14px;text-decoration:none}'
            '.placeholder{display:grid;place-items:center;min-height:160px;background:#1e1d25;color:#9c96ad;border-radius:8px}'
            '.timing{font-size:13px;color:#918b9c}.skipped{padding:24px;border:1px solid #43354a;border-radius:10px;margin:24px 0}'
            'summary{cursor:pointer;color:#cfc3eb}details p{overflow-wrap:anywhere}li p{margin-top:3px}'
            '@media(max-width:650px){article{grid-template-columns:1fr}h1{font-size:34px}.metrics{grid-template-columns:repeat(3,1fr)}}'
            '</style><main><div class="eyebrow">SHADOW / MATERIAL LIBRARY</div><h1>让素材，井然有序。</h1>'
            '<p>按实际抽帧画面整理，完整保留原片。抽样复核范围限于画面，未进行逐帧或音频审阅。</p>'
            '<section class="metrics">' + metrics + '</section><p><a href="mapping.csv">原始文件映射 CSV</a> · '
            '<a href="mapping.json">映射 JSON</a> · <a href="verify.json">完整性报告</a></p><nav>' + ''.join(navigation) + '</nav>'
            + skipped + ''.join(rows) + '</main></html>')
    return {'mapping.csv': stream.getvalue().encode('utf-8-sig'),
            'mapping.json': json.dumps(mapping, ensure_ascii=False, indent=2).encode('utf-8') + b'\n',
            'catalog.html': page.encode('utf-8')}


def save_reports(meta, state, reports):
    state.setdefault('report_hashes', {})
    for name, data in reports.items():
        path = ensure_no_case_collision(meta / name)
        digest = hashlib.sha256(data).hexdigest()
        if path.exists():
            if state['report_hashes'].get(name) != assets.digest(path):
                raise ValueError('Refusing to overwrite changed or unowned report: ' + str(path))
            if assets.digest(path) == digest:
                continue
        # Record ownership before write, so a power loss can be recovered safely.
        state['report_hashes'][name] = digest
        atomic_owned(meta / 'manifest.json', state)
        temp = meta / ('report-' + uuid.uuid4().hex + '.tmp')
        try:
            write_new(temp, data)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def apply_plan(path):
    plan = plan_read(path)  # Preflight every original, every evidence image, and all bound documents.
    root, meta = destination_state(plan)
    with destination_lock(meta):
        manifest = safe_path(meta / 'manifest.json')
        state = json_read(manifest) if manifest.exists() else {
            'schema_version': 1, 'kind': KIND, 'plan_id': plan['plan_id'], 'destination_root': str(root),
            'status': 'in_progress', 'completed': {}, 'pending': {}, 'entries': plan['entries'],
            'skipped_files': plan['skipped_files'], 'note': NOTE, 'report_hashes': {}}
        if state.get('plan_id') != plan['plan_id'] or state.get('kind') != KIND:
            raise ValueError('Manifest does not belong to this plan')
        specifications = list(output_specs(plan))
        # Reject all destination conflicts before copying the first byte of any video.
        for relative, source, sha in specifications:
            target = ensure_no_case_collision(root / relative)
            for parent in target.parents:
                if parent == root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError('Destination parent is a file: ' + str(parent))
            if target.exists():
                owned = state['completed'].get(relative) == sha or state.get('pending', {}).get(relative) == sha
                if not owned or not target.is_file() or assets.digest(target) != sha:
                    raise ValueError('Refusing to overwrite an unowned or changed destination: ' + str(target))
        reports = report_bytes(plan)
        for name in [*reports, 'verify.json']:
            report = ensure_no_case_collision(meta / name)
            if report.exists() and state['report_hashes'].get(name) != assets.digest(report):
                raise ValueError('Refusing to overwrite an unowned or changed report: ' + str(report))
        atomic_owned(manifest, state)
        copied, reused = 0, 0
        try:
            for relative, source, sha in specifications:
                target = root / relative
                if target.exists():
                    target = ensure_no_case_collision(target)
                    owned = state['completed'].get(relative) == sha or state.get('pending', {}).get(relative) == sha
                    if not owned or not target.is_file() or assets.digest(target) != sha:
                        raise ValueError('Destination appeared or changed after preflight; refusing reuse: ' + str(target))
                    reused += 1
                else:
                    state['pending'][relative] = sha
                    atomic_owned(manifest, state)
                    copy_exclusive(source, target, meta, sha)
                    copied += 1
                state['completed'][relative] = sha
                state['pending'].pop(relative, None)
                atomic_owned(manifest, state)
            state['status'] = 'complete'
            state.pop('error', None)
            state['counts'] = counts(plan['entries'])
            atomic_owned(manifest, state)
            save_reports(meta, state, reports)
            verification = verify_destination(root, write=False)
            save_reports(meta, state, {'verify.json': json.dumps(verification, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'})
            if verification['status'] != 'verified':
                raise ValueError('Copied media failed final verification; inspect verify.json')
            atomic_owned(manifest, state)
        except Exception as exc:
            state['status'] = 'incomplete'
            state['error'] = str(exc)
            atomic_owned(manifest, state)
            raise
    return {'status': 'complete', 'destination': str(root), 'counts': state['counts'],
            'copied_files_including_previews': copied, 'reused_files_including_previews': reused,
            'catalog': str(meta / 'catalog.html'), 'verification': verification, 'note': NOTE}


def verify_destination(destination, write=False):
    root = safe_path(destination)
    meta = safe_path(root / META)
    if write:
        # Keep the saved report current when the standalone CLI is used.
        # apply calls the read-only branch while it already holds this lock.
        require_file(meta / 'owner.json')
        with destination_lock(meta):
            result = verify_destination(root, write=False)
            state = json_read(require_file(meta / 'manifest.json'))
            save_reports(meta, state, {'verify.json': json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'})
            atomic_owned(meta / 'manifest.json', state)
        return result
    owner = json_read(require_file(meta / 'owner.json'))
    state = json_read(require_file(meta / 'manifest.json'))
    if (owner.get('kind') != KIND or owner.get('destination_root') != str(root)
            or state.get('plan_id') != owner.get('plan_id')):
        raise ValueError('Unrecognized organizer destination')
    checks = []
    for entry in state['entries']:
        relative = entry['destination_relative']
        if len(relative.split('/')) != 2 or '\\' in relative:
            raise ValueError('Unsafe path in manifest')
        for part in relative.split('/'):
            safe_component(part)
        path = safe_path(root / relative)
        checks.append({'asset_id': entry['asset_id'], 'destination_relative': relative,
                       'exists': path.is_file(), 'sha256_matches': path.is_file() and assets.digest(path) == entry['sha256']})
    valid = all(row['sha256_matches'] for row in checks) and state.get('status') == 'complete'
    return {'schema_version': 1, 'status': 'verified' if valid else 'failed', 'plan_id': state['plan_id'],
            'counts': counts(state['entries']), 'checks': checks,
            'source_scope': 'Destination copies verified. Originals were checked before apply; originals are never modified.',
            'content_scope': NOTE}


def main(argv=None):
    parser = assets.JsonArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--source', required=True)
    prep.add_argument('--workspace', required=True)
    prep.add_argument('--samples', type=int, default=8)
    prep.add_argument('--ffmpeg')
    prep.add_argument('--ffprobe')
    sample = sub.add_parser('sample')
    sample.add_argument('--workspace', required=True)
    sample.add_argument('--asset-id', required=True)
    sample.add_argument('--times', nargs='+', type=float, required=True)
    sample.add_argument('--ffmpeg')
    sample.add_argument('--ffprobe')
    template = sub.add_parser('review-template')
    template.add_argument('--workspace', required=True)
    template.add_argument('--output', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--workspace', required=True)
    plan.add_argument('--reviews', required=True)
    plan.add_argument('--destination', required=True)
    plan.add_argument('--taxonomy')
    plan.add_argument('--output')
    apply = sub.add_parser('apply')
    apply.add_argument('--plan', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--destination', required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == 'prepare':
            result = media.prepare(args.source, args.workspace, args.ffmpeg, args.ffprobe, args.samples)
        elif args.command == 'sample':
            result = media.sample_asset(args.workspace, args.asset_id, args.times, args.ffmpeg, args.ffprobe)
        elif args.command == 'review-template':
            result = review_template(args.workspace, args.output)
        elif args.command == 'plan':
            result = make_plan(args.workspace, args.reviews, args.destination, args.taxonomy, args.output)
        elif args.command == 'apply':
            result = apply_plan(args.plan)
        else:
            result = verify_destination(args.destination, write=True)
        assets.emit(result)
        return 1 if result.get('status') == 'failed' else 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        assets.emit({'status': 'error', 'error': str(exc)})
        return 2


if __name__ == '__main__':
    sys.exit(main())
