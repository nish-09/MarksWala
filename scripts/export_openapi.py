"""Export the backend OpenAPI schema (used to generate the frontend's API types).

    backend/.venv/Scripts/python scripts/export_openapi.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.main import app  # noqa: E402

out = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=1), encoding="utf-8")
print("wrote", out, "-", len(app.openapi()["paths"]), "paths")
