"""Export contracts without starting the server, opening a DB pool or starting workers.
Run from backend/: .venv/bin/python scripts/export_openapi.py /tmp/openapi.json
"""

import json
import sys
from pathlib import Path

from jobpilot.main import app

Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
