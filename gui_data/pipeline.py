"""Clean samples, deduplicate, split by task and shared images, and audit output."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from gui_data.acquire import dump
from gui_data.adapters import ADAPTERS
from gui_data.schema import DataError, fingerprint, safe_path, validate_record


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n' for row in rows), encoding='utf-8')


def read_jsonl(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def split_records(records, validation_ratio=.2, seed=42):
    """Keep shared task IDs and identical decoded images in the same component.

    Build components before deduplication: removing a duplicate must not hide
    a link between two otherwise distinct task groups.
    """
    parent = {row['group_id']: row['group_id'] for row in records if row['record_type'] != 'task'}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    def union(a, b):
        a, b = find(a), find(b)
        parent[max(a, b)] = min(a, b)
    seen_images = {}
    for row in records:
        image = row['observation']['image']
        if image is not None and row['record_type'] != 'task':
            key = image['pixel_sha256']
            if key in seen_images:
                union(row['group_id'], seen_images[key])
            else:
                seen_images[key] = row['group_id']
    for row in records:
        if row['record_type'] != 'task':
            row['component_id'] = find(row['group_id'])
    kept, duplicates, seen = [], [], {}
    for row in sorted(records, key=lambda x: x['id']):
        key = fingerprint(row)
        if key in seen:
            duplicates.append({'id': row['id'], 'duplicate_of': seen[key], 'reason': 'identical_instruction_image_context_label'})
        else:
            kept.append(row)
            seen[key] = row['id']
    groups = sorted({row['component_id'] for row in kept if row['record_type'] != 'task'},
                    key=lambda key: hashlib.sha256(f'{seed}:{key}'.encode()).hexdigest())
    n_val = min(len(groups) - 1, max(1, round(len(groups) * validation_ratio))) if len(groups) > 1 else 0
    validation = set(groups[:n_val])
    for row in kept:
        row['split'] = 'evaluation' if row['record_type'] == 'task' else 'validation' if row['component_id'] in validation else 'train'
        validate_record(row)
    return kept, duplicates, {'seed': seed, 'validation_ratio_requested': validation_ratio,
                               'independent_components': len(groups), 'validation_components': n_val,
                               'warning': 'Too few groups for an independent validation split.' if len(groups) < 2 else None}


def preview(records, out):
    # An audit contact sheet only; model images remain unchanged.
    selected = []
    for source in ['screenagent', 'mind2web']:
        selected.extend([r for r in records if r['source']['name'] == source and r['observation']['image']][:3])
    if not selected:
        return None
    sheet = Image.new('RGB', (960, 250 * ((len(selected) + 2) // 3)), 'white')
    draw = ImageDraw.Draw(sheet)
    for index, row in enumerate(selected):
        image = Image.open(safe_path(out, row['observation']['image']['path'])).convert('RGB')
        image.thumbnail((310, 210))
        x, y = index % 3 * 320, index // 3 * 250
        sheet.paste(image, (x, y + 30))
        draw.text((x + 4, y + 3), f'{row["source"]["name"]} / {row["record_type"]} / {row["split"]}', fill='black')
    sheet.save(Path(out) / 'preview.png')
    return 'preview.png'


def build(raw_root, out, max_side=1600, validation_ratio=.2, seed=42):
    raw_root, out = Path(raw_root).resolve(), Path(out).resolve()
    if type(max_side) is not int or not 64 <= max_side <= 4096:
        raise ValueError('max_side must be an integer in 64..4096.')
    if not 0 < validation_ratio < 1:
        raise ValueError('validation_ratio must lie strictly between 0 and 1.')
    manifest = json.loads((raw_root / 'manifest.json').read_text(encoding='utf-8'))
    # Acquisition corruption must abort the run rather than masquerade as annotation quality.
    for item in manifest['downloads']:
        if digest(safe_path(raw_root, item['path'])) != item['sha256']:
            raise DataError('raw_hash_mismatch', item['path'])
    for entry in manifest['records']:
        if digest(safe_path(raw_root, entry['raw_path'])) != entry['raw_sha256']:
            raise DataError('raw_hash_mismatch', entry['raw_path'])
    out.mkdir(parents=True, exist_ok=False)
    accepted, rejected = [], []
    for entry in manifest['records']:
        try:
            raw = json.loads(safe_path(raw_root, entry['raw_path']).read_text(encoding='utf-8'))
            if entry['source'] not in ADAPTERS:
                raise DataError('unsupported_source', str(entry['source']))
            record = ADAPTERS[entry['source']](raw, entry, manifest, raw_root, out, max_side)
            accepted.append(record)
        except (DataError, KeyError, TypeError, ValueError, OSError) as exc:
            rejected.append({'source': entry['source'], 'raw_path': entry['raw_path'],
                             'code': getattr(exc, 'code', 'invalid_source_record'), 'message': str(exc)})
    records, duplicates, split = split_records(accepted, validation_ratio, seed)
    if not records:
        raise DataError('empty_dataset', 'No usable records remain; inspect source annotations.')
    write_jsonl(out / 'records.jsonl', records)
    for name, filename in [('train', 'train.jsonl'), ('validation', 'validation.jsonl'), ('evaluation', 'evaluation_tasks.jsonl')]:
        write_jsonl(out / filename, [r for r in records if r['split'] == name])
    write_jsonl(out / 'rejected.jsonl', rejected)
    write_jsonl(out / 'duplicates.jsonl', duplicates)
    preview_path = preview(records, out)
    counts = {'downloaded_records': len(manifest['records']), 'accepted_before_dedup': len(accepted),
              'records': len(records), 'rejected': len(rejected), 'duplicates': len(duplicates),
              'by_source': dict(Counter(r['source']['name'] for r in records)),
              'by_record_type': dict(Counter(r['record_type'] for r in records)),
              'by_split': dict(Counter(r['split'] for r in records)),
              'by_action_type': dict(Counter(a['type'] for r in records for a in r['label']['actions'])),
              'rejection_reasons': dict(Counter(r['code'] for r in rejected)),
              'unique_images': len({r['observation']['image']['pixel_sha256'] for r in records if r['observation']['image']})}
    by_source_split = defaultdict(Counter)
    for record in records:
        by_source_split[record['source']['name']][record['split']] += 1
    report = {'schema_version': '1.0', 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'raw_bundle': str(raw_root), 'raw_manifest_sha256': digest(raw_root / 'manifest.json'),
              'counts': counts, 'by_source_split': {k: dict(v) for k, v in by_source_split.items()},
              'split_policy': split, 'sources': manifest['sources'], 'preview': preview_path,
              'limitations': ['Development sample, not full data or a representative benchmark.',
                              'Source task labels and instructions are untrusted data; no actions are executed.',
                              'Mind2Web DOM/CSS boxes are not converted to screenshot or live desktop click points.',
                              'WebArena task descriptions remain evaluation-only; no screenshots/actions are fabricated.',
                              'No independent post-action state labels were invented.'],
              'files': [{'path': p.relative_to(out).as_posix(), 'sha256': digest(p), 'bytes': p.stat().st_size}
                        for p in sorted(out.rglob('*')) if p.is_file()]}
    dump(out / 'manifest.json', report)
    report['audit'] = audit(out)
    # The manifest isn't in its own hash list, so adding the audit is safe.
    dump(out / 'manifest.json', report)
    return report


def audit(out):
    out = Path(out).resolve()
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        if digest(safe_path(out, item['path'])) != item['sha256']:
            raise DataError('prepared_hash_mismatch', item['path'])
    records = read_jsonl(out / 'records.jsonl')
    ids, groups, pixels = set(), {}, {}
    for row in records:
        validate_record(row)
        if row['id'] in ids:
            raise DataError('duplicate_id', row['id'])
        ids.add(row['id'])
        if row['split'] not in {'train', 'validation', 'evaluation'}:
            raise DataError('invalid_split', row['id'])
        if row['record_type'] == 'task' and row['split'] != 'evaluation':
            raise DataError('benchmark_contamination', row['id'])
        if row['record_type'] != 'task':
            for key in [row['group_id'], row['component_id']]:
                if key in groups and groups[key] != row['split']:
                    raise DataError('split_leakage', key)
                groups[key] = row['split']
        image = row['observation']['image']
        if image:
            image_path = safe_path(out, image['path'])
            if digest(image_path) != image['sha256']:
                raise DataError('prepared_hash_mismatch', image['path'])
            with Image.open(image_path) as actual:
                if actual.size != (image['width'], image['height']) or actual.mode != 'RGB':
                    raise DataError('image_size_mismatch', image['path'])
            key = image['pixel_sha256']
            if key in pixels and pixels[key] != row['split']:
                raise DataError('split_leakage', 'Identical images occur in different splits.')
            pixels[key] = row['split']
    split_ids = []
    for split, filename in [('train', 'train.jsonl'), ('validation', 'validation.jsonl'), ('evaluation', 'evaluation_tasks.jsonl')]:
        part = read_jsonl(out / filename)
        if any(r['split'] != split for r in part):
            raise DataError('invalid_split', filename)
        split_ids.extend(r['id'] for r in part)
        expected = [r for r in records if r['split'] == split]
        if part != expected:
            raise DataError('split_content_mismatch', filename)
    if len(split_ids) != len(ids) or set(split_ids) != ids:
        raise DataError('split_content_mismatch', 'Split files do not partition all records.')
    return {'passed': True, 'records_checked': len(records), 'task_leakage': False,
            'image_leakage': False, 'asset_hashes_verified': True,
            'split_files_match_records': True}
