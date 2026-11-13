import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("06_Risk_Management", "11_Performance_Analytics"):
    sys.path.insert(0, str(ROOT / sub))
