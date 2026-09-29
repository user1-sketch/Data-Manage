from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / 'backend' / '.venv' / 'Scripts' / 'python.exe'
if not PYTHON.exists():
    raise SystemExit('先在 backend/.venv 安装 requirements.txt')
sys.exit(subprocess.call([str(PYTHON), '-m', 'uvicorn', 'app.main:app',
                          '--host', '127.0.0.1', '--port', '8787'], cwd=ROOT / 'backend'))
