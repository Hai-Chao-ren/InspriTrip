from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"
DEMO_DATA_DIR = REPO_ROOT / "data" / "demo"
PRIVATE_DATA_DIR = REPO_ROOT / "data" / "private"
PIPELINE_OUTPUT_DIR = REPO_ROOT / "output"
SITE_DIR = REPO_ROOT / "site"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
