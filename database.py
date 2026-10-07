# database.py
"""
Модуль работы с базой данных SQLite.
Использует SQLAlchemy для ORM.
"""

import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

db = SQLAlchemy()


def utc_now():
    """Возвращает текущее время в UTC (timezone-aware)"""
    return datetime.now(timezone.utc)


class Participant(db.Model):
    """Модель участника соревнования"""
    __tablename__ = 'participants'
    
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(255), nullable=False, index=True)
    birth_year = db.Column(db.String(10), nullable=False)
    rank = db.Column(db.String(50), default='')  # разряд
    ky = db.Column(db.String(10), default='')  # кю
    school = db.Column(db.String(255), default='')  # СШ
    coach = db.Column(db.String(255), default='')  # тренер
    
    # Уникальность по ФИО + год рождения
    __table_args__ = (
        db.UniqueConstraint('fio', 'birth_year', name='uq_participant_fio_birth'),
    )
    
    def to_dict(self) -> Dict[str, str]:
        return {
            'id': self.id,
            'ФИО': self.fio,
            'год рождения': self.birth_year,
            'разряд': self.rank,
            'кю': self.ky,
            'СШ': self.school,
            'тренер': self.coach,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'Participant':
        return cls(
            fio=data.get('ФИО', '').strip(),
            birth_year=data.get('год рождения', '').strip(),
            rank=data.get('разряд', '').strip(),
            ky=data.get('кю', '').strip(),
            school=data.get('СШ', '').strip(),
            coach=data.get('тренер', '').strip(),
        )


class Judge(db.Model):
    """Модель судьи"""
    __tablename__ = 'judges'
    
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(255), nullable=False, unique=True, index=True)
    
    def to_dict(self) -> Dict[str, str]:
        return {
            'id': self.id,
            'ФИО': self.fio,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'Judge':
        return cls(fio=data.get('ФИО', '').strip())


class Competition(db.Model):
    """Модель соревнования"""
    __tablename__ = 'competitions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    folder_name = db.Column(db.String(255), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    status = db.Column(db.String(50), default='open')  # open, closed
    banner = db.Column(db.String(500), default='')
    
    disciplines = db.relationship('Discipline', backref='competition', cascade='all, delete-orphan')
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'folder_name': self.folder_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'status': self.status,
            'banner': self.banner,
        }


class Discipline(db.Model):
    """Модель дисциплины в рамках соревнования"""
    __tablename__ = 'disciplines'
    
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey('competitions.id'), nullable=False)
    kata_key = db.Column(db.String(100), nullable=False)  # nagenokata, katamenokata, etc.
    stage_config = db.Column(db.Text, default='{}')  # JSON конфиг стадии
    
    __table_args__ = (
        db.UniqueConstraint('competition_id', 'kata_key', name='uq_competition_discipline'),
    )
    
    pairs = db.relationship('Pair', backref='discipline', cascade='all, delete-orphan', lazy='dynamic')
    judge_scores = db.relationship('JudgeScore', backref='discipline', cascade='all, delete-orphan', lazy='dynamic')
    
    def to_dict(self) -> Dict[str, Any]:
        import json
        try:
            config = json.loads(self.stage_config or '{}')
        except json.JSONDecodeError:
            config = {}
        
        return {
            'id': self.id,
            'kata_key': self.kata_key,
            'stage': config,
            'pair_count': self.pairs.count(),
        }


