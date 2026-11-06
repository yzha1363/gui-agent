"""Package the current source and record v2 acceptance boundaries and evidence hashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from gui_agent.version import VERSION, MILESTONE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verification',type=Path,default=ROOT/'output/week3-friday/verification.json')
    parser.add_argument('--desktop-deferred-by-user',action='store_true')
    parser.add_argument('--include-evidence',action='store_true',help='Bundle local validation evidence separately from source')
    args=parser.parse_args()
    report=json.loads(args.verification.read_text(encoding='utf-8'))
    if not report['offline_all_passed']: raise ValueError('Offline verification must pass before packaging.')
    process=subprocess.run([sys.executable,str(ROOT/'scripts/package_project.py'),'--label','gui-agent-v'+VERSION],
                           cwd=ROOT,capture_output=True,text=True,encoding='utf-8',env=dict(os.environ,PYTHONIOENCODING='utf-8'),check=True)
    archive=Path(next(line.removeprefix('Archive: ') for line in process.stdout.splitlines() if line.startswith('Archive: ')))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None: raise ValueError('Source archive integrity failure.')
        inventory=json.loads(bundle.read('manifest.json'))
    directory=ROOT/'output/releases'/('gui-agent-v'+VERSION)/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True,exist_ok=False)
    evidence=None
    if args.include_evidence:
        benchmark=ROOT/report['benchmark']['summary']
        scopes=[ROOT/report['directory'],benchmark.parent,(ROOT/report['project_check']).parent,
                ROOT/'output/week3-friday/regression-before']
        if report['desktop_cases']: scopes.append(ROOT/'output/week3-friday/desktop-validation')
        files={p.resolve() for scope in scopes for p in scope.rglob('*') if p.is_file() and p.suffix!='.tmp'}
        if any(not p.is_relative_to(ROOT) for p in files): raise ValueError('Evidence escaped the workspace.')
        entries={p.relative_to(ROOT).as_posix():p.read_bytes() for p in sorted(files)}
        evidence_manifest={'file_count':len(entries),'files':[{'path':name,'sha256':hashlib.sha256(data).hexdigest()} for name,data in entries.items()]}
        entries['bundle-manifest.json']=json.dumps(evidence_manifest,ensure_ascii=False,indent=2).encode('utf-8')
        target=ROOT/'output/deliverables'/('gui-agent-v'+VERSION+'-validation-evidence-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'.zip')
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name,data in entries.items(): z.writestr(name,data)
        with zipfile.ZipFile(target) as z:
            if z.testzip() is not None or any(hashlib.sha256(z.read(f['path'])).hexdigest()!=f['sha256'] for f in evidence_manifest['files']):
                raise ValueError('Evidence archive integrity mismatch.')
        evidence={'archive':target.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'files':evidence_manifest['file_count']}
    release={'created_at_utc':datetime.now(timezone.utc).isoformat(),'version':VERSION,'milestone':MILESTONE,
             'source_archive':archive.relative_to(ROOT).as_posix(),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
             'source_file_count':inventory['file_count'],'source_archive_has_model_weights':inventory['includes_model_weights'],
             'source_archive_has_desktop_images':inventory['includes_desktop_images'],
             'verification':str(args.verification.resolve().relative_to(ROOT)),'verification_sha256':hashlib.sha256(args.verification.read_bytes()).hexdigest(),
             'full_project_tests':report['full_project_tests'],'new_friday_tests':report['new_unit_tests'],
             'desktop_fix_unit_tests':report.get('desktop_fix_unit_tests',0),
             'robustness_scenarios':len(report['robustness_cases']),'recovery_regression_scenarios':len(report['recovery_regression_cases']),
             'offline_accepted':True,'desktop_validation':report['desktop_validation'],
             'desktop_scenarios':len(report['desktop_cases']),
             'desktop_attempts':len(report.get('desktop_attempts',report['desktop_cases'])),
             'historical_failed_desktop_attempts':len(report.get('desktop_historical_failed_attempts',[])),
             'validation_evidence_bundle':evidence,
             'desktop_deferred_by_user':args.desktop_deferred_by_user,
             'pending':(['Real desktop robustness was deferred by the user for this delivery.'] if args.desktop_deferred_by_user else
                        ['Real desktop coverage is incomplete.'] if report['desktop_missing_scenarios'] else []) +
                       ['NIM real inference remains unverified.', 'Local model action-schema/annotation quality did not pass Wednesday evaluation.',
                        '200% Windows scaling and broader application coverage remain unverified.'],
             'scope':'Development milestone; offline engineering checks are separate from model capability and desktop acceptance.'}
    path=directory/'release.json'; path.write_text(json.dumps(release,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'version':VERSION,'archive':str(archive.relative_to(ROOT)),'release':str(path.relative_to(ROOT)),'evidence':evidence,
                      'sha256':release['archive_sha256'],'files':inventory['file_count']},ensure_ascii=True))


if __name__=='__main__': main()
