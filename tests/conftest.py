import os
import sys
import tempfile
from pathlib import Path

# Never let a test touch the real ~/.voxtral: point the data dir at a throwaway
# location before app.py is imported anywhere.
os.environ["VOXTRAL_HOME"] = tempfile.mkdtemp(prefix="voxtral-test-")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
