"""Build a small source delivery archive and its SHA-256 file manifest."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = ['gui_perception', 'gui_control', 'gui_workflow', 'gui_data', 'gui_agent', 'gui_tasks', 'gui_training', 'configs', 'plans', 'tests', 'scripts', 'docs']


def source_files():
    files = {path for pattern in ['*.py', '*.md', 'requirements*.txt', '.gitignore'] for path in ROOT.glob(pattern)}
    for name in SOURCE_DIRECTORIES:
        files.update(path for path in (ROOT / name).rglob('*')
                     if path.suffix in {'.py', '.cjs', '.json', '.md', '.txt'} and '__pycache__' not in path.parts)
    return sorted(path for path in files if path.is_file() and path.resolve().is_relative_to(ROOT))


def portable(value):
    """Replace workspace absolute paths with archive-relative paths in JSON evidence."""
    if isinstance(value, dict):
        return {key: portable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [portable(item) for item in value]
    if isinstance(value, str):
        prefix = str(ROOT) + '\\'
        if value.startswith(prefix):
            return value[len(prefix):].replace('\\', '/')
        if value.startswith(str(ROOT) + '/'):
            return value[len(str(ROOT)) + 1:]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-evidence', action='store_true', help='Include Friday JSON summaries and action logs, without screenshots')
    args = parser.parse_args()
    entries = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in source_files()}
    if args.include_evidence:
        aggregate_path = ROOT / 'output/friday-test-summary.json'
        aggregate = json.loads(aggregate_path.read_text(encoding='utf-8'))
        evidence = [aggregate_path] + [(ROOT / case['summary']).resolve() for case in aggregate['desktop_cases']]
        evidence += [path.parent / 'actions.jsonl' for path in evidence[1:] if (path.parent / 'actions.jsonl').exists()]
        for path in evidence:
            path = path.resolve()
            if not path.is_relative_to((ROOT / 'output').resolve()) or path.suffix not in {'.json', '.jsonl'}:
                raise ValueError('Evidence must be JSON/JSONL inside output/.')
            content = path.read_text(encoding='utf-8')
            if path.suffix == '.jsonl':
                data = ''.join(json.dumps(portable(json.loads(line)), ensure_ascii=False) + '\n'
                               for line in content.splitlines())
            else:
                document = portable(json.loads(content))
                if path == aggregate_path:
                    for case in document['desktop_cases']:
                        case['summary'] = case['summary'].replace('\\', '/')
                data = json.dumps(document, ensure_ascii=False, indent=2)
            entries[path.relative_to(ROOT).as_posix()] = data.encode('utf-8')
    manifest = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'includes_desktop_images': False, 'includes_model_weights': False,
                'evidence_kind': 'historical_summaries_and_action_logs' if args.include_evidence else 'none',
                'file_count': len(entries),
                'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(entries.items())]}
    entries['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    directory = ROOT / 'output/deliverables'
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / ('gui-agent-week1-' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(entries.items()):
            bundle.writestr(name, data)
    # Verify the actual archive, not just the list of source paths.
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Archive integrity check failed.')
        for item in manifest['files']:
            if hashlib.sha256(bundle.read(item['path'])).hexdigest() != item['sha256']:
                raise ValueError(f'Archive hash mismatch: {item["path"]}')
    print(f'Archive: {archive}')
    print(f'Files: {manifest["file_count"]} + manifest.json; verified SHA-256 hashes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
