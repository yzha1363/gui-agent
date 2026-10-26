"""Download bounded, traceable samples from the three official sources."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import urllib.parse
import urllib.request

SCREEN = 'niuzaisheng/ScreenAgent'
WEB = 'web-arena-x/webarena'
MIND = 'osunlp/Multimodal-Mind2Web'
HOSTS = {'api.github.com', 'raw.githubusercontent.com', 'huggingface.co',
         'datasets-server.huggingface.co'}


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


class Fetcher:
    def __init__(self, root, max_bytes=80 * 1024 * 1024):
        self.root, self.max_bytes = Path(root), max_bytes
        self.files, self.total, self.lock = [], 0, threading.Lock()

    def fetch(self, url, relative, limit=16 * 1024 * 1024):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS:
            raise ValueError('Download URL is outside the official source allowlist.')
        dest = (self.root / relative).resolve()
        if not dest.is_relative_to(self.root.resolve()):
            raise ValueError('Download path escapes the bundle.')
        request = urllib.request.Request(url, headers={'User-Agent': 'gui-agent-data-preparation/1.0'})
        with urllib.request.urlopen(request, timeout=60) as response:
            final = urllib.parse.urlsplit(response.url)
            if final.hostname not in HOSTS:
                raise ValueError('Unexpected download redirect.')
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'File exceeds download size limit: {relative}')
        with self.lock:
            if self.total + len(data) > self.max_bytes:
                raise ValueError('Sample bundle exceeds total download budget.')
            self.total += len(data)
            # Asset signatures expire; preserve the immutable asset path instead.
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query = [(k, v) for k, v in query if k not in {'Expires', 'Signature', 'Key-Pair-Id'}]
            public_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
            self.files.append({'path': relative, 'url': public_url, 'bytes': len(data),
                               'sha256': hashlib.sha256(data).hexdigest()})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return data

    def json(self, url, relative):
        return json.loads(self.fetch(url, relative))


def acquire(root, screen_sessions=6, screen_steps=4, mind_offsets=(0, 64, 128, 256, 512),
            mind_length=4, web_tasks=32):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    fetch = Fetcher(root)
    sources, records = {}, []
    for name, repo in [('screenagent', SCREEN), ('webarena', WEB)]:
        commit = fetch.json(f'https://api.github.com/repos/{repo}/commits/main', f'metadata/{name}-commit.json')
        revision = commit['sha']
        sources[name] = {'repository': f'https://github.com/{repo}', 'revision': revision,
                         'official_split': 'train' if name == 'screenagent' else 'benchmark',
                         'license': 'Apache-2.0' if name == 'screenagent' else 'Apache-2.0 (repository)',
                         'usage': 'local_research' if name == 'screenagent' else 'evaluation_only'}
        fetch.fetch(f'https://raw.githubusercontent.com/{repo}/{revision}/LICENSE', f'licenses/{name}.txt')
    rev = sources['screenagent']['revision']
    tree = fetch.json(f'https://api.github.com/repos/{SCREEN}/git/trees/{rev}?recursive=1', 'metadata/screenagent-tree.json')
    if tree.get('truncated'):
        raise ValueError('ScreenAgent repository tree was truncated.')
    sessions = defaultdict(list)
    available = {entry['path'] for entry in tree['tree'] if entry['type'] == 'blob'}
    for name in sorted(available):
        if name.startswith('data/ScreenAgent/train/') and name.endswith('_translate.json'):
            sessions[name.split('/')[3]].append(name)
    selected = [name for session in sorted(sessions)[:screen_sessions]
                for name in sessions[session][:screen_steps]]
    def screen_sample(name):
        local = 'screenagent/' + '/'.join(name.split('/')[3:])
        raw = fetch.json(f'https://raw.githubusercontent.com/{SCREEN}/{rev}/{name}', local)
        image_name = raw['saved_image_name']
        if Path(image_name).name != image_name or '/' in image_name or '\\' in image_name:
            raise ValueError('Invalid source image name.')
        remote = str(Path(name).parent / 'images' / image_name).replace('\\', '/')
        if remote not in available:
            raise ValueError(f'Missing published screenshot: {remote}')
        image = str(Path(local).parent / 'images' / image_name).replace('\\', '/')
        fetch.fetch(f'https://raw.githubusercontent.com/{SCREEN}/{rev}/{remote}', image)
        return {'source': 'screenagent', 'raw_path': local, 'image_path': image,
                'origin': f'https://github.com/{SCREEN}/blob/{rev}/{name}', 'row_index': None}
    with ThreadPoolExecutor(max_workers=6) as pool:
        records.extend(pool.map(screen_sample, selected))
    metadata = fetch.json(f'https://huggingface.co/api/datasets/{MIND}', 'metadata/mind2web-repository.json')
    mind_rev = metadata['sha']
    sources['mind2web'] = {'repository': f'https://huggingface.co/datasets/{MIND}', 'revision': mind_rev,
                          'official_split': 'train', 'license': metadata.get('cardData', {}).get('license', 'unspecified'),
                          'usage': 'local_research', 'revision_verification': 'Every screenshot asset URL must contain repository revision; row response is also hashed.'}
    fetch.fetch(f'https://huggingface.co/datasets/{MIND}/raw/{mind_rev}/README.md', 'licenses/mind2web-dataset-card.md')
    for offset in mind_offsets:
        query = urllib.parse.urlencode({'dataset': MIND, 'config': 'default', 'split': 'train',
                                        'offset': offset, 'length': mind_length})
        page = fetch.json('https://datasets-server.huggingface.co/rows?' + query, f'mind2web/pages/{offset}.json')
        for entry in page['rows']:
            index, row = entry['row_idx'], entry['row']
            asset = row['screenshot']['src']
            if f'/--/{mind_rev}/--/' not in urllib.parse.urlsplit(asset).path:
                raise ValueError('Dataset viewer screenshot revision differs from repository revision.')
            local = f'mind2web/rows/{index}.json'
            (root / local).parent.mkdir(parents=True, exist_ok=True)
            dump(root / local, row)
            image = f'mind2web/images/{index}.jpg'
            fetch.fetch(asset, image)
            records.append({'source': 'mind2web', 'raw_path': local, 'image_path': image,
                            'origin': f'https://huggingface.co/datasets/{MIND}/tree/{mind_rev}', 'row_index': index})
    rev = sources['webarena']['revision']
    tasks = fetch.json(f'https://raw.githubusercontent.com/{WEB}/{rev}/config_files/test.raw.json', 'webarena/test.raw.json')
    # Spread a small sample over the complete benchmark, rather than taking one template family.
    indices = sorted({round(i * (len(tasks) - 1) / max(1, web_tasks - 1)) for i in range(web_tasks)})
    for index in indices:
        local = f'webarena/tasks/{tasks[index]["task_id"]}.json'
        (root / local).parent.mkdir(parents=True, exist_ok=True)
        dump(root / local, tasks[index])
        records.append({'source': 'webarena', 'raw_path': local, 'image_path': None,
                        'origin': f'https://github.com/{WEB}/blob/{rev}/config_files/test.raw.json', 'row_index': index})
    # Include checksums of derived per-row files as well as downloaded containers.
    for entry in records:
        entry['raw_sha256'] = hashlib.sha256((root / entry['raw_path']).read_bytes()).hexdigest()
    manifest = {'schema_version': '1.0', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Small development sample; not a full dataset or a benchmark reproduction.',
                'selection': {'screen_sessions': screen_sessions, 'screen_steps_per_session': screen_steps,
                              'mind_offsets': list(mind_offsets), 'mind_length': mind_length,
                              'webarena_tasks': web_tasks},
                'sources': sources, 'records': records, 'downloads': sorted(fetch.files, key=lambda x: x['path']),
                'downloaded_bytes': fetch.total}
    dump(root / 'manifest.json', manifest)
    return manifest
