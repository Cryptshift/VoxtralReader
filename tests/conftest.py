import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Never let a test touch the real ~/.voxtral: point the data dir at a throwaway
# location before app.py is imported anywhere, and remove it afterwards.
_home = tempfile.mkdtemp(prefix="voxtral-test-")
atexit.register(shutil.rmtree, _home, ignore_errors=True)
os.environ["VOXTRAL_HOME"] = _home

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
