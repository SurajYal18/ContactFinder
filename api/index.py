import os
import sys

# Compute project directories
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

# Ensure Python can resolve modules from both root and backend directories
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

# Import the FastAPI application instance for Vercel
try:
    from backend.server import app
except (ImportError, ModuleNotFoundError):
    from server import app
