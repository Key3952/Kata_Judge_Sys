"""Импорт старых данных из CSV в БД (CSV остаётся только для импорта).

Поддерживаемые форматы:
- data/participants.csv: name,birth_year,rank,kyu,sports_school,coach
  (старый формат с полной датой — извлекается год, п.1)
- data/judges.csv: name[,category][,region]
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from models import db, Participant, Judge

logger = logging.getLogger("judo_kata.csv_import")

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def extract_year(value: str | None) -> int | None:
    """Год из '2005', '2005-03-14', '14.03.2005' и т.п."""
    if not value:
        return None
    m = _YEAR_RE.search(str(value))
    return int(m.group(0)) if m else None


def upsert_participant(name: str, birth_year: int | None, **fields: object) -> Participant:
    from db_service import normalize_name

    name = name.strip()
    p = Participant.query.filter_by(name=name, birth_year=birth_year).first()
    if p is None:
        p = Participant(name=name, birth_year=birth_year)
        db.session.add(p)
    p.name_norm = normalize_name(name)
    for key, val in fields.items():
        if val not in (None, "") and hasattr(p, key):
            setattr(p, key, str(val).strip())
    return p


def import_participants_csv(path: Path) -> int:
    """Импортирует реестр участников. Возвращает количество записей."""
    if not path.exists():
        logger.info("CSV не найден, импорт пропущен: %s", path)
        return 0
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or row.get("ФИО") or "").strip()
            if not name:
                continue
            upsert_participant(
                name,
                extract_year(row.get("birth_year") or row.get("birth_date")),
                rank=row.get("rank"),
                kyu=row.get("kyu"),
                sports_school=row.get("sports_school"),
                coach=row.get("coach"),
            )
            count += 1
    db.session.commit()
    logger.info("Импортировано участников: %d", count)
    return count


def import_judges_csv(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or row.get("ФИО") or "").strip()
            if not name:
                continue
            j = db.session.query(Judge).filter_by(name=name).first()
            if j is None:
                j = Judge(name=name)
                db.session.add(j)
            cat = row.get("category") or row.get("judging_category")
            if cat and not j.category:
                j.category = cat.strip()
            if row.get("region") and not j.region:
                j.region = row["region"].strip()
            count += 1
    db.session.commit()
    logger.info("Импортировано судей: %d", count)
    return count
