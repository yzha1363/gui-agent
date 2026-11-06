"""Download a pinned public model; verify upstream LFS/Git object hashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FILES = {'README.md', 'config.json', 'generation_config.json', 'preprocessor_config.json',
         'processor_config.json', 'tokenizer.json', 'tokenizer_config.json', 'added_tokens.json',
         'special_tokens_map.json', 'chat_template.json', 'merges.txt', 'vocab.json', 'model.safetensors'}


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def upstream_matches(path, item):
    if not path.is_file(): return False
    data = path.read_bytes()
    if item.get('lfs'):
        return hashlib.sha256(data).hexdigest() == item['lfs']['sha256']
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() == item['blobId']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/week3-monday.json')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    model, revision = config['model_id'], config['model_revision']
    if model != 'HuggingFaceTB/SmolVLM-256M-Instruct' or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Use the reviewed model ID and an immutable commit.')
    out = (ROOT / config['model_dir']).resolve()
    if not out.is_relative_to((ROOT / 'models').resolve()): raise ValueError('Model cache must remain in models/.')
    out.mkdir(parents=True, exist_ok=True)
    metadata = fetch_json(f'https://huggingface.co/api/models/{model}/revision/{revision}?blobs=true')
    if metadata['sha'] != revision: raise ValueError('Hub returned an unexpected revision.')
    entries = {item['rfilename']: item for item in metadata['siblings'] if item['rfilename'] in FILES}
    if set(entries) != FILES: raise ValueError('Pinned model files do not match the reviewed inventory.')
    records = []
    for name, item in sorted(entries.items()):
        target = out / name
        if not upstream_matches(target, item):
            url = f'https://huggingface.co/{model}/resolve/{revision}/{name}'
            pending = target.with_suffix(target.suffix + '.part')
            print('Downloading ' + name, flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, pending.open('wb') as stream:
                while chunk := response.read(1024 * 1024): stream.write(chunk)
            if not upstream_matches(pending, item): raise ValueError('Upstream object hash mismatch: ' + name)
            pending.replace(target)
        records.append({'path': name, 'bytes': target.stat().st_size,
                        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'upstream_lfs_sha256': (item.get('lfs') or {}).get('sha256'), 'upstream_git_blob': item['blobId']})
    report = {'model_id': model, 'revision': revision, 'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
              'license': metadata.get('cardData', {}).get('license'), 'files': records,
              'trust_remote_code': False, 'upstream_hashes_verified': True}
    (out / 'download-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'model_dir': str(out), 'files': len(records), 'bytes': sum(x['bytes'] for x in records), 'hashes_verified': True}))


if __name__ == '__main__': main()
