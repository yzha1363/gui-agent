"""Save v2 robustness/regression evidence, keeping simulation and desktop separate."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from gui_agent.version import VERSION
from gui_tasks.robustness import EXPECTED, SCENARIOS, TEXT, run_robustness
from gui_agent.recovery_simulation import EXPECTED as RECOVERY_EXPECTED, SCENARIOS as RECOVERY_SCENARIOS, run_recovery_simulation
from gui_agent.recovery_http_fixture import HTTP_SCENARIOS, run_http_recovery
from gui_data.schema import safe_path
from robustness_demo import DESKTOP_SCENARIOS


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_robustness(directory, result):
    fixture = read(directory/'fixture.json'); name = fixture['scenario']; status,code,dispatches = EXPECTED[name]
    calls = fixture['actions']; values = fixture['final_state']
    checks = {
        'saved_summary_matches': read(directory/'session/summary.json') == result,
        'expected_status_code_and_dispatches': result['status']==status and result.get('error',{}).get('code')==code and result['input_dispatches_started']==dispatches,
        'no_click_on_stale_empty_location': all(c.get('hit') is not None for c in calls if c['action']=='click'),
        'no_duplicate_text_or_submit': sum(c['action']=='type_text' for c in calls)<=1 and values['submit_count']<=1,
        'within_shared_budgets': result['replans_used']<=result['policy']['max_replans'] and result['observation_attempts_used']<=result['policy']['max_observations'],
        'explicit_simulation': result['environment']=='simulation' and result['desktop_input_sent'] is False,
        'task_verification_not_forged': result['task_verified']==(status=='completed'),
    }
    if status=='completed':
        checks['both_goal_channels'] = result['goal_verification']['ocr']['met'] and result['goal_verification']['state']['met']
        checks['exact_actual_text_and_single_commit'] = values['entry_text']==TEXT and values['submit_count']==1
    if name=='layout_moves': checks['uses_new_target_position'] = calls[0]['point']==[150,190]
    if name in ('duplicate_appears','disappears_before_click'): checks['zero_input_after_reconfirmation_failure'] = not calls
    if name=='duplicate_second': checks['explicit_second_target_used'] = values['focus_target']=='secondary'
    if name=='submit_uncertain': checks['uncertain_submit_remains_failed'] = result['diagnostic']['goal']['met'] and result['input_delivery_uncertain'] and not result['task_verified']
    return {'case':name,'kind':fixture['kind'],'status':result['status'],'error_code':code,'error_category':result.get('error',{}).get('category'),
            'possible_dispatches':result['input_dispatches_started'],'input_calls':len(calls),'replans':result['replans_used'],
            'summary':(directory/'session/summary.json').relative_to(ROOT).as_posix(),'checks':checks,'passed':all(checks.values())}


def audit_desktop(directory):
    directory=Path(directory).resolve()
    result=read(directory/'summary.json'); name=result['scenario']; expected=EXPECTED[name]
    child=read(directory/'session/summary.json') if (directory/'session/summary.json').exists() else None
    actions=[json.loads(line) for line in (directory/'actions.jsonl').read_text(encoding='utf-8').splitlines()] if (directory/'actions.jsonl').exists() else []
    state=read(directory/'final-state.json') if (directory/'final-state.json').exists() else read(directory/'application-state.json')
    screenshots=list(directory.rglob('screenshot.png'))
    checks={'expected_status':result['status']==expected[0], 'expected_code':result.get('error',{}).get('code')==expected[1],
            'real_session_record':child is not None and child['environment']=='desktop',
            'screenshots_exist':bool(screenshots),'window_hwnd_recorded':bool(result.get('display',{}).get('window_hwnd')),
            'single_real_commit':state['values']['submit_count']<=1,'replay_planning_label':result['planning_kind']=='replay',
            'real_model_still_pending':result['real_model_validation']=='pending'}
    if child is not None:
        checks['expected_dispatches']=child['input_dispatches_started']==expected[2]
        checks['no_extra_native_actions']=len(actions)==expected[2]
        checks['same_hwnd']=read(directory/'ready.json')['display']['window_hwnd']==result.get('display',{}).get('window_hwnd')
        checks['declared_budgets_hold']=child['observation_attempts_used']<=child['policy']['max_observations'] and child['replans_used']<=child['policy']['max_replans']
        checks['goal_success_not_forged']=child['task_verified']==(expected[0]=='completed')
    if expected[0]=='completed':
        checks['both_goal_channels']=child['goal_verification']['ocr']['met'] and child['goal_verification']['state']['met']
        checks['actual_widget_text']=state['values']['entry_text']==TEXT and state['values']['submit_count']==1
        from gui_tasks.robustness import contracts
        from gui_workflow.plan import validate_plan
        from gui_workflow.runner import evaluate_observation
        from types import SimpleNamespace
        cycle=read(ROOT/child['cycles'][-1]['summary'])
        last=Path(cycle['observations'][-1])
        _,goal,_=contracts(name)
        condition=validate_plan({'window_title':'GUI Agent V2 Robustness','steps':[{'action':'wait_text','expect':goal}]})['steps'][0]['expect']
        independent=evaluate_observation(SimpleNamespace(report=read(last/'results.json'),state=read(last/'state.json')),condition)
        checks['goal_recomputed_from_raw_ocr_and_state']=independent['met'] and independent==child['goal_verification']
    if name=='duplicate_second': checks['second_widget_selected']=state['values']['focus_target']=='secondary'
    if name in ('duplicate','missing','stale_state'): checks['zero_real_input']=not actions and state['values']['submit_count']==0
    if name=='wrong_text': checks['wrong_text_was_not_submitted']=state['values']['entry_text']==TEXT+'!' and state['values']['submit_count']==0
    if name=='partial_input': checks['only_partial_text_and_no_submit']=state['values']['entry_text']=='Hel' and state['values']['submit_count']==0 and child['input_delivery_uncertain']
    if name=='submit_uncertain': checks['commit_uncertainty_preserved']=state['values']['submit_count']==1 and child['input_delivery_uncertain'] and not child['task_verified'] and child['diagnostic']['goal']['met']
    if name=='ocr_false_positive':
        workflow=read(directory/'session/cycle-01/workflow/summary.json')
        channels=workflow['steps'][0]['verification']
        checks['injected_ocr_does_not_override_actual_state']=channels['ocr']['met'] and not channels['state']['met'] and state['values']['submit_count']==0
    for image in screenshots:
        report=read(image.parent/'results.json')
        if report['source'].get('simulation') or report.get('synthetic_ocr'): raise ValueError('Synthetic evidence cannot be counted as desktop.')
    return {'case':name,'kind':result.get('kind','desktop_initialization_failure'),'status':result['status'],
            'error_code':result.get('error',{}).get('code'),'display':result.get('display'),
            'end_to_end_wall_seconds':result['end_to_end_wall_seconds'],'action_log_entries':len(actions),'screenshots':len(screenshots),
            'summary':(directory/'summary.json').relative_to(ROOT).as_posix(),
            'finished_at_utc':result.get('finished_at_utc',''),'source_sha256':result.get('source_sha256',{}),
            'checks':checks,'passed':all(checks.values())}


def audit_saved(directory):
    directory=Path(directory).resolve(); report=read(directory/'verification.json'); inventory=read(directory/'evidence-manifest.json')
    if any(sha(safe_path(directory,item['path']))!=item['sha256'] for item in inventory['files']): raise ValueError('Evidence hash mismatch.')
    if any(sha(safe_path(ROOT,name))!=value for name,value in report['source_sha256'].items()): raise ValueError('Recorded source version differs.')
    if len(report['robustness_cases'])!=len(SCENARIOS) or {c['case'] for c in report['robustness_cases']}!=set(SCENARIOS):
        raise ValueError('Missing/duplicate robustness case.')
    for case in report['robustness_cases']:
        parent=safe_path(ROOT,case['summary']).parents[1]
        if not parent.is_relative_to(directory): raise ValueError('Case escapes the experiment directory.')
        if audit_robustness(parent,read(parent/'session/summary.json'))!=case or not case['passed']: raise ValueError('Case rescore differs.')
    for case in report['recovery_regression_cases']:
        value=read(safe_path(ROOT,case['summary']))
        expected=RECOVERY_EXPECTED[case['case']] if case['case']!='rate_limited' else ('failed','rate_limited',1,0)
        actual=(value['status'],value.get('error',{}).get('code'),value['request_attempts'],value['input_dispatches_started'])
        if actual!=expected or not case['passed']: raise ValueError('Recovery regression differs.')
    for case in report.get('desktop_attempts',report['desktop_cases']):
        if audit_desktop(safe_path(ROOT,case['summary']).parent)!=case: raise ValueError('Desktop evidence differs.')
    if (directory/'desktop-evidence-manifest.json').exists():
        external=read(directory/'desktop-evidence-manifest.json')
        if any(sha(safe_path(ROOT,f['path']))!=f['sha256'] for f in external['files']): raise ValueError('Desktop raw evidence hash mismatch.')
    benchmark=read(safe_path(ROOT,report['benchmark']['summary']))
    if benchmark['statistics']!=report['benchmark']['statistics']: raise ValueError('Benchmark statistics changed.')
    if not report['offline_all_passed'] or not all(report['before_after_checks'].values()): raise ValueError('Checks did not pass.')
    return {'offline_all_passed':True,'robustness_cases':len(report['robustness_cases']),
            'recovery_regression_cases':len(report['recovery_regression_cases']),'files_verified':len(inventory['files']),
            'desktop_cases':len(report['desktop_cases']),'rerun':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-check',type=Path)
    parser.add_argument('--benchmark',type=Path)
    parser.add_argument('--desktop-root',type=Path,action='append',default=[])
    parser.add_argument('--audit-only',type=Path)
    args=parser.parse_args()
    if args.audit_only:
        print(json.dumps(audit_saved(args.audit_only))); return 0
    if args.project_check is None or args.benchmark is None: parser.error('--project-check and --benchmark are required for new evidence')
    output=ROOT/'output/week3-friday'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'); output.mkdir(parents=True,exist_ok=False)
    cases=[]
    for name in SCENARIOS:
        directory=output/'robustness'/name; result,_=run_robustness(directory,name); cases.append(audit_robustness(directory,result))
    # Execute Thursday scenarios against the new code, without rewriting Thursday's frozen original evidence.
    regression=[]
    for name in RECOVERY_SCENARIOS:
        directory=output/'recovery-regression'/name; result,_=run_recovery_simulation(directory,name)
        status,code,requests,dispatches=RECOVERY_EXPECTED[name]
        regression.append({'case':name,'kind':'simulation','passed':result['status']==status and result.get('error',{}).get('code')==code and
                           result['request_attempts']==requests and result['input_dispatches_started']==dispatches,
                           'summary':(directory/'session/summary.json').relative_to(ROOT).as_posix()})
    for name in HTTP_SCENARIOS:
        directory=output/'http-regression'/name; result,_,_=run_http_recovery(directory,name)
        status,code,requests,dispatches=RECOVERY_EXPECTED[name] if name!='rate_limited' else ('failed','rate_limited',1,0)
        regression.append({'case':name,'kind':'loopback_http_not_real_model','passed':result['status']==status and result.get('error',{}).get('code')==code and
                           result['request_attempts']==requests and result['input_dispatches_started']==dispatches,
                           'summary':(directory/'session/summary.json').relative_to(ROOT).as_posix()})
    before=read(ROOT/'output/week3-friday/regression-before/verification.json')
    before_source=ROOT/'output/week3-friday/regression-before/recovery-before.py.txt'
    before_checks={'original_source_hash':sha(before_source)==before['source_sha256'],
                   'old_stale_clicks_reproduced':all(c['calls'][0]['hit'] is None for c in before['cases'] if c['case']!='duplicate_appears'),
                   'old_ambiguity_was_not_blocked':next(c for c in before['cases'] if c['case']=='duplicate_appears')['status']=='completed',
                   'fixed_layout_recovers':next(c for c in cases if c['case']=='layout_moves')['status']=='completed',
                   'fixed_ambiguity_and_missing_stop_without_input':all(c['input_calls']==0 for c in cases if c['case'] in ('duplicate_appears','disappears_before_click'))}
    desktop_attempts=[]
    for root in args.desktop_root:
        for summary in sorted(root.rglob('summary.json')):
            if summary.parent.name=='session' or 'cycle-' in str(summary.parent): continue
            data=read(summary)
            if 'scenario' in data and 'planning_kind' in data: desktop_attempts.append(audit_desktop(summary.parent))
    latest={}
    for attempt in desktop_attempts:
        old=latest.get(attempt['case'])
        if old is None or (attempt['finished_at_utc'],attempt['summary'])>(old['finished_at_utc'],old['summary']): latest[attempt['case']]=attempt
    desktop=list(latest.values())
    launch_failures=[]; desktop_files=set()
    for root in args.desktop_root:
        root=root.resolve()
        if not root.is_relative_to(ROOT): raise ValueError('Desktop evidence must be inside the workspace.')
        for p in root.rglob('*'):
            if p.is_file(): desktop_files.add(p)
        for p in root.rglob('launch-failure.json'):
            launch_failures.append({'path':p.relative_to(ROOT).as_posix(),**read(p)})
    external={'files':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in sorted(desktop_files)]}
    (output/'desktop-evidence-manifest.json').write_text(json.dumps(external,ensure_ascii=False,indent=2),encoding='utf-8')
    benchmark=read(args.benchmark); project=read(args.project_check)
    unit=next(c for c in project['checks'] if c['name']=='unit_tests')
    test_log=(ROOT/unit['log']).read_text(encoding='utf-8'); new_tests=test_log.count('(test_robustness.')
    desktop_coverage=set(c['case'] for c in desktop if c['passed'])
    desktop_missing=sorted(set(DESKTOP_SCENARIOS)-desktop_coverage)
    report={'created_at_utc':datetime.now(timezone.utc).isoformat(),'version':VERSION,'directory':output.relative_to(ROOT).as_posix(),
            'new_unit_tests':new_tests,'full_project_tests':unit['test_count'],'project_check':str(args.project_check),
            'desktop_fix_unit_tests':test_log.count('(test_desktop_faults.')+
                sum(name in test_log for name in ('test_enclosing_verification_deadline_keeps_its_own_error_code','test_enclosing_lookup_deadline_keeps_its_own_error_code')),
            'robustness_cases':cases,'recovery_regression_cases':regression,'before_after_checks':before_checks,
            'status_counts':dict(Counter(c['status'] for c in cases)),'failure_categories':dict(Counter(c['error_category'] for c in cases if c['error_category'])),
            'desktop_cases':desktop,'desktop_attempts':desktop_attempts,
            'desktop_historical_failed_attempts':[c for c in desktop_attempts if not c['passed']],
            'desktop_launch_failures':launch_failures,'desktop_raw_files':len(desktop_files),
            'desktop_validation':'completed_controlled_app' if not desktop_missing and all(c['passed'] for c in desktop) else 'pending_or_incomplete',
            'desktop_required_scenarios':list(DESKTOP_SCENARIOS),'desktop_missing_scenarios':desktop_missing,
            'real_model_validation':'pending','nim_validation':'pending','scale_200_validation':'pending',
            'benchmark':{'summary':str(args.benchmark),'input_sha256':benchmark['input_sha256'],'statistics':benchmark['statistics'],
                         'cold_definition':benchmark['definition'],'warm_definition':benchmark['warm_definition']},
            'source_sha256':{name:sha(ROOT/name) for name in ('gui_workflow/recovery.py','gui_tasks/robustness.py','gui_tasks/desktop_faults.py','gui_agent/version.py','robustness_demo.py','run_robustness.py')},
            'offline_all_passed':all(c['passed'] for c in cases+regression) and all(before_checks.values()) and project['all_passed'] and new_tests==22,
            'desktop_all_passed':all(c['passed'] for c in desktop) if desktop else None}
    report['all_completed_checks_passed']=report['offline_all_passed'] and report['desktop_all_passed'] is not False
    with (output/'cases.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,['case','kind','status','error_code','error_category','possible_dispatches','input_calls','replans','passed','summary'],extrasaction='ignore')
        writer.writeheader();writer.writerows(cases)
    inventory={'files':[{'path':p.relative_to(output).as_posix(),'sha256':sha(p)} for p in sorted(output.rglob('*')) if p.is_file()]}
    (output/'evidence-manifest.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    encoded=json.dumps(report,ensure_ascii=False,indent=2)
    (output/'verification.json').write_text(encoded,encoding='utf-8'); (ROOT/'output/week3-friday/verification.json').write_text(encoded,encoding='utf-8')
    print(json.dumps({'offline_all_passed':report['offline_all_passed'],'desktop_cases':len(desktop),'new_tests':new_tests,
                      'full_project_tests':unit['test_count'],'directory':report['directory']}))
    return 0 if report['all_completed_checks_passed'] else 1


if __name__=='__main__': main()
