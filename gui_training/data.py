"""Freeze small visual-action splits; never fabricate DOM grounding or states."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

from gui_data.pipeline import audit as audit_source, read_jsonl, write_jsonl
from gui_data.schema import DataError, safe_path, validate_record

SPLITS = ('train', 'validation', 'test')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def components(records):
    """Include excluded visual records: they can still bridge tasks/images."""
    parent = {r['group_id']: r['group_id'] for r in records if r['observation']['image']}
    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]; k = parent[k]
        return k
    seen = {}
    for row in records:
        image = row['observation']['image']
        if not image: continue
        group = row['group_id']
        for key in ('pixels:' + image['pixel_sha256'], 'file:' + image['sha256']):
            if key in seen:
                a, b = find(group), find(seen[key])
                parent[max(a, b)] = min(a, b)
            seen[key] = group
    return {key: find(key) for key in parent}


def exclusion(row):
    if row['record_type'] == 'task': return 'no_visual_demonstration'
    if row['record_type'] != 'action': return 'non_action_record'
    if row['source']['name'] == 'mind2web': return 'unverified_dom_grounding'
    if row['source']['name'] != 'screenagent': return 'source_not_reviewed'
    if row['source']['official_split'] != 'train': return 'official_non_training_source'
    if row['quality'].get('coordinate_status') != 'image_pixels_scaled_from_declared_screenshot' or row['quality'].get('warnings'):
        return 'unreviewed_coordinates_or_warnings'
    if any(a['type'] in ('plan', 'reflect', 'select_option', 'hover') or 'target' in a for a in row['label']['actions']):
        return 'unsupported_or_unresolved_action'
    return None


def select_records(records, seed=42):
    link = components(records)
    selected, excluded = [], []
    for row in records:
        validate_record(row)
        reason = exclusion(row)
        if reason:
            excluded.append({'id': row['id'], 'source': row['source']['name'], 'reason': reason})
        else:
            selected.append((row, link[row['group_id']]))
    groups = {group for _, group in selected}
    if len(groups) < 3:
        raise DataError('insufficient_groups', 'At least three independent visual-action groups are required.')
    order = lambda group: hashlib.sha256(f'week3-fixed:{seed}:{group}'.encode()).hexdigest()
    heldout = {link[r['group_id']] for r in records if r['observation']['image'] and r.get('split') != 'train'} & groups
    ordered = sorted(groups, key=order)
    test_group = min(heldout, key=order) if heldout else ordered[0]
    validation_groups = heldout - {test_group}
    if not validation_groups:
        validation_groups = {next(g for g in ordered if g != test_group)}
    if not groups - validation_groups - {test_group}:
        raise DataError('insufficient_groups', 'Preserving legacy heldout groups leaves no training group.')
    rows = []
    for original, component in sorted(selected, key=lambda x: x[0]['id']):
        split = 'test' if component == test_group else 'validation' if component in validation_groups else 'train'
        image = original['observation']['image']
        rows.append({'schema_version': '1.0', 'id': original['id'], 'group_id': original['group_id'],
                     'component_id': component, 'split': split, 'instruction': original['instruction'],
                     'image': {k: deepcopy(image[k]) for k in ['path', 'width', 'height', 'sha256', 'pixel_sha256']},
                     'target': {'actions': deepcopy(original['label']['actions'])}, 'source': deepcopy(original['source']),
                     'source_split': original['split'], 'coordinate_space': 'source_image_pixels',
                     'execution_eligible': False, 'independent_postcondition_available': False})
    return rows, excluded


def validate_sample(row):
    if row.get('coordinate_space') != 'source_image_pixels' or row.get('independent_postcondition_available') is not False:
        raise DataError('invalid_training_record', 'No live coordinates or invented postconditions are permitted.')
    if set(row.get('target', {})) != {'actions'}:
        raise DataError('invalid_training_record', 'Only the annotated action array is a training target.')
    validate_record({'schema_version': '1.0', 'record_type': 'action', 'id': row['id'], 'group_id': row['group_id'],
        'instruction': row['instruction'], 'source': row['source'], 'split': row['split'], 'execution_eligible': row['execution_eligible'],
        'observation': {'image': {'width': row['image']['width'], 'height': row['image']['height'],
            'coordinate_space': 'image_pixels', 'desktop_origin': None}}, 'label': row['target']})
    if any(a['type'] not in {'click', 'move', 'drag', 'scroll', 'type_text', 'press', 'hotkey', 'wait'} or 'target' in a
           for a in row['target']['actions']):
        raise DataError('invalid_training_record', 'Unresolved or unreviewed action in the training set.')


def build_dataset(source, output, seed=42):
    source, output = Path(source).resolve(), Path(output).resolve()
    source_audit = audit_source(source)
    originals = read_jsonl(source / 'records.jsonl')
    rows, excluded = select_records(originals, seed)
    output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        validate_sample(row)
        origin = safe_path(source, row['image']['path'])
        destination = safe_path(output, row['image']['path'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists(): shutil.copyfile(origin, destination)
    write_jsonl(output / 'records.jsonl', rows)
    for split in SPLITS: write_jsonl(output / (split + '.jsonl'), [r for r in rows if r['split'] == split])
    write_jsonl(output / 'excluded.jsonl', excluded)
    sheet = Image.new('RGB', (900, ((len(rows) + 2) // 3) * 245), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        with Image.open(safe_path(output, row['image']['path'])) as im:
            im.thumbnail((285, 210)); x, y = i % 3 * 300, i // 3 * 245
            sheet.paste(im, (x, y + 25)); draw.text((x + 4, y + 4), f'{row["split"]} / {i + 1}', fill='black')
    sheet.save(output / 'preview.png')
    report = {'schema_version': '1.0', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'source': str(source), 'source_manifest_sha256': sha256(source / 'manifest.json'), 'source_audit': source_audit,
              'seed': seed, 'source_record_count': len(originals), 'selected_records': len(rows),
              'counts': dict(Counter(r['split'] for r in rows)), 'excluded_records': len(excluded),
              'exclusion_reasons': dict(Counter(r['reason'] for r in excluded)),
              'split_policy': 'All visual records connect tasks/images; preserve legacy heldout groups; one component is fixed test.',
              'input_policy': 'Only instruction and image; drop all source prompts, history, labels and metadata from user input.',
              'target_policy': 'Source annotated action list in original image pixels; not an executable live workflow.',
              'limitations': ['Twelve candidate action records are a pipeline development set, not a representative benchmark.',
                              'Source annotations are not independently verified live GUI postconditions.',
                              'Small images/Chinese instructions may limit this English VLM; quality is not certified.'],
              'files': [{'path': p.relative_to(output).as_posix(), 'sha256': sha256(p)}
                        for p in sorted(output.rglob('*')) if p.is_file()]}
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report['audit'] = audit_dataset(output)
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def audit_dataset(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        if sha256(safe_path(directory, entry['path'])) != entry['sha256']:
            raise DataError('training_hash_mismatch', entry['path'])
    rows = read_jsonl(directory / 'records.jsonl')
    ids, groups, pixels, counts = set(), {}, {}, Counter()
    for row in rows:
        validate_sample(row)
        if row['id'] in ids: raise DataError('duplicate_id', row['id'])
        ids.add(row['id']); split = row['split']; counts[split] += 1
        if split not in SPLITS or row['execution_eligible'] is not False:
            raise DataError('invalid_training_record', row['id'])
        if row.get('source_split') != 'train' and split == 'train':
            raise DataError('legacy_holdout_leakage', row['id'])
        for key in (row['group_id'], row['component_id']):
            if key in groups and groups[key] != split: raise DataError('task_leakage', key)
            groups[key] = split
        image = row['image']
        for key in ('pixels:' + image['pixel_sha256'], 'file:' + image['sha256']):
            if key in pixels and pixels[key] != split: raise DataError('image_leakage', key)
            pixels[key] = split
        path = safe_path(directory, image['path'])
        if sha256(path) != image['sha256']: raise DataError('training_hash_mismatch', image['path'])
        with Image.open(path) as actual:
            if list(actual.size) != [image['width'], image['height']] or actual.mode != 'RGB':
                raise DataError('image_size_mismatch', image['path'])
    for split in SPLITS:
        part = read_jsonl(directory / (split + '.jsonl'))
        if part != [r for r in rows if r['split'] == split]: raise DataError('split_content_mismatch', split)
        if not part: raise DataError('empty_split', split)
    if dict(counts) != manifest['counts']: raise DataError('count_mismatch', 'Training manifest counts differ.')
    return {'passed': True, 'records_checked': len(rows), 'task_leakage': False, 'image_leakage': False,
            'legacy_holdout_in_training': False, 'split_files_match_records': True, 'hashes_verified': True}
