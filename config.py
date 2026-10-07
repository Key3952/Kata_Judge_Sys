"""Конфигурация приложения: пути, настройки БД, параметры по умолчанию."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
COMPETITIONS_DIR = DATA_DIR / "competitions"
INSTANCE_DIR = BASE_DIR / "instance"
DOWNLOADS_DIR = BASE_DIR / "downloads"

PARTICIPANTS_CSV = DATA_DIR / "participants.csv"
JUDGES_CSV = DATA_DIR / "judges.csv"
DB_PATH = INSTANCE_DIR / "data.db"

SQLALCHEMY_DATABASE_URI = os.environ.get(
    "DATABASE_URL", f"sqlite:///{DB_PATH}"
)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# Название соревнования по умолчанию (используется до установки meta в БД)
DEFAULT_COMPETITION_TITLE = os.environ.get(
    "COMPETITION_TITLE", "Соревнования по дзюдо ката"
)

for _d in (DATA_DIR, COMPETITIONS_DIR, INSTANCE_DIR, DOWNLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
