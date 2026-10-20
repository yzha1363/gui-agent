"""Isolated desktop control test surface. No interaction with personal documents."""

from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from gui_perception.pipeline import enable_dpi_awareness

TITLE = 'GUI Agent Thursday Test'


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.title(TITLE)
    root.geometry('1000x760+160+100')
    root.configure(bg='#f1f5f9')
    state = {'clicks': 0, 'mouse_buttons': [], 'drag': [90, 90], 'running': False}
    jobs = queue.Queue()
    requests = queue.Queue()
    status = tk.StringVar(value='Ready. Start the local controller test.')
    tk.Label(root, text='GUI Agent Control Test', font=('Microsoft YaHei', 24), bg='#f1f5f9').place(x=24, y=20)
    tk.Label(root, textvariable=status, font=('Microsoft YaHei', 12), bg='#f1f5f9').place(x=24, y=65)
    tk.Label(root, text='OCR Click Target', font=('Microsoft YaHei', 12), bg='#f1f5f9').place(x=24, y=148)
    def clicked():
        state['clicks'] += 1
        status.set(f"Target clicked {state['clicks']} times")
    target = tk.Button(root, text='CLICK TARGET', font=('Arial', 20), command=clicked, bg='white')
    target.place(x=24, y=180, width=260, height=60)
    entry = tk.Entry(root, font=('Microsoft YaHei', 20))
    entry.place(x=330, y=250, width=630, height=45)
    focus = tk.Button(root, text='TYPE HERE', font=('Arial', 20), command=entry.focus_set, bg='white')
    focus.place(x=330, y=180, width=240, height=60)
    target.bind('<Button-3>', lambda event: state['mouse_buttons'].append('right'))
    target.bind('<Double-Button-1>', lambda event: state['mouse_buttons'].append('double'))
    target.bind('<Button-2>', lambda event: state['mouse_buttons'].append('middle'))
    tk.Label(root, text='Scroll area', font=('Arial', 16), bg='#f1f5f9').place(x=24, y=310)
    scroll = tk.Text(root, font=('Arial', 16), wrap='none')
    scroll.place(x=24, y=345, width=400, height=255)
    scroll.insert('1.0', '\n'.join(f'Line {i:02d} - scroll test' for i in range(1, 101)))
    tk.Label(root, text='Drag the blue square', font=('Arial', 16), bg='#f1f5f9').place(x=460, y=310)
    canvas = tk.Canvas(root, bg='white', highlightthickness=1)
    canvas.place(x=460, y=345, width=500, height=255)
    box = canvas.create_rectangle(60, 60, 120, 120, fill='#2563eb')
    def dragged(event):
        x, y = max(30, min(470, event.x)), max(30, min(225, event.y))
        canvas.coords(box, x - 30, y - 30, x + 30, y + 30)
        state['drag'] = [x, y]
    canvas.tag_bind(box, '<B1-Motion>', dragged)
    canvas.tag_bind(box, '<ButtonRelease-1>', dragged)
    tk.Label(root, text='Escape stops input. Primary-monitor corners trigger PyAutoGUI fail-safe.',
             font=('Arial', 12), bg='#f1f5f9').place(x=24, y=635)
    tk.Label(root, text='Results are saved under output/thursday-validation',
             font=('Arial', 12), bg='#f1f5f9').place(x=24, y=665)

    def snapshot():
        root.update_idletasks()
        def center(widget):
            return [widget.winfo_rootx() + widget.winfo_width() // 2,
                    widget.winfo_rooty() + widget.winfo_height() // 2]
        return {'clicks': state['clicks'], 'text': entry.get(), 'scroll': list(scroll.yview()),
                'drag': list(state['drag']), 'buttons': list(state['mouse_buttons']),
                'target': center(target), 'input': center(entry), 'scroll_center': center(scroll),
                'canvas_origin': [canvas.winfo_rootx(), canvas.winfo_rooty()],
                'region': [root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()]}

    def ui_snapshot():
        response = queue.Queue()
        requests.put(response)
        return response.get(timeout=10)

    def worker():
        from gui_control.controller import ControlError, DesktopController
        from gui_control.targets import select_text
        from gui_perception.pipeline import capture_screen, create_reader, save_run
        directory = Path('output/thursday-validation') / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        directory.mkdir(parents=True)
        checks = {}
        summary = {'passed': False, 'checks': checks, 'directory': str(directory)}
        try:
            with DesktopController(TITLE, log_path=directory / 'actions.jsonl') as control:
                layout = ui_snapshot()
                jobs.put('Loading local OCR model and checking screenshot coordinates...')
                reader, device = create_reader('cuda')
                image, source = capture_screen(region=layout['region'])
                report = save_run(image, source, directory / 'before', reader, device)
                selected = select_text(report, 'CLICK TARGET')
                expected = layout['target']
                selected_point = selected['desktop_center']
                summary['target'] = selected
                summary['coordinate_error_px'] = [selected_point[0] - expected[0], selected_point[1] - expected[1]]
                checks['ocr_center_within_button'] = abs(selected_point[0] - expected[0]) < 130 and abs(selected_point[1] - expected[1]) < 30
                control.click(*selected_point)
                checks['ocr_click'] = ui_snapshot()['clicks'] == 1
                control.click(*layout['target'], button='right')
                checks['right_click'] = 'right' in ui_snapshot()['buttons']
                control.click(*layout['target'], button='middle')
                checks['middle_click'] = 'middle' in ui_snapshot()['buttons']
                control.click(*layout['target'], clicks=2)
                checks['double_click'] = 'double' in ui_snapshot()['buttons']
                focus_target = select_text(report, 'TYPE HERE')
                control.click(*focus_target['desktop_center'])
                control.type_text('GUI Agent 123 中文输入测试')
                checks['unicode_input'] = ui_snapshot()['text'] == 'GUI Agent 123 中文输入测试'
                control.hotkey('ctrl', 'a')
                control.type_text('Hello GUI 中文')
                checks['hotkey_replace'] = ui_snapshot()['text'] == 'Hello GUI 中文'
                control.press('end')
                control.press('left')
                control.type_text('X')
                checks['single_key'] = ui_snapshot()['text'] == 'Hello GUI 中X文'
                initial_scroll = ui_snapshot()['scroll'][0]
                control.click(*layout['scroll_center'])
                control.scroll(-5, *layout['scroll_center'])
                checks['scroll_down'] = ui_snapshot()['scroll'][0] > initial_scroll
                control.scroll(5, *layout['scroll_center'])
                checks['scroll_up'] = ui_snapshot()['scroll'][0] <= initial_scroll + 0.001
                ox, oy = layout['canvas_origin']
                control.drag(ox + 90, oy + 90, ox + 350, oy + 180)
                checks['drag'] = all(abs(a - b) <= 3 for a, b in zip(ui_snapshot()['drag'], [350, 180]))
                try:
                    control.click(-10, -10)
                except ControlError:
                    checks['reject_out_of_bounds'] = True
                control.press('esc')
                time.sleep(0.2)
                try:
                    control.click(*layout['target'])
                except ControlError:
                    checks['stop_blocks_input'] = True
                final = ui_snapshot()
                image, source = capture_screen(region=layout['region'])
                image.save(directory / 'after.png')
                summary['final_state'] = final
                summary['passed'] = len(checks) == 13 and all(checks.values())
                jobs.put('PASS - all local control checks passed' if summary['passed'] else 'FAIL - inspect summary.json')
        except BaseException as exc:
            summary['error'] = type(exc).__name__ + ': ' + str(exc)
            jobs.put('FAIL - ' + summary['error'])
        finally:
            (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(summary, ensure_ascii=True), flush=True)
            state['running'] = False

    def start():
        if state['running']:
            return
        state['running'] = True
        state['clicks'] = 0
        state['mouse_buttons'] = []
        state['drag'] = [90, 90]
        canvas.coords(box, 60, 60, 120, 120)
        entry.delete(0, 'end')
        scroll.yview_moveto(0)
        status.set('Running local controller test. Keep this window in the foreground.')
        threading.Thread(target=worker, daemon=True).start()

    tk.Button(root, text='START TEST', font=('Arial', 16), command=start).place(x=24, y=100, width=220, height=40)
    def pump():
        while not requests.empty():
            requests.get_nowait().put(snapshot())
        while not jobs.empty():
            status.set(jobs.get_nowait())
        root.after(30, pump)
    pump()
    root.mainloop()


if __name__ == '__main__':
    main()