class Pair(db.Model):
    """Модель пары участников (Тори + Уке)"""
    __tablename__ = 'pairs'
    
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(db.Integer, db.ForeignKey('disciplines.id'), nullable=False)
    pair_number = db.Column(db.Integer, nullable=False)
    stage = db.Column(db.String(20), default='prelim')  # prelim, final
    
    # Тори данные
    tori_fio = db.Column(db.String(255), nullable=False)
    tori_birth_year = db.Column(db.String(10), default='')
    tori_rank = db.Column(db.String(50), default='')
    tori_ky = db.Column(db.String(10), default='')
    tori_school = db.Column(db.String(255), default='')
    tori_coach = db.Column(db.String(255), default='')
    
    # Уке данные
    uke_fio = db.Column(db.String(255), nullable=False)
    uke_birth_year = db.Column(db.String(10), default='')
    uke_rank = db.Column(db.String(50), default='')
    uke_ky = db.Column(db.String(10), default='')
    uke_school = db.Column(db.String(255), default='')
    uke_coach = db.Column(db.String(255), default='')
    
    __table_args__ = (
        db.UniqueConstraint('discipline_id', 'pair_number', 'stage', name='uq_pair_discipline_number_stage'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'номер пары': self.pair_number,
            'Тори_ФИО': self.tori_fio,
            'Тори_год рождения': self.tori_birth_year,
            'Тори_разряд': self.tori_rank,
            'Тори_кю': self.tori_ky,
            'Тори_СШ': self.tori_school,
            'Тори_тренер': self.tori_coach,
            'Уке_ФИО': self.uke_fio,
            'Уке_год рождения': self.uke_birth_year,
            'Уке_разряд': self.uke_rank,
            'Уке_кю': self.uke_ky,
            'Уке_СШ': self.uke_school,
            'Уке_тренер': self.uke_coach,
        }
    
    @classmethod
    def from_dict(cls, discipline_id: int, data: Dict[str, str], stage: str = 'prelim') -> 'Pair':
        return cls(
            discipline_id=discipline_id,
            pair_number=int(data.get('номер пары', 0)),
            stage=stage,
            tori_fio=data.get('Тори_ФИО', '').strip(),
            tori_birth_year=data.get('Тори_год рождения', '').strip(),
            tori_rank=data.get('Тори_разряд', '').strip(),
            tori_ky=data.get('Тори_кю', '').strip(),
            tori_school=data.get('Тори_СШ', '').strip(),
            tori_coach=data.get('Тори_тренер', '').strip(),
            uke_fio=data.get('Уке_ФИО', '').strip(),
            uke_birth_year=data.get('Уке_год рождения', '').strip(),
            uke_rank=data.get('Уке_разряд', '').strip(),
            uke_ky=data.get('Уке_кю', '').strip(),
            uke_school=data.get('Уке_СШ', '').strip(),
            uke_coach=data.get('Уке_тренер', '').strip(),
        )


class JudgeList(db.Model):
    """Список судей для дисциплины"""
    __tablename__ = 'judge_lists'
    
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(db.Integer, db.ForeignKey('disciplines.id'), nullable=False)
    position = db.Column(db.Integer, nullable=False)  # 1-5
    judge_fio = db.Column(db.String(255), nullable=False)
    stage = db.Column(db.String(20), default='prelim')
    
    __table_args__ = (
        db.UniqueConstraint('discipline_id', 'position', 'stage', name='uq_judge_discipline_position_stage'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'место': self.position,
            'ФИО': self.judge_fio,
        }


class JudgeScore(db.Model):
    """Оценки судьи для техники"""
    __tablename__ = 'judge_scores'
    
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(db.Integer, db.ForeignKey('disciplines.id'), nullable=False)
    pair_id = db.Column(db.Integer, db.ForeignKey('pairs.id'), nullable=False)
    judge_position = db.Column(db.Integer, nullable=False)  # 1-5
    technique_name = db.Column(db.String(255), nullable=False)
    score = db.Column(db.Float, nullable=False)
    details_json = db.Column(db.Text, default='{}')  # JSON с деталями
    stage = db.Column(db.String(20), default='prelim')
    
    pair = db.relationship('Pair', backref='scores')
    
    __table_args__ = (
        db.UniqueConstraint('discipline_id', 'pair_id', 'judge_position', 'technique_name', 'stage', 
                           name='uq_judge_score_unique'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        import json
        try:
            details = json.loads(self.details_json or '{}')
        except json.JSONDecodeError:
            details = {}
        
        return {
            'техника': self.technique_name,
            'оценка': self.score,
            'details_json': json.dumps(details, ensure_ascii=False),
        }


class FinalProtocol(db.Model):
    """Финальный протокол с суммарными оценками"""
    __tablename__ = 'final_protocols'
    
    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(db.Integer, db.ForeignKey('disciplines.id'), nullable=False)
    pair_id = db.Column(db.Integer, db.ForeignKey('pairs.id'), nullable=False)
    stage = db.Column(db.String(20), default='final')
    
    # Нормализованные данные для протокола
    tori_cell = db.Column(db.String(500), default='')  # ФИО||детали
    uke_cell = db.Column(db.String(500), default='')
    
    # Оценки от судей
    judge_1_score = db.Column(db.Float, default=0.0)
    judge_2_score = db.Column(db.Float, default=0.0)
    judge_3_score = db.Column(db.Float, default=0.0)
    judge_4_score = db.Column(db.Float, default=0.0)
    judge_5_score = db.Column(db.Float, default=0.0)
    
    total_score = db.Column(db.Float, default=0.0)
    place = db.Column(db.Integer, nullable=True)
    
    pair = db.relationship('Pair', backref='protocols')
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'номер пары': self.pair.pair_number if self.pair else 0,
            'Тори': self.tori_cell,
            'Уке': self.uke_cell,
            'Судья 1': self.judge_1_score,
            'Судья 2': self.judge_2_score,
            'Судья 3': self.judge_3_score,
            'Судья 4': self.judge_4_score,
            'Судья 5': self.judge_5_score,
            'Сумма': self.total_score,
            'Место': self.place,
        }


def init_db(app, db_path: str = None):
    """Инициализация базы данных"""
    if db_path is None:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'judo_kata.db')
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'connect_args': {'timeout': 30}
    }
    
    db.init_app(app)
    
    with app.app_context():
        db.create_all()
    
    return db
