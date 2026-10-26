"""Generate one GUI proposal; this CLI never creates a desktop controller."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw

from gui_agent import AgentError, AgentInput, ChatClient, GuiAgent, ModelConfig, ReplayClient
from gui_agent.contracts import load_dataset_input

ROOT = Path(__file__).resolve().parent
DEMO_RESPONSE = {'status': 'propose', 'reason': 'Focus the visible input entry before typing.',
                 'step': {'name': 'focus_input', 'action': 'click_text', 'text': 'INPUT FIELD',
                          'expect': {'text': 'INPUT READY', 'state': {'input_focused': True, 'status': 'INPUT READY'}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--demo', action='store_true', help='Use a synthetic image and explicit replay fixture')
    inputs.add_argument('--image', type=Path, help='Use an existing screenshot; does not capture the desktop')
    inputs.add_argument('--dataset', type=Path, help='Read an audited Monday processed bundle without labels')
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--instruction')
    parser.add_argument('--window-title')
    parser.add_argument('--state-fields', nargs='+', default=[], help='Known independent adapter field names; otherwise propose must clarify')
    parser.add_argument('--history', type=Path, help='JSON array of previous action text')
    parser.add_argument('--replay', type=Path, help='Use a saved JSON model reply instead of contacting a service')
    parser.add_argument('--base-url', default=os.environ.get('GUI_AGENT_BASE_URL', 'http://127.0.0.1:8000/v1'))
    parser.add_argument('--model', default=os.environ.get('GUI_AGENT_MODEL'))
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'output/week2-tuesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    try:
        if args.demo:
            if args.replay or args.history or args.instruction or args.window_title or args.state_fields:
                raise AgentError('invalid_input', '--demo uses its own explicit image, task and replay fixture.')
            assets = output.parent / (output.name + '-assets')
            assets.mkdir(parents=True, exist_ok=False)
            image = Image.new('RGB', (640, 360), 'white')
            draw = ImageDraw.Draw(image)
            draw.text((30, 30), 'GUI Agent synthetic fixture', fill='black')
            draw.rectangle((30, 90, 580, 150), outline='black', width=2)
            draw.text((45, 110), 'INPUT FIELD', fill='black')
            image.save(assets / 'demo.png')
            task = AgentInput('Focus INPUT FIELD. State adapter fields: input_focused (bool), status (str).',
                              assets / 'demo.png', 'GUI Agent Friday Demo', state_fields=('input_focused', 'status'))
            client = ReplayClient(json.dumps(DEMO_RESPONSE))
        else:
            if not args.window_title:
                raise AgentError('invalid_input', 'Provide a caller-controlled --window-title.')
            if args.dataset:
                if args.instruction or args.history:
                    raise AgentError('invalid_input', 'Dataset mode reads instruction and past history from the record.')
                task = load_dataset_input(args.dataset, args.index, args.window_title, args.state_fields)
            else:
                history = json.loads(args.history.read_text(encoding='utf-8')) if args.history else []
                task = AgentInput(args.instruction, args.image, args.window_title, history, args.state_fields)
            if args.replay:
                client = ReplayClient(args.replay.read_text(encoding='utf-8'))
            else:
                client = ChatClient(ModelConfig(args.base_url, args.model, os.environ.get('GUI_AGENT_API_KEY', ''),
                                               args.timeout, args.max_tokens))
        summary = GuiAgent(client).run(task, output)
    except (AgentError, ValueError, OSError) as exc:
        code = getattr(exc, 'code', 'input_or_output_error')
        print(json.dumps({'status': 'failed', 'error': {'code': code}, 'desktop_input_sent': False}))
        return 2
    print(json.dumps({'status': summary['status'], 'client_kind': summary['client_kind'],
                      'summary': str(output / 'summary.json'), 'desktop_input_sent': False,
                      'task_verified': False, 'error': summary.get('error')}, ensure_ascii=True))
    if summary['status'] == 'aborted':
        return 130
    return 1 if summary['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
