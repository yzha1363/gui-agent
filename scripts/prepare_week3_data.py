"""Build or audit the fixed visual-action development dataset."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui_training.data import audit_dataset, build_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/week2/processed/20261026T145603_897824Z')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--validate', type=Path)
    args = parser.parse_args()
    if args.validate:
        report = audit_dataset(args.validate)
    else:
        directory = args.output or ROOT / 'data/week3' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        report = build_dataset(args.source, directory)
        report = {key: report[key] for key in ('counts', 'selected_records', 'excluded_records', 'exclusion_reasons', 'audit')}
        report['directory'] = str(directory)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
