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
    """Глобальный реестр участников (основная база спортсменов).

    Пары регистрации ссылаются на участников по participant_id, поэтому
    изменение данных в реестре автоматически подтягивается везде, кроме
    завершённых (закрытых) соревнований — там хранится снимок (snapshot).
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    birth_year = db.Column(db.Integer, nullable=False, default=0)  # только год
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
    """Глобальный реестр судей (основная база судей)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    category = db.Column(db.String(50), default='')  # судейская категория

    def to_csv_row(self) -> dict:
        return {'ФИО': self.name or '', 'категория': self.category or ''}


class Competition(db.Model):
    """Реестр соревнований (папки в competitions/)."""
    id = db.Column(db.Integer, primary_key=True)
    folder_name = db.Column(db.String(200), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False, default='')
    display_name = db.Column(db.String(200), default='')
    subtitle = db.Column(db.String(200), default='')  # возрастная категория для табло
    event_date = db.Column(db.Date, nullable=True)    # дата проведения
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
    """Пары регистрации по дисциплине и этапу (prelim/final).

    tori_id/uke_id — ссылки на глобальный реестр участников (живые данные);
    *_snapshot — снимок на момент регистрации, используется для закрытых
    (завершённых) соревнований.
    """
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(
        db.Integer, db.ForeignKey('discipline.id'), nullable=False, index=True,
    )
    stage = db.Column(db.String(10), nullable=False, default='prelim')
    pair_number = db.Column(db.Integer, nullable=False, default=0)
    tori_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=True)
    uke_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=True)
    tori_name = db.Column(db.String(100), default='')
    uke_name = db.Column(db.String(100), default='')
    tori_snapshot = db.Column(SafeUnicodeJSON, nullable=True)
    uke_snapshot = db.Column(SafeUnicodeJSON, nullable=True)
    data_json = db.Column(SafeUnicodeJSON, nullable=False, default=dict)

    discipline = db.relationship('Discipline')
    tori = db.relationship('Participant', foreign_keys=[tori_id])
    uke = db.relationship('Participant', foreign_keys=[uke_id])


class JudgeListEntry(db.Model):
    """Состав судей дисциплины: ссылка на реестр судей + позиция."""
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(
        db.Integer, db.ForeignKey('discipline.id'), nullable=False, index=True,
    )
    position = db.Column(db.Integer, nullable=False, default=0)
    judge_id = db.Column(db.Integer, db.ForeignKey('judge.id'), nullable=True)
    judge_name = db.Column(db.String(100), nullable=False, default='')

    discipline = db.relationship('Discipline')
    judge = db.relationship('Judge')

    __table_args__ = (
        db.UniqueConstraint('discipline_id', 'position',
                            name='uq_judgelist_disc_pos'),
    )


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

    __table_args__ = (
        db.Index('ix_judgescore_lookup', 'discipline_id', 'stage',
                 'judge_name', 'pair_number'),
    )


def configure_sqlite_pragmas(engine) -> None:
    """WAL + нормальная синхронизация: меньше fsync — бережём диск."""
    from sqlalchemy import event

    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
