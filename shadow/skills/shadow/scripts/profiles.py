#!/usr/bin/env python3
"""Select domain workflow candidates or create an editable project brief. No network."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]


def catalog():
    return json.loads((SKILL_ROOT / 'assets/domain-profiles.json').read_text(encoding='utf-8-sig'))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def negated_at(query, position):
    # A negative short clause may contain modifiers before the type name:
    # "不要只做60秒活动精彩回顾" and "不做课程招生广告".
    # Punctuation and explicit contrast start a new scope. This remains a
    # conservative keyword heuristic, not a complete natural-language parser.
    prefix = re.split(r'[，,。；;！!？?\n]|而是|但是|但要|改为|需要的是|instead',
                      query[:position])[-1]
    return bool(re.search(r'(?:不要|不做|不是|无需|不需要|排除|并非|不用|别做|\bnot\b|\bwithout\b)', prefix))


def tag_matches(query, tag):
    tag = tag.casefold()
    if re.fullmatch(r'[a-z0-9 /_-]+', tag):
        pattern = r'(?<![a-z0-9])' + re.escape(tag) + r'(?![a-z0-9])'
    else:
        pattern = re.escape(tag)
    # Explicit "not this type" phrases are not positive intent.
    found = []
    for match in re.finditer(pattern, query):
        if negated_at(query, match.start()):
            continue
        found.append(match.group())
    return bool(found)


def select(query, limit=4):
    query_normalized = re.sub(r'\$shadow\b', '', query.casefold())
    candidates = []
    for profile in catalog()['profiles']:
        positive = [t for t in profile['query_tags'] if tag_matches(query_normalized, t)]
        explicit_id = any(not negated_at(query_normalized, match.start()) for match in re.finditer(
            r'(?<![a-z0-9-])' + re.escape(profile['id']) + r'(?![a-z0-9-])', query_normalized))
        if not positive and not explicit_id:
            continue
        conflicts = [t for t in profile.get('negative_tags', []) if tag_matches(query_normalized, t)]
        # Long phrases carry more information; number of generic synonyms must not dominate.
        weights = sorted((min(8, max(2, len(t.replace(' ', '')))) for t in positive), reverse=True)
        score = (100 if explicit_id else 0) + (weights[0] if weights else 0) + sum(weights[1:]) * .35
        score -= min(6, len(conflicts) * 2)
        candidates.append({'id': profile['id'], 'name': profile['name'], 'family': profile['family'],
                           'score': round(score, 2), 'matched_tags': positive, 'conflict_signals': conflicts,
                           'base_recipe': profile['base_recipe'], 'reference': profile['reference'],
                           'priority': profile.get('priority', 0), 'explicit_id': explicit_id})
    candidates.sort(key=lambda c: (-c['score'], -c['priority'], c['id']))
    uncertain = not candidates or (len(candidates) > 1 and candidates[0]['score'] - candidates[1]['score'] < 2)
    uncertain = uncertain or bool(candidates and candidates[0]['conflict_signals'] and not candidates[0]['explicit_id'])
    return {'query': query, 'selection_method': 'weighted_keyword_candidates', 'ambiguous': uncertain,
            'requires_material_review': True, 'candidates': candidates[:limit],
            'fallback_recipe': 'general-story' if not candidates else None,
            'next_step': 'Choose the primary purpose, then read only that profile and its base recipe. Conflicts are hints, not permission to override the user.'}


def get_profile(profile_id):
    return next((p for p in catalog()['profiles'] if p['id'] == profile_id), None)


def create_brief(profile, args):
    duration = args.duration
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('Duration must be finite and positive')
    if args.fps is not None and (not math.isfinite(args.fps) or args.fps <= 0):
        raise ValueError('FPS must be finite and positive')
    if args.aspect and not re.fullmatch(r'\d+(?:\.\d+)?:\d+(?:\.\d+)?', args.aspect):
        raise ValueError('Aspect must be a ratio such as 16:9, 9:16 or 2.35:1')
    if args.aspect and any(float(n) <= 0 for n in args.aspect.split(':')):
        raise ValueError('Aspect components must be positive')
    path = Path(args.output).expanduser().resolve()
    brief = {'schema_version': 1, 'tool': 'shadow', 'created_utc': datetime.now(timezone.utc).isoformat(),
             'title': args.title, 'primary_profile': profile['id'], 'profile_name': profile['name'],
             'base_recipe': profile['base_recipe'], 'workflow_reference': profile['reference'],
             'audience': args.audience, 'purpose': None,
             'output_specs': {'duration_seconds': duration, 'aspect_ratio': args.aspect, 'fps': args.fps},
             'required_inputs': {key: None for key in profile['required_inputs']},
             'deliverables': profile['deliverables'], 'quality_gates': profile['quality_gates'],
             'material_identity': {'subject': None, 'approved_sources': [], 'mandatory_moments': [], 'excluded_material': []},
             'editorial_decisions': {'audience_goal': None, 'story_beats': [], 'selected_shots': [],
                                    'material_gaps': [], 'reference_studies': [], 'rhythm_sections': [], 'transition_motives': [],
                                    'audio_plan': [], 'color_plan': [], 'subtitle_review': [], 'review_passes': []},
             'status': 'brief_requires_actual_inputs_and_editorial_decisions'}
    # Do not overwrite an existing brief, including an existing timeline passed accidentally.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(brief, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'ok': True, 'path': str(path), 'profile': profile['id'], 'status': brief['status']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('id')
    choose = sub.add_parser('select')
    choose.add_argument('query')
    choose.add_argument('--limit', type=int, default=4)
    brief = sub.add_parser('brief')
    brief.add_argument('id')
    brief.add_argument('--output', required=True)
    brief.add_argument('--title', required=True)
    brief.add_argument('--audience')
    brief.add_argument('--duration', type=float)
    brief.add_argument('--aspect')
    brief.add_argument('--fps', type=float)
    args = parser.parse_args()
    if args.action == 'list':
        emit({'profiles': [{k: p[k] for k in ['id', 'name', 'family', 'base_recipe', 'reference']} for p in catalog()['profiles']]})
    elif args.action == 'select':
        if not 1 <= args.limit <= 20:
            parser.error('--limit must be between 1 and 20')
        emit(select(args.query, args.limit))
    else:
        profile = get_profile(args.id)
        if profile is None:
            raise ValueError('Unknown profile ID: ' + args.id)
        emit(profile if args.action == 'show' else create_brief(profile, args))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        emit({'ok': False, 'error': str(exc)})
        raise SystemExit(2)
