"""Create an optional venv sharing the current project's CUDA packages read-only."""

from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = ROOT / '.venv'
    if Path(sys.prefix).resolve() != base.resolve():
        raise SystemExit('Run this script with .venv/Scripts/python.exe.')
    target = ROOT / '.venv-training'
    if not (target / 'Scripts/python.exe').is_file():
        venv.EnvBuilder(with_pip=True).create(target)
    shared = base / 'Lib/site-packages'
    (target / 'Lib/site-packages/project_runtime.pth').write_text(str(shared.resolve()) + '\n', encoding='utf-8')
    subprocess.run([str(target / 'Scripts/python.exe'), '-c', 'import sys,torch;print(sys.executable);print(torch.__version__)'], check=True)
    print('Next: .\\.venv-training\\Scripts\\python.exe -m pip install -r requirements-training.txt')


if __name__ == '__main__': main()
