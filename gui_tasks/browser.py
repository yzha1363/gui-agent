"""Real desktop fixtures using an isolated Edge profile and loopback-only UI."""

import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from gui_tasks.basic import FIELDS, LocalTaskState, reply_for, task_spec


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>body{font:24px Arial;margin:28px;background:#f8fafc;color:#111}h1{font-size:28px}
button{font:24px Arial;padding:18px;margin:12px 24px 18px 0;background:white;border:1px solid #555}
input{display:block;font:28px Arial;width:92%;padding:14px;margin:24px 0}
#status{font-size:30px;color:#14532d;margin:30px 0}#result{margin:35px 0;font-size:25px;white-space:pre-wrap}
</style></head><body><h1>Local GUI task sandbox</h1><p>__CASE__</p><div id="status">READY</div>
__FOCUS__<button onclick="send('finish')">__ACTION__</button>
<input id="entry" aria-label="Test input" oninput="send('input',this.value)">
<div id="result"></div><p>This page uses only a local test catalog, sample file and inbox.</p>
<script>
let chain=Promise.resolve();
function send(action,value='') {
  chain=chain.then(async()=>{
    const r=await fetch(location.pathname+'/event',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action,value})});
    if(!r.ok) throw new Error('Local event rejected');
    const s=await r.json();
    document.getElementById('status').textContent=s.status;
    document.getElementById('result').textContent=s.result || s.last_message;
  }).catch(()=>{document.getElementById('status').textContent='LOCAL EVENT ERROR'});
}
function focusInput(){document.getElementById('entry').focus();send('focus');}
</script></body></html>'''


class LocalWebApp:
    def __init__(self, app, title):
        self.app, self.title, self.lock = app, title, threading.RLock()
        self.path = '/' + uuid.uuid4().hex
        self.loaded = threading.Event()
        owner = self
        focus = '<button onclick="focusInput()">FOCUS INPUT</button>' if app.case in ('search', 'send_message') else ''
        if app.fault == 'missing':
            focus = ''
        if app.fault == 'duplicate':
            focus += focus
        self.html = PAGE.replace('__TITLE__', title).replace('__CASE__', app.case).replace('__FOCUS__', focus).replace(
            '__ACTION__', task_spec(app.case).steps[-1]['text']).encode('utf-8')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, body, content_type):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != owner.path:
                    self.respond(404, b'Not found', 'text/plain')
                    return
                owner.loaded.set()
                self.respond(200, owner.html, 'text/html; charset=utf-8')

            def do_POST(self):
                if self.path != owner.path + '/event' or self.headers.get('Origin') != owner.origin:
                    self.respond(403, b'Rejected', 'text/plain')
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        raise ValueError('Invalid payload size.')
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict) or set(data) != {'action', 'value'}:
                        raise ValueError('Unsupported fields.')
                    with owner.lock:
                        state = owner.app.event(data['action'], data['value'])
                    self.respond(200, json.dumps(state).encode(), 'application/json')
                except (ValueError, TypeError, KeyError):
                    self.respond(400, b'Invalid local event', 'text/plain')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.url = self.origin + self.path
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def snapshot(self):
        with self.lock:
            return self.app.snapshot()


class OwnedBrowser:
    """Record one uniquely titled window; close only that same HWND and PID."""
    def __init__(self, directory, title, url):
        candidates = [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                      Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe')]
        self.executable = next((p for p in candidates if p.is_file()), None)
        if self.executable is None:
            raise FileNotFoundError('Microsoft Edge is required for the desktop fixture.')
        self.directory, self.title, self.url = Path(directory), title, url
        self.handle, self.pid = None, None
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    def name(self, handle):
        text = ctypes.create_unicode_buffer(1024)
        self.user32.GetWindowTextW(handle, text, len(text))
        return text.value

    def process_id(self, handle):
        value = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(value))
        return value.value

    def find(self):
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(handle, _):
            name = self.name(handle)
            if name == self.title or name.startswith(self.title + ' - '):
                found.append(handle)
            return True
        self.user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user32.EnumWindows(callback_type(callback), 0)
        if len(found) > 1:
            raise RuntimeError('Duplicate isolated browser windows; refusing to guess.')
        return found[0] if found else None

    def open(self):
        profile = (self.directory / 'edge-profile').resolve()
        profile.mkdir(parents=True, exist_ok=False)
        subprocess.Popen([str(self.executable), '--app=' + self.url, '--user-data-dir=' + str(profile),
                          '--no-first-run', '--no-default-browser-check', '--disable-sync',
                          '--disable-background-networking', '--window-size=1100,800', '--window-position=100,60'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            handle = self.find()
            if handle:
                self.handle, self.pid = handle, self.process_id(handle)
                # Apply bounds to this owned window only, within the primary screen.
                self.user32.SetWindowPos(handle, None, 100, 60, 1100, 800, 0x0040)
                self.focus()
                return
            time.sleep(.1)
        raise TimeoutError('Isolated browser window did not appear.')

    def is_open(self):
        return bool(self.window_exists() and
                    (self.name(self.handle) == self.title or self.name(self.handle).startswith(self.title + ' - ')))

    def window_exists(self):
        """A title change must not be mistaken for actual window destruction."""
        return bool(self.handle and self.user32.IsWindow(self.handle) and self.process_id(self.handle) == self.pid)

    def focus(self):
        if not self.is_open():
            raise RuntimeError('Cannot focus a window outside the owned browser identity.')
        self.user32.ShowWindow(self.handle, 9)
        self.user32.SetForegroundWindow(self.handle)
        if self.user32.GetForegroundWindow() == self.handle:
            return
        # Startup only: connect the current thread to the foreground input queue
        # while activating this recorded HWND, and always detach immediately.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        current = kernel32.GetCurrentThreadId()
        foreground = self.user32.GetForegroundWindow()
        foreground_thread = self.user32.GetWindowThreadProcessId(foreground, None)
        attached = current != foreground_thread and self.user32.AttachThreadInput(current, foreground_thread, True)
        try:
            self.user32.SetForegroundWindow(self.handle)
        finally:
            if attached:
                self.user32.AttachThreadInput(current, foreground_thread, False)

    def close(self):
        if not self.window_exists():
            return
        if not self.is_open():
            raise RuntimeError('Owned window title changed; refusing to close it.')
        self.user32.PostMessageW(self.handle, 0x0010, 0, 0)  # WM_CLOSE, not process termination.
        end = time.monotonic() + 8
        while self.window_exists() and time.monotonic() < end:
            time.sleep(.05)
        if self.window_exists():
            raise TimeoutError('Owned browser window did not close; no forced termination.')


def execute_session(app, title, directory, device, state_provider, busy=None):
    from gui_agent.client import ReplayClient
    from gui_agent.planner import GuiPlanner
    from gui_agent.session import AgentSession
    from gui_control.controller import DesktopController, FocusLost, StopRequested
    from gui_control.unicode_input import click_button
    from gui_workflow.observer import ScreenObserver

    class FixtureController(DesktopController):
        def check(self, point=None):
            # Opening the owned child briefly changes foreground. Never dispatch
            # while its lifecycle operation is in progress; recheck the guard after.
            end = time.monotonic() + 30
            while busy is not None and busy.is_set():
                if self.stopped.is_set():
                    raise StopRequested('Stop during owned child transition.')
                if time.monotonic() >= end:
                    raise TimeoutError('Owned child transition timed out.')
                time.sleep(.02)
            super().check(point)

    spec = task_spec(app.case)
    with FixtureController(title, log_path=directory / 'actions.jsonl', click_sender=click_button) as control:
        deadline = time.monotonic() + 30
        announced = False
        while True:
            try:
                control.check()
                break
            except FocusLost:
                if not announced:
                    print('Waiting for test window foreground: ' + title, flush=True)
                    announced = True
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        def state():
            control.guard.active()
            return {'source': 'local-test-application-events', 'values': state_provider()}
        observer = ScreenObserver(control, directory / 'observations', device, state_provider=state)
        if app.fault == 'false_ocr':
            observe = observer.observe
            def injected(label):
                snapshot = observe(label)
                if '_after' in label:
                    snapshot.report['results'].append({'id': 999, 'text': spec.goal['text'], 'confidence': .99})
                    snapshot.report['fault_injection'] = 'Explicit successful OCR text; independent state unchanged'
                    (snapshot.directory / 'results.json').write_text(json.dumps(snapshot.report, indent=2), encoding='utf-8')
                return snapshot
            observer.observe = injected
        timeout = 15 if app.case in ('open_browser', 'close_app') else (1 if app.fault != 'none' else 5)
        planner = GuiPlanner(ReplayClient(json.dumps(reply_for(spec, timeout))))
        summary = AgentSession(control, observer, planner, directory / 'session').run(
            spec.instruction, title, FIELDS, spec.goal, execute=True)
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        summary['display'] = {'dpi': user32.GetDpiForWindow(control.guard.window_handle),
                              'screen_physical_pixels': list(control.backend.size())}
        summary['display']['scale_percent'] = round(summary['display']['dpi'] / 96 * 100)
        summary['desktop_fixture'] = 'owned-edge-lifecycle-host' if busy is not None else 'loopback-edge-page'
        (directory / 'session/summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        return summary


def run_desktop(case, directory, fault='none', device='cuda'):
    """No external websites, messages, personal browser profile or user app closes."""
    from gui_perception.pipeline import enable_dpi_awareness
    enable_dpi_awareness()
    directory = Path(directory)
    app = LocalTaskState(case, directory / 'fixture', fault)
    title = 'GUI Basic ' + case + ' ' + uuid.uuid4().hex[:10]
    with LocalWebApp(app, title) as web:
        browser = OwnedBrowser(directory, title, web.url)
        try:
            if case not in ('open_browser', 'close_app'):
                browser.open()
                if not web.loaded.wait(5):
                    raise TimeoutError('Local page did not load.')
                time.sleep(.8)
                browser.focus()
                result = execute_session(app, browser.name(browser.handle), directory, device, web.snapshot)
            else:
                result = run_lifecycle_host(app, browser, directory, device)
            (directory / 'browser.json').write_text(json.dumps({'title': title, 'hwnd': browser.handle, 'pid': browser.pid,
                'isolated_profile': str(directory / 'edge-profile'), 'page_loaded': web.loaded.is_set(),
                'window_open_after_task': browser.window_exists(), 'setup_launch': case != 'open_browser'}, indent=2), encoding='utf-8')
            return result, app
        finally:
            browser.close()


def run_lifecycle_host(app, browser, directory, device):
    """Keep the observer on a live supervisor while the owned child opens/closes."""
    import tkinter as tk
    root = tk.Tk()
    title = 'GUI Lifecycle ' + uuid.uuid4().hex[:10]
    root.title(title)
    root.geometry('1000x650+130+100')
    root.configure(bg='white')
    busy, jobs, results = threading.Event(), queue.Queue(), queue.Queue()
    label = tk.StringVar(value='READY')
    tk.Label(root, text='Owned browser lifecycle test', font=('Arial', 26), bg='white').place(x=30, y=25)
    tk.Label(root, textvariable=label, font=('Arial', 28), fg='#14532d', bg='white').place(x=30, y=90)
    tk.Label(root, text='Only the isolated browser created for this run is controlled.',
             font=('Arial', 17), bg='white').place(x=30, y=350)
    tk.Label(root, text='Escape stops agent input.', font=('Arial', 16), bg='white').place(x=30, y=430)
    def lifecycle(case):
        if case == 'open_browser':
            browser.open()
            if not browser.is_open():
                raise RuntimeError('Owned browser did not open.')
        else:
            if not browser.is_open():
                raise RuntimeError('Owned browser was already absent before CLOSE APP.')
            browser.close()
            if browser.window_exists():
                raise RuntimeError('Owned browser is still open.')
    app.lifecycle = lifecycle

    def clicked():
        busy.set()
        try:
            app.event('finish')
            label.set(app.values['status'])
        except Exception as exc:
            app.values.update(app_error=type(exc).__name__, status='LIFECYCLE ERROR')
            label.set('LIFECYCLE ERROR')
        finally:
            root.lift()
            root.focus_force()
            root.update_idletasks()
            busy.clear()
    tk.Button(root, text=task_spec(app.case).steps[-1]['text'], font=('Arial', 24), command=clicked,
              bg='white').place(x=520, y=200, width=400, height=80)

    def start():
        if app.case == 'close_app':
            try:
                browser.open()
            except Exception as exc:
                results.put(exc)
                root.after(10, root.destroy)
                return
        root.lift()
        root.focus_force()
        def independent_state():
            values = app.snapshot()
            values['browser_open'] = browser.window_exists()
            values['app_closed'] = app.case == 'close_app' and not browser.window_exists()
            return values
        def worker():
            try:
                results.put(execute_session(app, title, directory, device, independent_state, busy))
            except BaseException as exc:
                results.put(exc)
            jobs.put('done')
        root.after(600, lambda: threading.Thread(target=worker, daemon=True).start())
    def pump():
        if not jobs.empty():
            root.destroy()
            return
        root.after(40, pump)
    root.after(400, start)
    root.after(40, pump)
    root.mainloop()
    if results.empty():
        raise RuntimeError('Lifecycle host closed before test completion.')
    result = results.get()
    if isinstance(result, BaseException):
        raise result
    return result
