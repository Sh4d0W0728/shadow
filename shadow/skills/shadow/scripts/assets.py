#!/usr/bin/env python3
"""Shadow's local, durable asset library. stdlib only; FFmpeg/ffprobe optional."""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

VIDEO = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mts', '.m2ts', '.mpg', '.mpeg', '.mxf'}
AUDIO = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.aif', '.aiff', '.opus'}
IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif'}
MEDIA = VIDEO | AUDIO | IMAGE
RIGHTS = {'.txt', '.pdf', '.doc', '.docx'}
INCOMPLETE = {'.crdownload', '.part', '.partial', '.tmp', '.download'}
PROJECT_DIRS = ['staging/downloads', 'staging/extracted', 'media/video', 'media/audio', 'media/image',
                'media/other', 'rights', 'previews/contact-sheets', 'manifests', 'notes', 'exports', 'projects']
PROVENANCE_FIELDS = ['origin', 'page_url', 'item_id', 'title', 'query', 'license_note',
                     'downloaded_at', 'entitlement_note']
REVIEWED = {'viewed', 'listened', 'user-verified'}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def category_of(path):
    ext = path.suffix.lower()
    return 'video' if ext in VIDEO else 'audio' if ext in AUDIO else 'image' if ext in IMAGE else 'other'


def safe_name(name):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(name).stem).strip(' .')[:100] or 'asset'
    return stem + Path(name).suffix.lower()


def find_tool(name, supplied=None):
    if supplied:
        p = Path(supplied).expanduser()
        if not p.is_file():
            raise ValueError('Tool does not exist: ' + str(p))
        return str(p.resolve())
    env = os.getenv('SHADOW_' + name.upper()) or os.getenv('CUTCRAFT_' + name.upper())
    if env and Path(env).is_file():
        return str(Path(env).resolve())
    found = shutil.which(name)
    if found:
        return found
    p = Path(r'C:\Program Files\Topaz Labs LLC\Topaz Video AI') / (name + '.exe')
    return str(p) if p.is_file() else None


