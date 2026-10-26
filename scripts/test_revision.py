"""Record every offline regression case with module counts and real outcomes."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class RecordedResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def record(self, test, status, detail=None):
        parts = test.id().split('.')
        self.records.append({'id': test.id(), 'module': '.'.join(parts[:-2]), 'class': parts[-2],
                             'case': parts[-1], 'status': status,
                             'duration_ms': round((time.perf_counter() - self.started) * 1000, 3),
                             'detail': detail})

    def addSuccess(self, test):
        self.record(test, 'passed')
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.record(test, 'failed', self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record(test, 'error', self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.record(test, 'skipped', reason)
        super().addSkip(test, reason)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []


def main():
    directory = ROOT / 'output/revision/regression' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    with (directory / 'unittest.log').open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(suite)
    groups = {}
    for row in result.records:
        key = row['class']
        entry = groups.setdefault(key, {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0})
        entry['total'] += 1
        entry['passed' if row['status'] == 'passed' else 'skipped' if row['status'] == 'skipped' else 'failed'] += 1
    summary = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'type': 'offline-unit-and-regression',
               'success': result.wasSuccessful(), 'tests': result.testsRun, 'groups': groups, 'cases': result.records,
               'desktop_input': False, 'screenshots': 'Not applicable: mock backend; real GUI screenshots are separate system evidence.'}
    (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'success': summary['success'], 'tests': result.testsRun, 'summary': str(directory / 'summary.json')}, ensure_ascii=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
