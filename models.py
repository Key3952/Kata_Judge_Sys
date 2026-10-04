"""Модели SQLAlchemy для SQLite-реестра системы судейства.

CSV остаётся основным рабочим хранилищем; БД хранит глобальный реестр
участников/судей и архив соревнований (пары, оценки судей).
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import JSON, TypeDecorator


class SafeUnicodeJSON(TypeDecorator):
    """JSON-колонка с гарантией unicode: sqlite3 может вернуть bytes."""

    impl = JSON
    cache_ok = True

    def process_result_value(self, value, dialect):
        if isinstance(value, (bytes, bytearray)):
            import json as _json
            try:
                return _json.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return None
        return value

db = SQLAlchemy()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Participant(db.Model):
    """Глобальный реестр участников (зеркало participants.csv)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    birth_year = db.Column(db.Integer, nullable=False, default=0)
    rank = db.Column(db.String(50))
    kyu = db.Column(db.String(50))
    sports_school = db.Column(db.String(100))
    coach = db.Column(db.String(100))
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    def to_csv_row(self) -> dict:
        return {
            'ФИО': self.name or '',
            'год рождения': str(self.birth_year or ''),
            'разряд': self.rank or '',
            'кю': self.kyu or '',
            'СШ': self.sports_school or '',
            'тренер': self.coach or '',
        }


class Judge(db.Model):
    """Глобальный реестр судей (зеркало judges.csv)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)

    def to_csv_row(self) -> dict:
        return {'ФИО': self.name or ''}


class Competition(db.Model):
    """Реестр соревнований (папки в competitions/)."""
    id = db.Column(db.Integer, primary_key=True)
    folder_name = db.Column(db.String(200), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False, default='')
    display_name = db.Column(db.String(200), default='')
    status = db.Column(db.String(20), default='open')  # open/closed
    created_at = db.Column(db.DateTime, default=_utcnow)

    disciplines = db.relationship(
        'Discipline', backref='competition',
        cascade='all, delete-orphan', lazy=True,
    )


class Discipline(db.Model):
    """Дисциплина (ката) внутри соревнования."""
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(
        db.Integer, db.ForeignKey('competition.id'), nullable=False, index=True,
    )
    kata_key = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), default='')

    __table_args__ = (
        db.UniqueConstraint('competition_id', 'kata_key',
                            name='uq_discipline_comp_kata'),
    )


class PairReg(db.Model):
    """Архив пар регистрации по дисциплине и этапу (prelim/final)."""
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(
        db.Integer, db.ForeignKey('discipline.id'), nullable=False, index=True,
    )
    stage = db.Column(db.String(10), nullable=False, default='prelim')
    pair_number = db.Column(db.Integer, nullable=False, default=0)
    tori_name = db.Column(db.String(100), default='')
    uke_name = db.Column(db.String(100), default='')
    data_json = db.Column(SafeUnicodeJSON, nullable=False, default=dict)

    discipline = db.relationship('Discipline')


class JudgeScore(db.Model):
    """Протокол оценок одного судьи по одной паре (архив)."""
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(
        db.Integer, db.ForeignKey('discipline.id'), nullable=False, index=True,
    )
    stage = db.Column(db.String(10), nullable=False, default='prelim')
    judge_name = db.Column(db.String(100), nullable=False)
    judge_position = db.Column(db.Integer, nullable=False, default=1)
    pair_number = db.Column(db.Integer, nullable=False, default=0)
    scores_json = db.Column(SafeUnicodeJSON, nullable=False, default=list)   # баллы по техникам
    details_json = db.Column(SafeUnicodeJSON, nullable=False, default=list)  # штрафы по техникам
    total = db.Column(db.Float)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    discipline = db.relationship('Discipline')
