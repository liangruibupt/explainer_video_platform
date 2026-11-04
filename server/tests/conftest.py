import os
import sys
import tempfile
from pathlib import Path

# Keep test runs out of the real data/ folder: pipeline.DATA is read at import time.
os.environ.setdefault("EVP_DATA", tempfile.mkdtemp(prefix="evp-test-data-"))
os.environ["EVP_WF_WARM_GPUS"] = "0"  # stub nodes declare gpu:image; never boot a real GPU
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
