"""Модели SQLAlchemy. Единая нормализованная схема БД (CSV — только импорт).

Ключевые связи:
- Participant / Judge — глобальные реестры (источник истины для персон).
- CompetitionMeta — название/дата/субтайтл соревнования «на горячую».
- DisciplinePair ссылается на Participant через FK (name/year — снимок
  только для завершённых соревнований, см. spec п.2).
- JudgeScore ссылается на Judge через FK + содержит raw_json и
  score_items (разбор оценок по техникам).
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, Index
from sqlalchemy.types import TypeDecorator, TEXT

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SafeUnicodeJSON(TypeDecorator):
    """JSON как текст: совместимо со старыми данными и не требует JSONB."""

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        import json

        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        import json

        if value is None:
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None


class Participant(db.Model):
    """Глобальный реестр спортсменов (п.1: хранится только год рождения)."""

    __tablename__ = "participant"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(200), nullable=False, index=True)
    # нормализованная (нижний регистр) копия ФИО — для поиска по кириллице,
    # т.к. SQLite LOWER()/LIKE не интернациональны
    name_norm: str = db.Column(db.String(200), default="", index=True)
    birth_year: int | None = db.Column(db.Integer)  # только год, без даты
    rank: str | None = db.Column(db.String(50))     # разряд/звание
    kyu: str | None = db.Column(db.String(20))
    sports_school: str | None = db.Column(db.String(200))
    coach: str | None = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("name", "birth_year", name="uq_participant_name_year"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "birth_year": self.birth_year,
            "rank": self.rank,
            "kyu": self.kyu,
            "sports_school": self.sports_school,
            "coach": self.coach,
        }


class Judge(db.Model):
    """Глобальный реестр судей (п.6: + колонка судейской категории)."""

    __tablename__ = "judge"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(200), unique=True, nullable=False, index=True)
    category: str | None = db.Column(db.String(50))  # судейская категория
    region: str | None = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "region": self.region,
        }


class Competition(db.Model):
    """Соревнование: папка-идентификатор + статус жизненного цикла."""

    __tablename__ = "competition"

    STATUS_ACTIVE = "active"
    STATUS_FINISHED = "finished"

    id: int = db.Column(db.Integer, primary_key=True)
    folder_name: str = db.Column(db.String(200), unique=True, nullable=False)
    status: str = db.Column(db.String(20), default=STATUS_ACTIVE)
    created_at = db.Column(db.DateTime, default=utcnow)
    disciplines = db.relationship(
        "Discipline", backref="competition", cascade="all, delete-orphan"
    )


class CompetitionMeta(db.Model):
    """П.11: название/дата/subtitle меняются на горячую, одна запись на комп."""

    __tablename__ = "competition_meta"

    id: int = db.Column(db.Integer, primary_key=True)
    competition_id: int = db.Column(
        db.ForeignKey("competition.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    title: str | None = db.Column(db.String(300))
    date: str | None = db.Column(db.String(30))       # ISO-строка 'YYYY-MM-DD'
    location: str | None = db.Column(db.String(200))
    subtitle: str | None = db.Column(db.String(300))  # п.3: возрастная категория
    main_tablo_discipline: str | None = db.Column(db.String(50))  # дисциплина главного табло
    status: str = db.Column(db.String(20), default="open")        # open/close (UI-переключатель)
    current_stage: str = db.Column(db.String(10), default="qual")  # qual|final — активный этап
    final_top_n: int = db.Column(db.Integer, default=3)            # сколько пар переводить в финал
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    competition = db.relationship("Competition", backref="meta")


class Discipline(db.Model):
    """Ката/дисциплина внутри соревнования."""

    __tablename__ = "discipline"

    id: int = db.Column(db.Integer, primary_key=True)
    competition_id: int = db.Column(
        db.ForeignKey("competition.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kata_key: str = db.Column(db.String(50), nullable=False)
    display_name: str | None = db.Column(db.String(200))
    # п.9: количество оцениваемых техник (по умолчанию 10, диапазон 5..10)
    techniques_count: int = db.Column(db.Integer, default=10, nullable=False)
    __table_args__ = (
        UniqueConstraint("competition_id", "kata_key", name="uq_disc_comp_kata"),
    )


class DisciplinePair(db.Model):
    """Пара в дисциплине. Ссылка на реестр участников через FK (п.2)."""

    __tablename__ = "discipline_pair"

    STAGE_QUAL = "qual"
    STAGE_FINAL = "final"

    id: int = db.Column(db.Integer, primary_key=True)
    discipline_id: int = db.Column(
        db.ForeignKey("discipline.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: str = db.Column(db.String(10), default=STAGE_QUAL, nullable=False)
    pair_number: int = db.Column(db.Integer, nullable=False)
    start_order: int | None = db.Column(db.Integer)  # п.8: результат жеребьёвки
    tori_id: int | None = db.Column(db.ForeignKey("participant.id"))
    uke_id: int | None = db.Column(db.ForeignKey("participant.id"))
    # Снимок ФИО/года: замораживается при переходе соревнования в finished
    tori_snapshot = db.Column(SafeUnicodeJSON)
    uke_snapshot = db.Column(SafeUnicodeJSON)

    tori = db.relationship("Participant", foreign_keys=[tori_id])
    uke = db.relationship("Participant", foreign_keys=[uke_id])

    __table_args__ = (
        UniqueConstraint("discipline_id", "stage", "pair_number",
                         name="uq_pair_disc_stage_num"),
    )

    def effective_tori(self, frozen: bool) -> dict:
        """п.2: активное соревнование — живые данные реестра;
        завершённое — снимок на момент завершения."""
        if frozen and self.tori_snapshot:
            return dict(self.tori_snapshot)
        if self.tori:
            return self.tori.to_dict()
        return dict(self.tori_snapshot or {"name": ""})

    def effective_uke(self, frozen: bool) -> dict:
        if frozen and self.uke_snapshot:
            return dict(self.uke_snapshot)
        if self.uke:
            return self.uke.to_dict()
        return dict(self.uke_snapshot or {"name": ""})


class JudgeListEntry(db.Model):
    """Состав судей дисциплины/этапа с позициями."""

    __tablename__ = "judge_list_entry"

    id: int = db.Column(db.Integer, primary_key=True)
    discipline_id: int = db.Column(
        db.ForeignKey("discipline.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: str = db.Column(db.String(10), default="qual", nullable=False)
    judge_id: int = db.Column(db.ForeignKey("judge.id"), nullable=False)
    position: int = db.Column(db.Integer, default=1)
    judge = db.relationship("Judge")

    __table_args__ = (
        UniqueConstraint("discipline_id", "stage", "judge_id",
                         name="uq_judgelist_disc_stage_judge"),
    )


class JudgeScore(db.Model):
    """Протокол судьи по паре. Оценка за технику — отдельной строкой
    (score_item) для корректного учёта пропусков (п.9) и аналитики."""

    __tablename__ = "judge_score"

    id: int = db.Column(db.Integer, primary_key=True)
    discipline_id: int = db.Column(
        db.ForeignKey("discipline.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: str = db.Column(db.String(10), default="qual", nullable=False)
    judge_id: int = db.Column(db.ForeignKey("judge.id"), nullable=False)
    pair_number: int = db.Column(db.Integer, nullable=False)
    total: float | None = db.Column(db.Float)          # итог судьи по паре
    raw_json = db.Column(SafeUnicodeJSON)              # сырые данные формы
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    judge = db.relationship("Judge")
    items = db.relationship(
        "ScoreItem", backref="score", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("discipline_id", "stage", "judge_id", "pair_number",
                         name="uq_score_disc_stage_judge_pair"),
        Index("ix_score_lookup", "discipline_id", "stage", "pair_number"),
    )


class ScoreItem(db.Model):
    """Одна техника в протоколе: penalty/bonus из UI, computed_score —
    пересчитанный балл (10 - штрафы, 0 если забыта, None если не выполняется)."""

    __tablename__ = "score_item"

    id: int = db.Column(db.Integer, primary_key=True)
    score_id: int = db.Column(
        db.ForeignKey("judge_score.id", ondelete="CASCADE"), nullable=False, index=True
    )
    technique_index: int = db.Column(db.Integer, nullable=False)
    penalty: float = db.Column(db.Float, default=0.0, nullable=False)
    bonus: float = db.Column(db.Float, default=0.0, nullable=False)
    forgotten: bool = db.Column(db.Boolean, default=False, nullable=False)
    not_performed: bool = db.Column(db.Boolean, default=False, nullable=False)
    computed_score: float | None = db.Column(db.Float)

    __table_args__ = (
        UniqueConstraint("score_id", "technique_index", name="uq_item_score_idx"),
    )
