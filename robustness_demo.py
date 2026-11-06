"""Dedicated real Tk test window. Input starts only after BEGIN ROBUSTNESS."""

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from gui_perception.pipeline import enable_dpi_awareness
from gui_tasks.robustness import FIELDS, TEXT, RobustnessApp, contracts
from gui_workflow.verification import publish_state

ROOT = Path(__file__).resolve().parent
TITLE = 'GUI Agent V2 Robustness'
DESKTOP_SCENARIOS = ('normal', 'layout_shifted', 'layout_moves', 'duplicate', 'duplicate_second', 'missing',
                     'wrong_text', 'ocr_false_positive', 'stale_state', 'partial_input', 'submit_uncertain')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=DESKTOP_SCENARIOS, default='normal')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--device', choices=('cuda', 'cpu', 'auto'), default='cuda')
    args = parser.parse_args()
    output = args.output or ROOT / 'output/week3-friday/desktop' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True, exist_ok=False)
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                   ('robustness_demo.py','gui_workflow/recovery.py','gui_agent/recovery.py')}
    enable_dpi_awareness(); root = tk.Tk(); root.title(TITLE); root.geometry('1000x680+100+80'); root.configure(bg='white')
    jobs = queue.Queue(); stop_requested = threading.Event()
    state = dict(running=False, control=None, submit_count=0, focus_target='', state_write_denials=0)
    entry_value = tk.StringVar(); status = tk.StringVar(value='WAITING'); submitted = tk.StringVar(); progress = tk.StringVar(value='Ready: press BEGIN ROBUSTNESS')
    tk.Label(root, text='GUI Agent V2 - Dedicated Test', font=('Arial', 24), bg='white').place(x=25, y=15)
    tk.Label(root, text='Scenario: ' + args.scenario, font=('Arial', 17), bg='white').place(x=25, y=58)
    tk.Label(root, textvariable=status, font=('Arial', 22), fg='#166534', bg='white').place(x=25, y=102)
    entry = tk.Entry(root, textvariable=entry_value, font=('Microsoft YaHei', 22))
    entry.place(x=25, y=435, width=930, height=48)
    tk.Label(root, textvariable=submitted, font=('Microsoft YaHei', 21), bg='white').place(x=25, y=510)
    tk.Label(root, textvariable=progress, font=('Arial', 12), bg='white', wraplength=930, justify='left').place(x=25, y=570)
    tk.Label(root, text='Test data only. Escape/STOP aborts. Keep this window in the foreground.', font=('Arial', 12), bg='white').place(x=25, y=635)
    def focus(name):
        if args.scenario != 'ocr_false_positive':
            entry.focus_set(); state['focus_target'] = name; status.set('INPUT READY')
    primary = tk.Button(root, text='INPUT FIELD', font=('Arial', 22), command=lambda: focus('primary'))
    secondary = tk.Button(root, text='INPUT FIELD', font=('Arial', 22), command=lambda: focus('secondary'))
    def submit():
        state['submit_count'] += 1; submitted.set('SUBMITTED ' + entry_value.get())
    button = tk.Button(root, text='SUBMIT', font=('Arial', 22), command=submit)
    widgets = {'primary': primary, 'secondary': secondary, 'submit': button}
    layout = RobustnessApp(args.scenario if args.scenario in ('layout_shifted', 'duplicate', 'duplicate_second', 'missing') else 'normal').controls
    def place_layout():
        for widget in widgets.values(): widget.place_forget()
        for name, box in layout.items():
            x1, y1, x2, y2 = box
            widgets[name].place(x=round(x1*1.45), y=round(y1*1.4)+92, width=round((x2-x1)*1.45), height=round((y2-y1)*1.4))
    place_layout()
    def changed(*unused):
        if args.scenario == 'wrong_text' and entry_value.get() == TEXT: entry_value.set(TEXT + '!')
    entry_value.trace_add('write', changed)
    def ui_call(function):
        done = threading.Event(); box = {}
        jobs.put(('ui_call', (function, done, box)))
        if not done.wait(5): raise TimeoutError('Dedicated test UI did not acknowledge the request.')
        if 'error' in box: raise box['error']
        return box.get('value')
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]; user32.GetAncestor.restype = wintypes.HWND
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]; user32.GetDpiForWindow.restype = wintypes.UINT
    def hwnd(): return user32.GetAncestor(root.winfo_id(), 2)
    def display():
        dpi = user32.GetDpiForWindow(hwnd())
        return {'window_hwnd': hwnd(), 'dpi': dpi, 'scale_percent': round(dpi/96*100),
                'screen_physical_pixels': [root.winfo_screenwidth(), root.winfo_screenheight()]}
    def values_snapshot():
        return dict(input_focused=root.focus_get()==entry, status=status.get(), entry_text=entry_value.get(), submitted=submitted.get(),
                    submit_enabled=str(button['state'])=='normal', focus_target=state['focus_target'], submit_count=state['submit_count'])
    state_path = output / 'application-state.json'
    def worker():
        from dataclasses import replace
        from gui_agent import GuiPlanner, ReplayClient
        from gui_agent.recovery import RecoverySession
        from gui_control.controller import DesktopController, StopRequested
        from gui_control.unicode_input import click_button
        from gui_perception.pipeline import capture_screen, save_run
        from gui_workflow.observer import ScreenObserver
        from gui_workflow.recovery import RecoveryPolicy
        from gui_workflow.verification import StateFile, failure_category
        begin = time.perf_counter(); result = None; timings = []; cold_seconds = None; warmup = None; session_entered = False
        try:
            measured_display = ui_call(display)
            with DesktopController(TITLE, log_path=output / 'actions.jsonl', click_sender=click_button) as native:
                state['control'] = native
                if stop_requested.is_set(): native.stop()
                # Capture a real initial frame even if the state adapter is intentionally stale.
                image, source = capture_screen(region=native.window_region())
                save_run(image, source, output / 'initial-capture')
                init = time.perf_counter()
                observer = ScreenObserver(native, output / 'observations', args.device, state_provider=StateFile(state_path, native))
                cold_seconds = time.perf_counter() - init
                observe = observer.observe; moved = False
                def measured_observe(label):
                    nonlocal moved
                    if args.scenario == 'layout_moves' and 'step01_predispatch' in label and not moved:
                        def move(): layout['primary'] = [30, 170, 270, 215]; place_layout()
                        ui_call(move); moved = True
                    start = time.perf_counter(); snapshot = observe(label)
                    if args.scenario == 'ocr_false_positive' and 'step01_after' in label:
                        raw = output / 'raw-ocr'; raw.mkdir(exist_ok=True)
                        (raw / (snapshot.directory.name + '.json')).write_text(json.dumps(snapshot.report, ensure_ascii=False, indent=2), encoding='utf-8')
                        snapshot.report['results'].append({'id': 999, 'text': 'INPUT READY', 'confidence': .99, 'bbox': [20, 100, 330, 140]})
                        snapshot.report['fault_injection'] = 'OCR false positive; original report saved separately'
                        (snapshot.directory / 'results.json').write_text(json.dumps(snapshot.report, ensure_ascii=False, indent=2), encoding='utf-8')
                    timings.append({'label': label, 'wall_seconds': round(time.perf_counter()-start,6), 'ocr_ms': snapshot.report['ocr_ms'],
                                    'directory': str(snapshot.directory)})
                    return snapshot
                observer.observe = measured_observe
                if args.scenario != 'stale_state':
                    warm = measured_observe('reader_warmup'); warmup = warm.report['ocr_ms']
                class FaultControl:
                    def __getattr__(self, name): return getattr(native, name)
                    def type_text(self, text):
                        native.type_text(text[:3] if args.scenario == 'partial_input' else text)
                        if args.scenario == 'partial_input': raise RuntimeError('Injected failure after real partial input.')
                    def click(self, x, y):
                        is_submit,before=ui_call(lambda:(button.winfo_rootx()<=x<button.winfo_rootx()+button.winfo_width() and
                                                       button.winfo_rooty()<=y<button.winfo_rooty()+button.winfo_height(),state['submit_count']))
                        native.click(x, y)
                        if args.scenario == 'submit_uncertain' and is_submit:
                            from gui_tasks.desktop_faults import inject_after_submit_acknowledgement
                            inject_after_submit_acknowledgement(before,lambda:ui_call(lambda:state['submit_count']))
                reply, goal, instruction = contracts(args.scenario, timeout=5)
                planner = GuiPlanner(ReplayClient(json.dumps(reply, ensure_ascii=False)))
                # Explicit desktop laboratory profile: bounded but accommodates screenshot/OCR latency.
                policy = replace(RecoveryPolicy(), session_timeout=120, observation_timeout=15, lookup_timeout=30,
                                 verification_timeout=5, max_snapshot_age=10)
                session_entered = True
                result = RecoverySession(FaultControl(), observer, planner, output / 'session', policy,
                                          progress=lambda stage: jobs.put(('progress', stage))).run(
                                              instruction, TITLE, FIELDS, goal, execute=True)
            result.update(scenario=args.scenario, kind='real_desktop_with_explicit_fault_injection' if args.scenario not in ('normal','layout_shifted') else 'real_desktop_controlled_app',
                          display=measured_display, reader_initialization_seconds=round(cold_seconds,6), warmup_ocr_ms=warmup,
                          observation_timings=timings, end_to_end_wall_seconds=round(time.perf_counter()-begin,6),
                          real_model_validation='pending', planning_kind='replay', state_file=str(state_path), source_sha256=source_hashes)
        except (Exception, KeyboardInterrupt) as exc:
            result = {'status': 'aborted' if isinstance(exc,(StopRequested,KeyboardInterrupt)) else 'initialization_failed',
                      'success': False, 'task_verified': False, 'scenario': args.scenario,
                      'error': {'code': type(exc).__name__, 'category': failure_category(exc), 'message': str(exc)},
                      'end_to_end_wall_seconds': round(time.perf_counter()-begin,6), 'reader_initialization_seconds': cold_seconds,
                      'observation_timings': timings, 'real_model_validation': 'pending', 'planning_kind': 'replay'}
            result.update(desktop_input_sent=session_entered, desktop_input_uncertain=session_entered)
        finally:
            state['control'] = None
            try:
                final_state = ui_call(values_snapshot)
                (output/'final-state.json').write_text(json.dumps({'source':'actual-tk-widgets','values':final_state},ensure_ascii=False,indent=2),encoding='utf-8')
            except (Exception, KeyboardInterrupt) as exc:
                result['final_state_capture_error'] = type(exc).__name__
            (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            jobs.put(('finished', result['status'])); print(json.dumps({'status': result['status'], 'summary': str(output/'summary.json')},ensure_ascii=True),flush=True)
    def begin():
        if state['running']: return
        if (output/'summary.json').exists():
            progress.set('This run is immutable. Close and start a new run.'); return
        stop_requested.clear()
        state['running'] = True; progress.set('Running dedicated test; Escape/STOP aborts.')
        threading.Thread(target=worker,daemon=True).start()
    def stop():
        stop_requested.set()
        if state['control'] is not None: state['control'].stop()
    tk.Button(root,text='BEGIN ROBUSTNESS',font=('Arial',16),command=begin).place(x=25,y=165,width=285,height=45)
    tk.Button(root,text='STOP',font=('Arial',16),command=stop).place(x=330,y=165,width=120,height=45)
    def pump():
        values = values_snapshot()
        timestamp = time.time() - (10 if args.scenario == 'stale_state' else 0)
        if not publish_state(state_path, {'timestamp':timestamp,'hwnd':hwnd(),'values':values}): state['state_write_denials'] += 1
        while not jobs.empty():
            kind,value = jobs.get_nowait()
            if kind == 'ui_call':
                function,done,box = value
                try: box['value'] = function()
                except Exception as exc: box['error'] = exc
                finally: done.set()
            else:
                progress.set(value)
                if kind=='finished': state['running']=False
        root.after(40,pump)
    root.protocol('WM_DELETE_WINDOW',lambda: stop() if state['running'] else root.destroy())
    root.update_idletasks(); pump()
    (output/'ready.json').write_text(json.dumps({'title':TITLE,'scenario':args.scenario,'display':display(),'requires_begin':True,'source_sha256':source_hashes},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ready':True,'output':str(output),'scenario':args.scenario},ensure_ascii=True),flush=True)
    root.mainloop()


if __name__=='__main__': main()