def probe(path, supplied=None):
    tool = find_tool('ffprobe', supplied)
    if not tool:
        return {'status': 'unavailable', 'reason': 'ffprobe not found; file extension is not content verification'}
    try:
        result = subprocess.run([tool, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if result.returncode:
            return {'status': 'failed', 'reason': result.stderr[-1500:]}
        data = json.loads(result.stdout)
        fmt = data.get('format', {})
        streams = []
        for s in data.get('streams', []):
            row = {k: s.get(k) for k in ['index', 'codec_type', 'codec_name', 'width', 'height',
                                         'avg_frame_rate', 'r_frame_rate', 'sample_rate', 'channels',
                                         'channel_layout', 'duration'] if k in s}
            if s.get('avg_frame_rate') not in (None, '0/0'):
                try:
                    row['fps'] = float(Fraction(s['avg_frame_rate']))
                except (ValueError, ZeroDivisionError):
                    pass
            if s.get('tags', {}).get('rotate'):
                row['rotate_tag'] = s['tags']['rotate']
            if s.get('side_data_list'):
                row['side_data'] = s['side_data_list']
            streams.append(row)
        return {'status': 'ok', 'probed_at': now(), 'format': fmt.get('format_name'),
                'duration_seconds': float(fmt['duration']) if fmt.get('duration') else None,
                'streams': streams, 'note': 'Technical metadata only; no semantic content review implied.'}
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return {'status': 'failed', 'reason': str(exc)}


def init_project(path):
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in PROJECT_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    marker = root / '.shadow-project.json'
    if not marker.exists():
        marker.write_text(json.dumps({'schema_version': 1, 'project_id': uuid.uuid4().hex,
                                     'project_name': root.name, 'created_at': now()}, ensure_ascii=False, indent=2), encoding='utf-8')
    with connect(root) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, stored_path TEXT, original_name TEXT,
          mode TEXT, category TEXT, media_role TEXT NOT NULL DEFAULT 'source', status TEXT NOT NULL DEFAULT 'candidate',
          byte_size INTEGER, metadata_json TEXT NOT NULL DEFAULT '{}',
          semantic_summary TEXT, review_evidence TEXT NOT NULL DEFAULT 'metadata-only',
          review_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS provenance (
          id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
          origin TEXT, page_url TEXT, item_id TEXT, title TEXT, query TEXT,
          license_note TEXT, downloaded_at TEXT, entitlement_note TEXT,
          local_source_path TEXT, archive_member TEXT, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tags (asset_id TEXT NOT NULL REFERENCES assets(id), tag TEXT NOT NULL,
          UNIQUE(asset_id, tag));
        CREATE TABLE IF NOT EXISTS annotations (id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
          summary TEXT NOT NULL, evidence TEXT NOT NULL, note TEXT, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS uses (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
          source_in REAL NOT NULL, source_out REAL NOT NULL, timeline_in REAL,
          track TEXT NOT NULL, sort_order INTEGER NOT NULL, purpose TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS provenance_asset ON provenance(asset_id);
        CREATE TABLE IF NOT EXISTS rights_attachments (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
          stored_path TEXT NOT NULL, original_name TEXT NOT NULL, archive_path TEXT, archive_member TEXT,
          provenance_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
        ''')
    return {'project': str(root), 'database': str(root / 'library.sqlite3'), 'directories': PROJECT_DIRS}


@contextmanager
def connect(root):
    db = sqlite3.connect(str(root / 'library.sqlite3'))
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def project(path):
    root = Path(path).expanduser().resolve()
    if not (root / '.shadow-project.json').is_file() or not (root / 'library.sqlite3').is_file():
        raise ValueError('Not an initialized Shadow project. Run init first: ' + str(root))
    return root


def asset(db, asset_id):
    row = db.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown asset ID: ' + asset_id)
    return row


def add_tags(db, asset_id, tags):
    for tag in tags or []:
        if tag.strip():
            db.execute('INSERT OR IGNORE INTO tags VALUES (?,?)', (asset_id, tag.strip()))


def add_provenance(db, asset_id, values, source=None, member=None):
    columns = ['asset_id'] + PROVENANCE_FIELDS + ['local_source_path', 'archive_member', 'recorded_at']
    data = [asset_id] + [values.get(k) for k in PROVENANCE_FIELDS] + [str(source) if source else None, member, now()]
    db.execute('INSERT INTO provenance (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', data)


def candidate(root, values, tags=None):
    aid = uuid.uuid4().hex
    with connect(root) as db:
        db.execute('INSERT INTO assets(id,category,status,created_at,updated_at) VALUES (?,?,?,?,?)',
                   (aid, 'other', 'candidate', now(), now()))
        add_provenance(db, aid, values)
        add_tags(db, aid, tags)
    return {'asset_id': aid, 'status': 'candidate', 'file_available': False, 'note': 'Page/title records are not downloaded media or content verification.'}


def import_file(root, source, mode='copy', category=None, tags=None, values=None, candidate_id=None,
                ffprobe=None, archive_member=None, role=None):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ValueError('Source file missing: ' + str(source))
    if source.suffix.lower() in INCOMPLETE or any(source.name.lower().endswith(e) for e in INCOMPLETE):
        raise ValueError('Incomplete download cannot be imported: ' + str(source))
    if source.suffix.lower() not in MEDIA:
        raise ValueError('Only supported media files are imported; executable/project/archive files are not media: ' + str(source))
    sha = digest(source)
    values = dict(values or {})
    if not values.get('origin'): values['origin'] = 'user'
    # Download completion time is supplied by the browser/caller; import time is recorded_at.
    # An unknown download time stays unknown rather than silently borrowing the import timestamp.
    with connect(root) as db:
        previous = db.execute('SELECT * FROM assets WHERE sha256=?', (sha,)).fetchone()
        pending = asset(db, candidate_id) if candidate_id else None
        if pending is not None and pending['stored_path']:
            raise ValueError('candidate-id already has a file; use returned canonical asset ID')
        if previous is not None:
            aid = previous['id']
            if not previous['stored_path'] or not Path(previous['stored_path']).is_file():
                raise ValueError('Same content already registered but stored/reference path is missing. Repair canonical file first: ' + aid)
            if pending is not None and pending['id'] != aid:
                db.execute('UPDATE provenance SET asset_id=? WHERE asset_id=?', (aid, pending['id']))
                db.execute('UPDATE annotations SET asset_id=? WHERE asset_id=?', (aid, pending['id']))
                if pending['semantic_summary'] and not previous['semantic_summary']:
                    db.execute('UPDATE assets SET semantic_summary=?,review_evidence=?,review_note=? WHERE id=?',
                               (pending['semantic_summary'],pending['review_evidence'],pending['review_note'],aid))
                for row in db.execute('SELECT tag FROM tags WHERE asset_id=?', (pending['id'],)).fetchall():
                    add_tags(db, aid, [row['tag']])
                db.execute('DELETE FROM tags WHERE asset_id=?', (pending['id'],))
                db.execute('DELETE FROM assets WHERE id=?', (pending['id'],))
            add_provenance(db, aid, values, source, archive_member)
            add_tags(db, aid, tags)
            return {'asset_id': aid, 'deduplicated': True, 'sha256': sha, 'stored_path': previous['stored_path'],
                    'status': previous['status'], 'merged_candidate_id': candidate_id}
        aid = pending['id'] if pending is not None else uuid.uuid4().hex
        cat = category or category_of(source)
        media_role = role or ('preview' if re.search(r'预览|样机|preview|watermark', source.stem, re.I) else 'source')
        if cat not in {'video', 'audio', 'image', 'other'}:
            raise ValueError('Invalid category: ' + cat)
        destination = source
        if mode == 'copy':
            destination = root / 'media' / cat / (sha[:12] + '-' + safe_name(source.name))
            if destination.exists() and (not destination.is_file() or digest(destination) != sha):
                destination = root / 'media' / cat / (sha + '-' + safe_name(source.name))
                if destination.exists() and (not destination.is_file() or digest(destination) != sha):
                    raise ValueError('Destination collision; no existing project file overwritten')
            temporary = destination.with_name(destination.name + '.importing')
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, temporary)
                if digest(temporary) != sha:
                    raise ValueError('Source changed during import; retry after download is complete')
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        metadata = probe(destination, ffprobe)
        row_values = (sha, str(destination), source.name, mode, cat, media_role, 'downloaded', destination.stat().st_size,
                      json.dumps(metadata, ensure_ascii=False), now(), aid)
        if pending is None:
            db.execute('INSERT INTO assets(id,created_at,updated_at) VALUES (?,?,?)', (aid, now(), now()))
        db.execute('UPDATE assets SET sha256=?,stored_path=?,original_name=?,mode=?,category=?,media_role=?,status=?,byte_size=?,metadata_json=?,updated_at=? WHERE id=?', row_values)
        add_provenance(db, aid, values, source, archive_member)
        add_tags(db, aid, tags)
    return {'asset_id': aid, 'deduplicated': False, 'sha256': sha, 'stored_path': str(destination),
            'status': 'downloaded', 'media_role': media_role, 'metadata_status': metadata['status'], 'semantic_review': 'metadata-only'}


def record(root, row):
    result = dict(row)
    result['technical_metadata'] = json.loads(result.pop('metadata_json'))
    result['file_available'] = bool(result['stored_path'] and Path(result['stored_path']).is_file())
    with connect(root) as db:
        result['tags'] = [r['tag'] for r in db.execute('SELECT tag FROM tags WHERE asset_id=? ORDER BY tag', (row['id'],))]
        result['provenance'] = [dict(r) for r in db.execute('SELECT * FROM provenance WHERE asset_id=? ORDER BY id', (row['id'],))]
    return result


def list_assets(root, query=None, tags=None, category=None, status=None):
    # Parameterized filtering; '*' and '%' in queries are literal text, not SQL patterns.
    clauses, params = [], []
    if query:
        clauses.append('a.id IN (SELECT id FROM assets WHERE instr(lower(coalesce(original_name,\'\') || \' \' || coalesce(semantic_summary,\'\')), lower(?))>0 UNION SELECT asset_id FROM tags WHERE instr(lower(tag),lower(?))>0 UNION SELECT asset_id FROM provenance WHERE instr(lower(coalesce(title,\'\') || \' \' || coalesce(query,\'\') || \' \' || coalesce(item_id,\'\')),lower(?))>0)')
        params += [query, query, query]
    if category:
        clauses.append('a.category=?'); params.append(category)
    if status:
        clauses.append('a.status=?'); params.append(status)
    for tag in tags or []:
        clauses.append('EXISTS(SELECT 1 FROM tags t WHERE t.asset_id=a.id AND t.tag=?)'); params.append(tag)
    sql = 'SELECT a.* FROM assets a' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY a.created_at,a.id'
    with connect(root) as db:
        rows = db.execute(sql, params).fetchall()
    return [record(root, r) for r in rows]


def annotate(root, asset_id, summary, evidence, note=None, tags=None, verify=False):
    with connect(root) as db:
        row = asset(db, asset_id)
        if verify and evidence not in REVIEWED:
            raise ValueError('verified requires viewed, listened, or explicit user-verified evidence; metadata/title is insufficient')
        if verify and not (row['stored_path'] and Path(row['stored_path']).is_file()):
            raise ValueError('Cannot verify a candidate or missing file')
        db.execute('INSERT INTO annotations(asset_id,summary,evidence,note,recorded_at) VALUES (?,?,?,?,?)',
                   (asset_id, summary, evidence, note, now()))
        db.execute('UPDATE assets SET semantic_summary=?,review_evidence=?,review_note=?,updated_at=? WHERE id=?',
                   (summary, evidence, note, now(), asset_id))
        if verify and row['status'] != 'selected':
            db.execute('UPDATE assets SET status=\'verified\' WHERE id=?', (asset_id,))
        add_tags(db, asset_id, tags)
    return {'asset_id': asset_id, 'evidence': evidence, 'verified': verify, 'note': 'This records supplied review evidence; the CLI does not recognize video content.'}


def set_status(root, asset_id, target, note=None):
    with connect(root) as db:
        row = asset(db, asset_id)
        if target in {'downloaded', 'verified', 'selected'} and not (row['stored_path'] and Path(row['stored_path']).is_file()):
            raise ValueError('Requested status requires an existing imported media file')
        if target == 'verified' and row['review_evidence'] not in REVIEWED:
            raise ValueError('Record viewed/listened/user-verified annotation before setting verified')
        if target == 'candidate' and row['stored_path']:
            raise ValueError('Imported files keep downloaded/verified/selected status; candidate denotes a page-only record')
        db.execute('UPDATE assets SET status=?,review_note=coalesce(?,review_note),updated_at=? WHERE id=?', (target, note, now(), asset_id))
    return {'asset_id': asset_id, 'status': target, 'review_evidence': row['review_evidence']}


def add_use(root, asset_id, source_in, source_out, purpose, order=0, track='V1', timeline_in=None):
    for value in (source_in, source_out, timeline_in):
        if value is not None and not math.isfinite(value):
            raise ValueError('Time values must be finite')
    if source_in < 0 or source_out <= source_in or (timeline_in is not None and timeline_in < 0):
        raise ValueError('Require 0 <= source-in < source-out and nonnegative timeline-in')
    with connect(root) as db:
        row = asset(db, asset_id)
        if not row['stored_path'] or not Path(row['stored_path']).is_file():
            raise ValueError('Selected use requires an existing imported media file')
        duration = json.loads(row['metadata_json']).get('duration_seconds')
        if row['category'] != 'image' and duration is not None and source_out > duration + 0.05:
            raise ValueError('Selected source-out exceeds probed duration')
        uid = uuid.uuid4().hex
        db.execute('INSERT INTO uses VALUES (?,?,?,?,?,?,?,?,?)', (uid, asset_id, source_in, source_out, timeline_in, track, order, purpose, now()))
        db.execute('UPDATE assets SET status=\'selected\',updated_at=? WHERE id=?', (now(), asset_id))
    return {'use_id': uid, 'asset_id': asset_id, 'status': 'selected', 'content_review': row['review_evidence'],
            'note': 'Selection is a decision; it does not imply the media has been viewed or listened to.'}


def check(root, rehash=False):
    rows = list_assets(root)
    issues = []
    for row in rows:
        if not row['stored_path']:
            continue
        p = Path(row['stored_path'])
        if not p.is_file():
            issues.append({'asset_id': row['id'], 'kind': 'missing_file', 'path': str(p)})
        elif rehash and digest(p) != row['sha256']:
            issues.append({'asset_id': row['id'], 'kind': 'changed_file', 'path': str(p)})
    return {'asset_count': len(rows), 'ok': not issues, 'issues': issues,
            'note': 'Existence/hash checks do not verify semantic content, licensing, or Premiere import.'}


def inside(root, target):
    target = Path(target).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise ValueError('Output must stay inside initialized project: ' + str(target))
    return target


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def csv_cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_cell(row.get(k)) for k in columns})


def export_manifest(root):
    records = list_assets(root)
    jpath, cpath = root / 'manifests/assets.json', root / 'manifests/assets.csv'
    write_json(jpath, {'schema_version': 1, 'generated_at': now(), 'project': str(root), 'assets': records})
    flattened = []
    for row in records:
        r = {k: row.get(k) for k in ['id', 'original_name', 'stored_path', 'sha256', 'category', 'media_role', 'status', 'file_available', 'semantic_summary', 'review_evidence']}
        r['tags'] = ' | '.join(row['tags'])
        for k in PROVENANCE_FIELDS:
            r[k] = ' | '.join(dict.fromkeys(str(p[k]) for p in row['provenance'] if p.get(k)))
        r['metadata_status'] = row['technical_metadata'].get('status')
        r['duration_seconds'] = row['technical_metadata'].get('duration_seconds')
        flattened.append(r)
    columns = ['id','original_name','stored_path','sha256','category','media_role','status','file_available','metadata_status','duration_seconds','semantic_summary','review_evidence','tags'] + PROVENANCE_FIELDS
    write_csv(cpath, flattened, columns)
    with connect(root) as db:
        rights = [dict(r) for r in db.execute('SELECT * FROM rights_attachments ORDER BY recorded_at')]
    write_json(root / 'manifests/rights-attachments.json', {'attachments': rights, 'note': 'Preserved attachments only; documents were not opened or executed.'})
    return {'json': str(jpath), 'csv': str(cpath), 'asset_count': len(records), 'rights_attachments': len(rights)}


def shotlist(root):
    with connect(root) as db:
        rows = db.execute('SELECT u.*,a.stored_path,a.original_name,a.semantic_summary,a.review_evidence,a.category FROM uses u JOIN assets a ON a.id=u.asset_id ORDER BY u.sort_order,u.created_at,u.id').fetchall()
    shots, cursors = [], {}
    for row in rows:
        shot = dict(row)
        shot['media_path'] = shot.pop('stored_path')
        shot['in'] = shot.pop('source_in'); shot['out'] = shot.pop('source_out')
        if shot['timeline_in'] is None:
            shot['timeline_in'] = cursors.get(shot['track'], 0)
        shot['duration'] = shot['out'] - shot['in']
        cursors[shot['track']] = max(cursors.get(shot['track'], 0), shot['timeline_in'] + shot['duration'])
        shot['file_available'] = Path(shot['media_path']).is_file()
        shots.append(shot)
    jpath, cpath = root / 'manifests/shotlist.json', root / 'manifests/shotlist.csv'
    write_json(jpath, {'schema_version': 1, 'shots': shots, 'note': 'Selected uses with source seconds; editorial decisions, not executed timeline effects.'})
    write_csv(cpath, shots, ['id','asset_id','media_path','original_name','in','out','duration','timeline_in','track','sort_order','purpose','semantic_summary','review_evidence','file_available'])
    return {'json': str(jpath), 'csv': str(cpath), 'shot_count': len(shots)}


def summary(root):
    rows = list_assets(root)
    marker = json.loads((root / '.shadow-project.json').read_text(encoding='utf-8'))
    lines = ['# ' + marker.get('project_name', root.name) + ' — 素材摘要', '',
             '生成于 ' + now() + '；标题/查询/文件名和技术元数据不代表已经观看素材。', '',
             '- 素材记录：' + str(len(rows)), '- 可用文件：' + str(sum(r['file_available'] for r in rows)),
             '- 有观看/试听/用户核验记录：' + str(sum(r['review_evidence'] in REVIEWED for r in rows)), '',
             '| ID | 类别 / 状态 | 内容摘要与依据 | 来源 | 待确认 |', '|---|---|---|---|---|']
    def cell(value):
        return str(value or '').replace('|', '\\|').replace('\n', ' ')
    for row in rows:
        titles = '; '.join(p.get('title') or p.get('page_url') or p.get('origin') or '未记录' for p in row['provenance'])
        unknowns = []
        if not row['stored_path']: unknowns.append('尚未下载')
        elif not row['file_available']: unknowns.append('文件缺失')
        if row['review_evidence'] not in REVIEWED: unknowns.append('内容尚未观看/试听核验')
        if row['technical_metadata'].get('status') != 'ok' and row['stored_path']: unknowns.append('技术探测未知/失败')
        if any(p.get('origin') == 'baotu' and not p.get('license_note') for p in row['provenance']): unknowns.append('页面授权说明未记录')
        desc = row['semantic_summary'] or '未知；仅来源标题：' + titles
        lines.append('| ' + ' | '.join(cell(v) for v in [row['id'][:12], row['category'] + ' / ' + row['media_role'] + ' / ' + row['status'], desc + ' [' + row['review_evidence'] + ']', titles, '; '.join(unknowns) or '已记录字段无此项缺口']) + ' |')
    lines += ['', '## 选镜用途', '']
    with connect(root) as db:
        uses = db.execute('SELECT * FROM uses ORDER BY sort_order,created_at').fetchall()
    if not uses:
        lines.append('尚无选镜记录；不能把已下载状态称为已经选入成片。')
    for u in uses:
        lines.append('- ' + u['asset_id'][:12] + ' ' + str(u['source_in']) + '–' + str(u['source_out']) + ' 秒 / ' + u['track'] + '：' + u['purpose'])
    lines += ['', '这些未知项是记录缺口，不自动要求另一次版权确认。页面可观察到的会员/授权信息如实保留；遇到登录、下载额度、额外购买或访问阻碍时由浏览器流程处理。']
    output = root / 'notes/asset-summary.md'
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'summary': str(output), 'asset_count': len(rows)}


def safe_member(name):
    name = name.replace('\\', '/')
    raw_parts = name.rstrip('/').split('/')
    if name.startswith('/') or not raw_parts or any(p in {'', '.', '..'} or re.search(r'[<>:"|?*\x00-\x1f]', p)
          or p.endswith((' ', '.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in raw_parts):
        raise ValueError('Unsafe archive member path: ' + name)
    return raw_parts


def is_rights(name):
    return Path(name).suffix.lower() in RIGHTS and bool(re.search(r'授权|版权|协议|license|licence|copyright|rights', name, re.I))


def archive_limits(max_files,max_total_mb,max_member_mb,max_ratio):
    if not isinstance(max_files,int) or max_files < 1:
        raise ValueError('max-files must be a positive integer')
    if any(not math.isfinite(v) or v <= 0 for v in [max_total_mb,max_member_mb,max_ratio]):
        raise ValueError('Archive guards must be finite positive values')


def commit_extracted(temp, target):
    if target.exists():
        if not target.is_file() or digest(target) != digest(temp):
            raise ValueError('Existing extraction file differs; no project file overwritten: ' + str(target))
        temp.unlink()
    else:
        temp.replace(target)


def retain_rights(root, source, archive, member, values):
    sha = digest(source)
    target = inside(root, root / 'rights' / (sha[:12] + '-' + safe_name(source.name)))
    if target.exists() and (not target.is_file() or digest(target) != sha):
        raise ValueError('Rights attachment destination collision; no file overwritten')
    if source.resolve() != target:
        shutil.copy2(source, target)
    aid = uuid.uuid4().hex
    with connect(root) as db:
        db.execute('INSERT INTO rights_attachments VALUES (?,?,?,?,?,?,?,?)',
                   (aid, sha, str(target), source.name, str(archive), member, json.dumps(values or {}, ensure_ascii=False), now()))
    return {'attachment_id': aid, 'stored_path': str(target), 'sha256': sha, 'note': 'Preserved only; not opened, executed, or treated as an independently validated license.'}


def extract_zip(root, archive, values=None, tags=None, ffprobe=None, max_files=500,
                max_total_mb=2048, max_member_mb=1024, max_ratio=200):
    archive_limits(max_files,max_total_mb,max_member_mb,max_ratio)
    archive = Path(archive).expanduser().resolve()
    if not archive.is_file(): raise ValueError('ZIP archive missing: ' + str(archive))
    dest = inside(root, root / 'staging/extracted' / (digest(archive)[:12] + '-' + safe_name(archive.stem)))
    selected, skipped, total, seen = [], [], 0, set()
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) > max_files: raise ValueError('Archive member count exceeds guard')
        for info in members:
            name = info.filename.replace('\\', '/')
            parts = safe_member(name)
            key = '/'.join(parts).casefold()
            if key in seen: raise ValueError('Duplicate/case-colliding archive paths are unsupported')
            seen.add(key)
            unix_type = (info.external_attr >> 16) & 0o170000
            if unix_type == 0o120000: raise ValueError('ZIP symbolic links are not accepted')
            if info.flag_bits & 1: raise ValueError('Encrypted stock archive is unsupported')
            if info.is_dir(): continue
            total += info.file_size
            if info.file_size > max_member_mb * 1024 * 1024 or total > max_total_mb * 1024 * 1024:
                raise ValueError('Archive expanded size exceeds guard')
            if info.file_size / max(1, info.compress_size) > max_ratio:
                raise ValueError('Archive compression ratio exceeds guard')
            if Path(name).suffix.lower() not in MEDIA and not is_rights(name):
                skipped.append({'member': info.filename, 'reason': 'non-media; not extracted or executed'})
                continue
            target = inside(root, dest.joinpath(*parts))
            selected.append((info, target))
        # Validate whole directory before writing any member. Extract only whitelisted media.
        extracted = []
        for info, target in selected:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.extracting')
            try:
                with z.open(info) as source, temp.open('wb') as output:
                    copied = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk: break
                        copied += len(chunk)
                        if copied > info.file_size or copied > max_member_mb * 1024 * 1024:
                            raise ValueError('Archive member exceeded declared/allowed size')
                        output.write(chunk)
                commit_extracted(temp,target)
            finally:
                temp.unlink(missing_ok=True)
            extracted.append((info.filename, target))
    imports, rights = [], []
    for member, p in extracted:
        if is_rights(member): rights.append(retain_rights(root,p,archive,member,values))
        else: imports.append(import_file(root,p,tags=tags,values=values,ffprobe=ffprobe,archive_member=str(archive) + ' :: ' + member))
    return {'archive': str(archive), 'extraction_directory': str(dest), 'imported': imports, 'rights_attachments': rights, 'skipped': skipped}


def extract_rar(root, archive, values=None, tags=None, ffprobe=None, unrar=None, max_files=500,
                max_total_mb=2048, max_member_mb=1024, max_ratio=200):
    archive_limits(max_files,max_total_mb,max_member_mb,max_ratio)
    archive = Path(archive).expanduser().resolve()
    if not archive.is_file(): raise ValueError('RAR archive missing: ' + str(archive))
    tool = unrar or shutil.which('unrar') or str(Path(r'C:\Program Files\WinRAR\UnRAR.exe'))
    if not Path(tool).is_file(): raise ValueError('Trusted UnRAR executable required; provide --unrar')
    result = subprocess.run([tool,'lt','-c-','-cfg-','-p-','-scu','-@','--',str(archive)],capture_output=True,timeout=60)
    if result.returncode: raise ValueError('UnRAR cannot list archive (encrypted/damaged/unsupported)')
    # -scu gives Unicode UTF-16 output on supported Windows UnRAR; unsupported localized layouts fail closed.
    listing = result.stdout.decode('utf-16le', errors='strict').replace('\r','')
    blocks = re.split(r'\n\s*(?:名称|Name):\s*', listing)[1:]
    if not blocks: raise ValueError('UnRAR member listing could not be parsed; do not extract blindly')
    if len(blocks) > max_files: raise ValueError('Archive member count exceeds guard')
    entries, skipped, total, seen = [], [], 0, set()
    for block in blocks:
        lines = block.splitlines(); name = lines[0].strip()
        fields = {}
        for line in lines[1:]:
            match = re.match(r'\s*([^:]+):\s*(.*)',line)
            if match: fields[match[1].strip()] = match[2].strip()
        kind = fields.get('类型', fields.get('Type','')).casefold()
        parts = safe_member(name); key = '/'.join(parts).casefold()
        if key in seen: raise ValueError('Duplicate/case-colliding archive paths are unsupported')
        seen.add(key)
        if kind in {'目录','directory'}: continue
        if kind not in {'文件','file'}: raise ValueError('Unsupported RAR member type, including links: ' + kind)
        if any(re.search(r'link|链接|redir|重定向', k + ' ' + v, re.I) for k,v in fields.items()):
            raise ValueError('RAR links/redirections are unsupported')
        size = fields.get('大小',fields.get('Size')); packed = fields.get('打包大小',fields.get('Packed size'))
        if size is None or packed is None or not size.isdigit() or not packed.isdigit():
            raise ValueError('RAR member sizes unavailable; no extraction performed')
        size, packed = int(size), int(packed); total += size
        if size > max_member_mb * 1024 * 1024 or total > max_total_mb * 1024 * 1024: raise ValueError('Archive expanded size exceeds guard')
        if size / max(1,packed) > max_ratio: raise ValueError('Archive compression ratio exceeds guard')
        if Path(name).suffix.lower() not in MEDIA and not is_rights(name):
            skipped.append({'member':name,'reason':'non-media/non-license; not extracted or executed'}); continue
        entries.append({'name':name,'parts':parts,'size':size})
    dest = inside(root,root / 'staging/extracted' / (digest(archive)[:12] + '-' + safe_name(archive.stem)))
    imports, rights = [], []
    for info in entries:
        target = inside(root,dest.joinpath(*info['parts'])); target.parent.mkdir(parents=True,exist_ok=True)
        temp = target.with_name(target.name + '.extracting')
        # 'p' emits bytes, never allows the archive to choose filesystem paths or execute a member.
        proc = subprocess.Popen([tool,'p','-inul','-cfg-','-p-','-ol-','-scu','-@','--',str(archive),info['name']],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        timer = threading.Timer(120,proc.kill); timer.start()
        try:
            copied = 0
            with temp.open('wb') as output:
                while True:
                    chunk = proc.stdout.read(1024 * 1024)
                    if not chunk: break
                    copied += len(chunk)
                    if copied > info['size'] or copied > max_member_mb * 1024 * 1024:
                        proc.kill(); raise ValueError('RAR output exceeded declared/allowed size')
                    output.write(chunk)
            code = proc.wait(timeout=5)
            if code or copied != info['size']: raise ValueError('RAR member extraction/CRC verification failed: ' + info['name'])
            commit_extracted(temp,target)
        finally:
            timer.cancel()
            if proc.poll() is None: proc.kill(); proc.wait()
            proc.stdout.close(); proc.stderr.close(); temp.unlink(missing_ok=True)
        if is_rights(info['name']): rights.append(retain_rights(root,target,archive,info['name'],values))
        else: imports.append(import_file(root,target,tags=tags,values=values,ffprobe=ffprobe,archive_member=str(archive) + ' :: ' + info['name']))
    return {'archive':str(archive),'extraction_directory':str(dest),'imported':imports,'rights_attachments':rights,'skipped':skipped,
            'note':'Original and preview roles are distinct; attachments retained only; no documents/executables opened.'}


def contact_sheet(root, asset_id, ffmpeg=None, count=8, columns=4, start=0, end=None):
    if not 1 <= count <= 30 or not 1 <= columns <= 10: raise ValueError('count 1..30 and columns 1..10 required')
    tool = find_tool('ffmpeg', ffmpeg)
    if not tool: raise ValueError('ffmpeg required for actual contact sheet images')
    with connect(root) as db: row = asset(db, asset_id)
    if not row['stored_path'] or not Path(row['stored_path']).is_file(): raise ValueError('Media file missing')
    if row['category'] not in {'video', 'image'}: raise ValueError('Contact sheet requires video/image')
    duration = json.loads(row['metadata_json']).get('duration_seconds')
    if row['category'] == 'image':
        count, columns, times = 1, 1, [0.0]
    else:
        end = end if end is not None else duration
        if end is None: raise ValueError('Unknown duration: supply --end in seconds')
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start: raise ValueError('Invalid preview time range')
        if duration is not None and end > duration + 0.05: raise ValueError('Preview end exceeds duration')
        times = [start + (end - start) * ((i + 0.5) / count) for i in range(count)]
    outdir = inside(root, root / 'previews/contact-sheets' / (asset_id + '-' + uuid.uuid4().hex[:8]))
    outdir.mkdir(parents=True)
    samples = []
    for i, stamp in enumerate(times):
        frame = outdir / ('frame-%03d.jpg' % i)
        command = [tool, '-hide_banner', '-loglevel', 'error', '-y', '-ss', '%.6f' % stamp, '-i', row['stored_path'],
                   '-frames:v', '1', '-vf', 'scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2', str(frame)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if result.returncode or not frame.is_file(): raise ValueError('Preview extraction failed: ' + result.stderr[-1500:])
        samples.append({'index': i, 'requested_source_seconds': stamp, 'image': str(frame), 'note': 'FFmpeg seeks to timestamp; decoded frame nearest available timestamp may differ.'})
    sheet = outdir / 'contact-sheet.jpg'
    nrows = math.ceil(count / columns)
    command = [tool, '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '1', '-start_number', '0',
               '-i', str(outdir / 'frame-%03d.jpg'), '-vf', 'tile=%dx%d:nb_frames=%d:padding=4:margin=4' % (columns, nrows, count),
               '-frames:v', '1', str(sheet)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if result.returncode or not sheet.is_file(): raise ValueError('Contact sheet composition failed: ' + result.stderr[-1500:])
    manifest = outdir / 'timestamps.json'
    write_json(manifest, {'asset_id': asset_id, 'media_path': row['stored_path'], 'contact_sheet': str(sheet),
                          'samples': samples, 'note': 'Generated visual samples; no content review is asserted until an agent/user actually views them.'})
    return {'asset_id': asset_id, 'contact_sheet': str(sheet), 'timestamp_manifest': str(manifest), 'samples': samples}


def add_provenance_args(p):
    p.add_argument('--origin', choices=['user', 'baotu', 'other'], default='user')
    for field in PROVENANCE_FIELDS[1:]: p.add_argument('--' + field.replace('_', '-'))
    p.add_argument('--tag', action='append', default=[])


def main(argv=None):
    parser = JsonArgumentParser(description='Shadow project asset library: files, provenance, reviews and selected uses.')
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('init'); p.add_argument('project')
    p = sub.add_parser('candidate'); p.add_argument('project'); add_provenance_args(p)
    for cmd in ['import', 'extract-zip', 'extract-rar']:
        p = sub.add_parser(cmd); p.add_argument('project'); p.add_argument('source'); add_provenance_args(p); p.add_argument('--ffprobe')
        if cmd == 'import':
            p.add_argument('--mode', choices=['copy','reference'], default='copy'); p.add_argument('--category', choices=['video','audio','image','other']); p.add_argument('--candidate-id'); p.add_argument('--role',choices=['source','preview','reference'])
        else:
            p.add_argument('--max-files', type=int, default=500); p.add_argument('--max-total-mb', type=float, default=2048)
            p.add_argument('--max-member-mb', type=float, default=1024); p.add_argument('--max-ratio', type=float, default=200)
            if cmd == 'extract-rar': p.add_argument('--unrar')
    p = sub.add_parser('import-staging'); p.add_argument('project'); add_provenance_args(p); p.add_argument('--ffprobe'); p.add_argument('--candidate-id')
    for cmd in ['list','search']:
        p = sub.add_parser(cmd); p.add_argument('project'); p.add_argument('--query'); p.add_argument('--tag', action='append', default=[])
        p.add_argument('--category', choices=['video','audio','image','other']); p.add_argument('--status', choices=['candidate','downloaded','verified','selected'])
    p = sub.add_parser('annotate'); p.add_argument('project'); p.add_argument('asset_id'); p.add_argument('--summary', required=True)
    p.add_argument('--evidence', choices=['metadata-only','user-description','viewed','listened','user-verified'], required=True)
    p.add_argument('--note'); p.add_argument('--tag', action='append', default=[]); p.add_argument('--verify', action='store_true')
    p = sub.add_parser('status'); p.add_argument('project'); p.add_argument('asset_id'); p.add_argument('--to', choices=['candidate','downloaded','verified','selected'], required=True); p.add_argument('--note')
    p = sub.add_parser('use'); p.add_argument('project'); p.add_argument('asset_id'); p.add_argument('--in', dest='source_in', type=float, required=True)
    p.add_argument('--out', dest='source_out', type=float, required=True); p.add_argument('--purpose', required=True); p.add_argument('--order', type=int, default=0)
    p.add_argument('--track', default='V1'); p.add_argument('--timeline-in', type=float)
    p = sub.add_parser('check'); p.add_argument('project'); p.add_argument('--rehash', action='store_true')
    for cmd in ['export','summary','shotlist']:
        p = sub.add_parser(cmd); p.add_argument('project')
    p = sub.add_parser('contact-sheet'); p.add_argument('project'); p.add_argument('asset_id'); p.add_argument('--ffmpeg')
    p.add_argument('--count', type=int, default=8); p.add_argument('--columns', type=int, default=4); p.add_argument('--start', type=float, default=0); p.add_argument('--end', type=float)
    args = parser.parse_args(argv)
    if args.action == 'init': emit(init_project(args.project)); return 0
    root = project(args.project)
    values = {k: getattr(args, k, None) for k in PROVENANCE_FIELDS}
    if args.action == 'candidate': result = candidate(root, values, args.tag)
    elif args.action == 'import': result = import_file(root,args.source,args.mode,args.category,args.tag,values,args.candidate_id,args.ffprobe,role=args.role)
    elif args.action == 'import-staging':
        files = [p for p in sorted((root / 'staging/downloads').iterdir()) if p.is_file() and p.suffix.lower() in MEDIA]
        ignored = [str(p) for p in sorted((root / 'staging/downloads').iterdir()) if p.is_file() and p not in files]
        if args.candidate_id and len(files) != 1: raise ValueError('candidate-id staging import requires exactly one completed media file; import an exact path otherwise')
        result = {'imported': [import_file(root,p,tags=args.tag,values=values,candidate_id=args.candidate_id,ffprobe=args.ffprobe) for p in files],
                  'ignored': ignored, 'note': 'Only completed media suffixes; no guess that multiple downloads belong to one stock item.'}
    elif args.action == 'extract-zip': result = extract_zip(root,args.source,values,args.tag,args.ffprobe,args.max_files,args.max_total_mb,args.max_member_mb,args.max_ratio)
    elif args.action == 'extract-rar': result = extract_rar(root,args.source,values,args.tag,args.ffprobe,args.unrar,args.max_files,args.max_total_mb,args.max_member_mb,args.max_ratio)
    elif args.action in {'list','search'}: result = {'assets': list_assets(root,args.query,args.tag,args.category,args.status)}
    elif args.action == 'annotate': result = annotate(root,args.asset_id,args.summary,args.evidence,args.note,args.tag,args.verify)
    elif args.action == 'status': result = set_status(root,args.asset_id,args.to,args.note)
    elif args.action == 'use': result = add_use(root,args.asset_id,args.source_in,args.source_out,args.purpose,args.order,args.track,args.timeline_in)
    elif args.action == 'check':
        result = check(root,args.rehash); emit(result); return 0 if result['ok'] else 2
    elif args.action == 'export': result = export_manifest(root)
    elif args.action == 'summary': result = summary(root)
    elif args.action == 'shotlist': result = shotlist(root)
    elif args.action == 'contact-sheet': result = contact_sheet(root,args.asset_id,args.ffmpeg,args.count,args.columns,args.start,args.end)
    emit(result)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        emit({'error': str(exc), 'error_type': type(exc).__name__})
        raise SystemExit(2)
