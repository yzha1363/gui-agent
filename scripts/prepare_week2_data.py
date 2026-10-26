"""Fetch official small samples, normalize them offline, or audit an existing run."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui_data.acquire import acquire, dump
from gui_data.pipeline import audit, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--fetch', action='store_true', help='Download bounded public samples and prepare them')
    choice.add_argument('--raw', type=Path, help='Prepare an existing raw bundle without network or desktop input')
    choice.add_argument('--validate', type=Path, help='Audit an existing prepared dataset offline')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-side', type=int, default=1600)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.validate:
        print(json.dumps(audit(args.validate), ensure_ascii=True))
        return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    raw = args.raw or ROOT / 'data/week2/raw' / stamp
    if args.fetch:
        acquire(raw)
    out = args.output or ROOT / 'data/week2/processed' / stamp
    report = build(raw, out, max_side=args.max_side, seed=args.seed)
    evidence = ROOT / 'output/week2-monday' / stamp
    evidence.mkdir(parents=True, exist_ok=False)
    # This report contains counts and source metadata; raw public data stays local.
    dump(evidence / 'summary.json', {'processed': str(out.resolve()), 'raw_bundle': str(raw.resolve()),
                                    'counts': report['counts'], 'audit': report['audit'],
                                    'sources': report['sources'], 'split_policy': report['split_policy'],
                                    'by_source_split': report['by_source_split'], 'limitations': report['limitations']})
    print(json.dumps({'output': str(out), 'summary': str(evidence / 'summary.json'),
                      'counts': report['counts'], 'audit': report['audit']}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
