"""Environment + file configuration for AI Animal Lab.

Binding spec: ../API_CONTRACT.md ("Environment / configuration").

All values are read once at import time (the process is launched with the
desired environment). `app/config.json` holds the category list and is
(self-)created with defaults when missing; at least 3 categories are required
for the 3-choice labelling scheme.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent            # .../backend/app
BACKEND_DIR = APP_DIR.parent                          # .../backend
PROJECT_ROOT = BACKEND_DIR.parent                     # .../animal-lab

CONFIG_JSON_PATH = APP_DIR / "config.json"

APP_NAME = "AI Animal Lab"
STUDENT_ID_RE = r"^[A-Za-z0-9_-]{6,64}$"
DEFAULT_CATEGORIES = ["cat", "dog", "rabbit", "bird", "fish"]

STUDENT_FRONTEND_DIR = PROJECT_ROOT / "frontend" / "student"
ADMIN_FRONTEND_DIR = PROJECT_ROOT / "frontend" / "admin"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"env var {name}={raw!r} is not an integer") from None


def ensure_config_file(path: Path = CONFIG_JSON_PATH) -> Path:
    """Create app/config.json with the default categories if it does not exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"categories": list(DEFAULT_CATEGORIES)}, indent=2) + "\n",
            encoding="utf-8",
        )
    return path


def load_categories(path: Path = CONFIG_JSON_PATH) -> list[str]:
    """Load categories from config.json; validate >= 3 unique entries."""
    ensure_config_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}: invalid JSON ({exc})") from None
    cats = raw.get("categories") if isinstance(raw, dict) else None
    if (
        not isinstance(cats, list)
        or len(cats) == 0
        or not all(isinstance(c, str) and c.strip() != "" for c in cats)
    ):
        raise RuntimeError(
            f"{path}: 'categories' must be a list of non-empty strings, e.g. "
            '{"categories": ["cat", "dog", "rabbit", "bird", "fish"]}'
        )
    cats = [c.strip() for c in cats]
    if len(cats) < 3:
        raise RuntimeError(
            f"{path}: at least 3 categories are required for the 3-choice "
            f"scheme, found {len(cats)}: {cats}"
        )
    if len(set(cats)) != len(cats):
        raise RuntimeError(f"{path}: duplicate categories are not allowed: {cats}")
    return cats


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    public_port: int
    admin_port: int
    public_port: int
    admin_port: int
    target_per_student: int
    workshop_goal: int
    max_upload_mb: int
    max_files_per_upload: int
    categories: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "animal_lab.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    target = _env_int("TARGET_PER_STUDENT", 35)
    return Settings(
        data_dir=Path(
            os.environ.get("ANIMAL_LAB_DATA", str(BACKEND_DIR / "data"))
        ).expanduser(),
        public_port=_env_int("PUBLIC_PORT", 8000),
        admin_port=_env_int("ADMIN_PORT", 8001),
        target_per_student=target,
        # Projected-progress goal for the dashboard: total labels aimed for.
        # Set WORKSHOP_GOAL when you know the room; default assumes ~25 labelers.
        workshop_goal=_env_int("WORKSHOP_GOAL", target * 25),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 15),
        max_files_per_upload=_env_int("MAX_FILES_PER_UPLOAD", 60),
        categories=tuple(load_categories()),
    )


settings = load_settings()
