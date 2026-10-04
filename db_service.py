"""Слой сервиса БД (SQLite) для системы судейства дзюдо-ката.

CSV/JSON остаются основным рабочим хранилищем (обратная совместимость,
ручное редактирование в Excel). SQLite используется как:
  - глобальный реестр участников и судей (автодополнение, история);
  - архив пар регистрации и оценок судей по соревнованиям;
  - источник данных для вкладок «Участники / Судьи» веб-редактора.

Все методы потокобезопасны (блокировка на уровне сервиса) и не бросают
исключений наружу при сбоях записи — они лишь логируют ошибку, чтобы
основной CSV-поток никогда не зависел от состояния БД.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from models import db, Participant, Judge, Competition, Discipline, PairReg, JudgeScore

logger = logging.getLogger('judo_kata.db')

# Нормализованные заголовки CSV-участника -> поля модели Participant
PARTICIPANT_FIELD_MAP: Dict[str, str] = {
    'ФИО': 'name',
    'год рождения': 'birth_year',
    'разряд': 'rank',
    'кю': 'kyu',
    'СШ': 'sports_school',
    'тренер': 'coach',
}


def _norm_birth(value: Any) -> str:
    """Год рождения: приводим к int или None."""
    s = str(value or '').strip()
    digits = ''.join(ch for ch in s if ch.isdigit())[:4]
    return digits


class DBService:
    """Репозиторий/сервис для работы с SQLite поверх SQLAlchemy."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # ---------- Внутренние утилиты ----------

    @staticmethod
    def _find_participant(name: str, birth_year: Optional[int]) -> Optional[Participant]:
        q = Participant.query.filter(db.func.lower(Participant.name) == name.lower())
        if birth_year is not None:
            p = q.filter(Participant.birth_year == birth_year).first()
            if p is not None:
                return p
        return q.first()

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        try:
            s = str(value or '').strip()
            return int(s) if s else None
        except (TypeError, ValueError):
            return None

    # ---------- Глобальный реестр участников ----------

    def upsert_participant(self, row: Dict[str, Any]) -> None:
        """Добавляет/мягко обновляет участника (ФИО + год рождения).

        Пустые новые значения не затирают уже сохранённые — та же семантика,
        что у CSVManager.upsert_participant.
        """
        name = str(row.get('ФИО', '') or '').strip()
        if not name:
            return
        birth = self._to_int(_norm_birth(row.get('год рождения')))
        try:
            with self._lock:
                p = self._find_participant(name, birth)
                if p is None:
                    p = Participant(name=name, birth_year=birth or 0)
                    db.session.add(p)
                for csv_key, attr in PARTICIPANT_FIELD_MAP.items():
                    if csv_key in ('ФИО', 'год рождения'):
                        continue
                    val = str(row.get(csv_key, '') or '').strip()
                    if val:  # мягкое обновление: пустое не затираем
                        setattr(p, attr, val)
                if birth is not None and p.birth_year in (None, 0):
                    p.birth_year = birth
                db.session.commit()
        except Exception as exc:  # noqa: BLE001 - БД не должна ронять рабочий поток
            db.session.rollback()
            logger.warning("DB upsert_participant failed for %r: %s", name, exc)

    def sync_participants_from_csv(self, rows: List[Dict[str, Any]]) -> int:
        """Полная синхронизация реестра участников из participants.csv.

        Возвращает количество записей в реестре после синхронизации.
        """
        try:
            with self._lock:
                seen_ids: set = set()
                for row in rows:
                    name = str(row.get('ФИО', '') or '').strip()
                    if not name:
                        continue
                    birth = self._to_int(_norm_birth(row.get('год рождения')))
                    p = self._find_participant(name, birth)
                    if p is None:
                        p = Participant(name=name, birth_year=birth or 0)
                        db.session.add(p)
                        db.session.flush()
                    for csv_key, attr in PARTICIPANT_FIELD_MAP.items():
                        if csv_key == 'ФИО':
                            continue
                        val = str(row.get(csv_key, '') or '').strip()
                        if csv_key == 'год рождения':
                            if val and p.birth_year in (None, 0):
                                p.birth_year = self._to_int(val) or 0
                        elif val:
                            setattr(p, attr, val)
                    seen_ids.add(p.id)
                # Удаляем то, чего больше нет в CSV (CSV — источник истины)
                for p in Participant.query.all():
                    if p.id not in seen_ids:
                        db.session.delete(p)
                db.session.commit()
                return Participant.query.count()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB sync_participants_from_csv failed: %s", exc)
            return -1

    def delete_participant_by_id(self, pid: int) -> bool:
        try:
            with self._lock:
                p = db.session.get(Participant, int(pid))
                if p is None:
                    return False
                db.session.delete(p)
                db.session.commit()
                return True
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB delete_participant failed: %s", exc)
            return False

    # ---------- Глобальный реестр судей ----------

    def add_judge(self, name: str) -> None:
        name = str(name or '').strip()
        if not name:
            return
        try:
            with self._lock:
                exists = Judge.query.filter(
                    db.func.lower(Judge.name) == name.lower()
                ).first()
                if exists is None:
                    db.session.add(Judge(name=name))
                    db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB add_judge failed for %r: %s", name, exc)

    def sync_judges_from_csv(self, rows: List[Dict[str, Any]]) -> int:
        try:
            with self._lock:
                names = {
                    str(r.get('ФИО', '') or '').strip()
                    for r in rows if str(r.get('ФИО', '') or '').strip()
                }
                existing = {j.name.strip().lower(): j for j in Judge.query.all()}
                for n in names:
                    if n.lower() not in existing:
                        db.session.add(Judge(name=n))
                for lname, j in existing.items():
                    if j.name.strip() not in names:
                        db.session.delete(j)
                db.session.commit()
                return Judge.query.count()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB sync_judges_from_csv failed: %s", exc)
            return -1

    def delete_judge_by_id(self, jid: int) -> bool:
        try:
            with self._lock:
                j = db.session.get(Judge, int(jid))
                if j is None:
                    return False
                db.session.delete(j)
                db.session.commit()
                return True
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB delete_judge failed: %s", exc)
            return False

    # ---------- Соревнования / дисциплины ----------

    def register_competition(self, folder_name: str, display_name: str) -> None:
        try:
            with self._lock:
                comp = Competition.query.filter_by(folder_name=folder_name).first()
                if comp is None:
                    comp = Competition(folder_name=folder_name, name=display_name)
                    db.session.add(comp)
                comp.display_name = display_name
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB register_competition failed: %s", exc)

    def set_competition_status(self, folder_name: str, status: str) -> None:
        try:
            with self._lock:
                comp = Competition.query.filter_by(folder_name=folder_name).first()
                if comp is not None:
                    comp.status = status
                    db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB set_competition_status failed: %s", exc)

    def remove_competition(self, folder_name: str) -> None:
        """Каскадно удаляет соревнование со всеми дисциплинами/парами/оценками."""
        try:
            with self._lock:
                comp = Competition.query.filter_by(folder_name=folder_name).first()
                if comp is None:
                    return
                for disc in comp.disciplines:
                    PairReg.query.filter_by(discipline_id=disc.id).delete()
                    JudgeScore.query.filter_by(discipline_id=disc.id).delete()
                    db.session.delete(disc)
                db.session.delete(comp)
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB remove_competition failed: %s", exc)

    def register_discipline(self, comp_folder: str, kata_key: str,
                            kata_name: str = '') -> None:
        try:
            with self._lock:
                comp = Competition.query.filter_by(folder_name=comp_folder).first()
                if comp is None:
                    comp = Competition(folder_name=comp_folder, name=comp_folder)
                    db.session.add(comp)
                    db.session.flush()
                disc = Discipline.query.filter_by(
                    competition_id=comp.id, kata_key=kata_key
                ).first()
                if disc is None:
                    disc = Discipline(competition_id=comp.id, kata_key=kata_key,
                                      name=kata_name or kata_key)
                    db.session.add(disc)
                disc.name = kata_name or disc.name
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB register_discipline failed: %s", exc)

    def remove_discipline(self, comp_folder: str, kata_key: str) -> None:
        try:
            with self._lock:
                comp = Competition.query.filter_by(folder_name=comp_folder).first()
                if comp is None:
                    return
                disc = Discipline.query.filter_by(
                    competition_id=comp.id, kata_key=kata_key
                ).first()
                if disc is None:
                    return
                PairReg.query.filter_by(discipline_id=disc.id).delete()
                JudgeScore.query.filter_by(discipline_id=disc.id).delete()
                db.session.delete(disc)
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB remove_discipline failed: %s", exc)

    def _get_discipline(self, comp_folder: str, kata_key: str) -> Optional[Discipline]:
        comp = Competition.query.filter_by(folder_name=comp_folder).first()
        if comp is None:
            return None
        return Discipline.query.filter_by(competition_id=comp.id,
                                          kata_key=kata_key).first()

    # ---------- Пары регистрации ----------

    def save_pairs(self, comp_folder: str, kata_key: str, stage: str,
                   pairs: List[Dict[str, Any]]) -> None:
        """Архивирует список пар дисциплины/этапа (полная перезапись)."""
        try:
            with self._lock:
                disc = self._get_discipline(comp_folder, kata_key)
                if disc is None:
                    return
                PairReg.query.filter_by(discipline_id=disc.id, stage=stage).delete()
                for row in pairs:
                    db.session.add(PairReg(
                        discipline_id=disc.id,
                        stage=stage,
                        pair_number=self._to_int(row.get('номер пары')) or 0,
                        tori_name=str(row.get('Тори_ФИО', '') or ''),
                        uke_name=str(row.get('Уке_ФИО', '') or ''),
                        data_json=dict(row),
                    ))
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB save_pairs failed: %s", exc)

    # ---------- Оценки судей ----------

    def save_judge_score(self, comp_folder: str, kata_key: str, stage: str,
                         judge_name: str, judge_position: int, pair_number: int,
                         scores: List[float], details: List[Dict[str, Any]],
                         total: Any) -> None:
        """Сохраняет/обновляет протокол оценок одного судьи по одной паре."""
        try:
            with self._lock:
                disc = self._get_discipline(comp_folder, kata_key)
                if disc is None:
                    return
                rec = JudgeScore.query.filter_by(
                    discipline_id=disc.id, stage=stage,
                    judge_name=judge_name, pair_number=pair_number,
                ).first()
                if rec is None:
                    rec = JudgeScore(discipline_id=disc.id, stage=stage,
                                     judge_name=judge_name,
                                     pair_number=pair_number)
                    db.session.add(rec)
                rec.judge_position = judge_position
                rec.scores_json = list(scores)
                rec.details_json = list(details)
                try:
                    rec.total = float(total) if total not in (None, '') else None
                except (TypeError, ValueError):
                    rec.total = None
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("DB save_judge_score failed: %s", exc)

    def get_judge_details(self, comp_folder: str, kata_key: str,
                          judge_name: str, pos: int, tori: str,
                          uke: str) -> Optional[Dict[str, Any]]:
        """Возвращает {техника: detail} из БД или None, если записи нет."""
        try:
            with self._lock:
                disc = self._get_discipline(comp_folder, kata_key)
                if disc is None:
                    return None
                rec = JudgeScore.query.filter_by(
                    discipline_id=disc.id, judge_name=judge_name,
                    judge_position=pos,
                ).first()
                if rec is None or not rec.details_json:
                    return None
                from technics import DISCIPLINE_ROWS_BY_KEY
                techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
                details = list(rec.details_json)
                return {tech: details[i] for i, tech in enumerate(techniques)
                        if i < len(details)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB get_judge_details failed: %s", exc)
            return None


db_service = DBService()
