"""Сервисный слой работы с БД (SQLite).

Принципы:
- CSV больше не источник истины: все данные живут в БД, CSV — только импорт.
- Оптимизации: WAL + synchronous=NORMAL + busy_timeout (мало fsync,
  конкурентное чтение при realtime-обновлениях табло с двух компов);
  bulk-загрузка реестра участников в память вместо SELECT на каждую пару;
  diff-upsert пар/оценок вместо DELETE+INSERT всего этапа.
"""
from __future__ import annotations

import logging
import random

from sqlalchemy import event

from models import (
    db, Participant, Judge, Competition, CompetitionMeta, Discipline,
    DisciplinePair, JudgeListEntry, JudgeScore, ScoreItem,
)
from scoring import protocol_from_raw, final_score, rank_pairs

logger = logging.getLogger("judo_kata.db_service")


def normalize_name(name: str) -> str:
    """Нормализация для поиска: Python-нижний регистр корректен для кириллицы
    (в отличие от SQLite LOWER())."""
    return " ".join((name or "").lower().split())


def configure_sqlite_pragmas(engine) -> None:  # type: ignore[no-untyped-def]
    """WAL и дружественные к диску настройки для каждого подключения."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA cache_size=-8000")
        cursor.close()


class DBService:
    """Точка входа для всех операций с данными."""

    # ---------- Реестры (п.1, п.2, п.6) ----------

    def upsert_participant(self, name: str, birth_year: int | None = None,
                           **fields: object) -> Participant:
        """Создать/обновить участника. Правка в одном месте подтягивается
        во все активные соревнования автоматически (через FK)."""
        name = name.strip()
        p = Participant.query.filter_by(name=name, birth_year=birth_year).first()
        if p is None:
            p = Participant(name=name, birth_year=birth_year)
            db.session.add(p)
        p.name_norm = normalize_name(name)
        for key in ("rank", "kyu", "sports_school", "coach"):
            if key in fields and fields[key] not in (None, ""):
                setattr(p, key, str(fields[key]).strip())
        db.session.commit()
        return p

    def update_participant(self, participant_id: int, **fields: object) -> Participant | None:
        p = db.session.get(Participant, participant_id)
        if p is None:
            return None
        for key, val in fields.items():
            if hasattr(p, key) and key != "id":
                setattr(p, key, val)
        p.name_norm = normalize_name(p.name)
        db.session.commit()
        return p

    def search_participants(self, query: str, limit: int = 20) -> list[Participant]:
        """SQLite LOWER()/LIKE не интернациональны (кириллица не регистронезависима),
        поэтому поиск — по нормализованному индексу name_norm (lowercase)."""
        q = (query or "").strip().lower()
        if not q:
            return Participant.query.order_by(Participant.name).limit(limit).all()
        norm = normalize_name(q)
        return (Participant.query
                .filter(Participant.name_norm.like(f"%{norm}%"))
                .order_by(Participant.name)
                .limit(limit).all())

    def upsert_judge(self, name: str, category: str | None = None,
                     region: str | None = None) -> Judge:
        name = name.strip()
        j = Judge.query.filter_by(name=name).first()
        if j is None:
            j = Judge(name=name)
            db.session.add(j)
        if category:
            j.category = category.strip()
        if region:
            j.region = region.strip()
        db.session.commit()
        return j

    def update_judge(self, judge_id: int, **fields: object) -> Judge | None:
        j = db.session.get(Judge, judge_id)
        if j is None:
            return None
        for key, val in fields.items():
            if hasattr(j, key) and key != "id":
                setattr(j, key, val)
        db.session.commit()
        return j

    # ---------- Соревнования и meta (п.3, п.11) ----------

    def get_or_create_competition(self, folder_name: str) -> Competition:
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            comp = Competition(folder_name=folder_name)
            db.session.add(comp)
            db.session.commit()
        return comp

    def _meta_row(self, folder_name: str) -> CompetitionMeta:
        """Гарантированная запись meta (создаёт при отсутствии)."""
        comp = self.get_or_create_competition(folder_name)
        meta = CompetitionMeta.query.filter_by(competition_id=comp.id).first()
        if meta is None:
            meta = CompetitionMeta(competition_id=comp.id)
            db.session.add(meta)
            db.session.commit()
        return meta

    def set_main_tablo(self, folder_name: str, discipline_key: str | None) -> None:
        self._meta_row(folder_name).main_tablo_discipline = discipline_key or None
        db.session.commit()

    def set_comp_status(self, folder_name: str, status: str) -> None:
        self._meta_row(folder_name).status = status
        db.session.commit()

    def set_current_stage(self, folder_name: str, stage: str,
                          top_n: int | None = None) -> None:
        meta = self._meta_row(folder_name)
        meta.current_stage = stage
        if top_n is not None:
            meta.final_top_n = max(1, int(top_n))
        db.session.commit()

    def promote_top_to_final(self, folder_name: str, kata_key: str,
                             top_n: int) -> list[int]:
        """Перевод топ-N пар квалификации в финал (по leaderboard)."""
        board = self.build_leaderboard(folder_name, kata_key, "qual")[:top_n]
        disc = self.get_or_create_discipline(folder_name, kata_key)
        nums = [r["pair_number"] for r in board]
        items = [{"pair_number": n,
                  "tori_name": r["tori"].get("name", ""),
                  "uke_name": r["uke"].get("name", "")}
                 for n, r in zip(nums, board)]
        self.save_pairs(disc.id, DisciplinePair.STAGE_FINAL, items)
        return nums

    def get_all_competitions(self) -> list[dict]:
        """Список соревнований с живыми названиями (п.2/п.11)."""
        out = []
        for comp in Competition.query.order_by(Competition.id).all():
            m = self.get_competition_meta(comp.folder_name)
            out.append({"name": comp.folder_name,
                        "display_name": m.get("title") or comp.folder_name,
                        "status": m.get("status", "open"),
                        "finished": comp.status == Competition.STATUS_FINISHED})
        return out

    def delete_competition(self, folder_name: str) -> bool:
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return False
        db.session.delete(comp)   # cascade: disciplines/pairs/judges/scores
        db.session.commit()
        return True

    def clear_participants(self) -> int:
        n = Participant.query.count()
        Participant.query.delete()
        db.session.commit()
        return n

    def clear_judges(self) -> int:
        n = Judge.query.count()
        Judge.query.delete()
        db.session.commit()
        return n

    def column_suggestions(self, field: str, q: str,
                           limit: int = 20) -> list[str]:
        """Уникальные значения колонки реестра для автодополнения."""
        col = {"sports_school": Participant.sports_school,
               "coach": Participant.coach,
               "rank": Participant.rank}.get(field)
        if col is None:
            return []
        val = (q or "").strip().lower()
        seen: list[str] = []
        for (p,) in Participant.query.with_entities(col).distinct().all():
            if not p:
                continue
            if val and val not in p.lower():
                continue
            seen.append(p)
            if len(seen) >= limit:
                break
        return seen

    def participant_info(self, name: str) -> dict | None:
        """Данные последнего реестра по ФИО (для подтягивания в форму, п.2)."""
        norm = normalize_name(name)
        p = (Participant.query.filter_by(name_norm=norm)
             .order_by(Participant.updated_at.desc()).first())
        return p.to_dict() if p else None

    def set_competition_meta(self, folder_name: str, *, title: str | None = None,
                             date: str | None = None, location: str | None = None,
                             subtitle: str | None = None) -> CompetitionMeta:
        """П.11/п.3: название, дата и субтайтл (возрастная категория табло)
        меняются на горячую — табло читает meta при каждом обновлении."""
        comp = self.get_or_create_competition(folder_name)
        meta = CompetitionMeta.query.filter_by(competition_id=comp.id).first()
        if meta is None:
            meta = CompetitionMeta(competition_id=comp.id)
            db.session.add(meta)
        if title is not None:
            meta.title = title.strip()
        if date is not None:
            meta.date = date.strip()
        if location is not None:
            meta.location = location.strip()
        if subtitle is not None:
            meta.subtitle = subtitle.strip()
        db.session.commit()
        return meta

    def get_competition_meta(self, folder_name: str) -> dict:
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return {}
        meta = CompetitionMeta.query.filter_by(competition_id=comp.id).first()
        if meta is None:
            return {}
        return {
            "title": meta.title, "date": meta.date,
            "location": meta.location, "subtitle": meta.subtitle,
            "main_tablo_discipline": meta.main_tablo_discipline,
            "status": meta.status or "open",
            "current_stage": meta.current_stage or "qual",
            "final_top_n": meta.final_top_n or 3,
        }

    def finish_competition(self, folder_name: str) -> bool:
        """Завершение: замораживает снимки ФИО участников в парах (п.2 —
        в завершённых соревнованиях данные больше не подтягиваются)."""
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return False
        pairs = (DisciplinePair.query.join(Discipline)
                 .filter(Discipline.competition_id == comp.id).all())
        for pr in pairs:
            if pr.tori and not pr.tori_snapshot:
                pr.tori_snapshot = pr.tori.to_dict()
            if pr.uke and not pr.uke_snapshot:
                pr.uke_snapshot = pr.uke.to_dict()
        comp.status = Competition.STATUS_FINISHED
        db.session.commit()
        return True

    # ---------- Дисциплины и пары (п.9) ----------

    def get_or_create_discipline(self, folder_name: str, kata_key: str,
                                 display_name: str | None = None,
                                 techniques_count: int = 10) -> Discipline:
        comp = self.get_or_create_competition(folder_name)
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                          kata_key=kata_key).first()
        if disc is None:
            disc = Discipline(competition_id=comp.id, kata_key=kata_key,
                              display_name=display_name,
                              techniques_count=max(5, min(10, techniques_count)))
            db.session.add(disc)
            db.session.commit()
        return disc

    def save_pairs(self, discipline_id: int, stage: str,
                   pairs: list[dict]) -> None:
        """Diff-upsert пар. Быстрый поиск участников по индексу в памяти
        (одна загрузка реестра вместо N SELECT)."""
        all_parts = Participant.query.all()
        index: dict[tuple[str, int | None], Participant] = {
            (p.name.lower(), p.birth_year): p for p in all_parts
        }
        by_name_only: dict[str, list[Participant]] = {}
        for p in all_parts:
            by_name_only.setdefault(p.name.lower(), []).append(p)

        existing = {pr.pair_number: pr for pr in DisciplinePair.query.filter_by(
            discipline_id=discipline_id, stage=stage).all()}

        seen: set[int] = set()
        for item in pairs:
            num = int(item.get("pair_number", 0) or 0)
            if num <= 0:
                continue
            seen.add(num)
            pair = existing.get(num)
            if pair is None:
                pair = DisciplinePair(discipline_id=discipline_id, stage=stage,
                                      pair_number=num)
                db.session.add(pair)
            for role in ("tori", "uke"):
                pname = (item.get(f"{role}_name") or "").strip()
                pyear = item.get(f"{role}_birth_year")
                year = int(pyear) if pyear not in (None, "", 0, "0") else None
                pid: int | None = None
                if pname:
                    found = index.get((pname.lower(), year))
                    if found is None and year is None:
                        cands = by_name_only.get(pname.lower(), [])
                        if len(cands) == 1:   # коллизия однофамильцев — не угадываем
                            found = cands[0]
                    if found is not None:
                        pid = found.id
                    else:
                        new_p = Participant(name=pname, birth_year=year,
                                            name_norm=normalize_name(pname))
                        db.session.add(new_p)
                        db.session.flush()
                        pid = new_p.id
                        index[(pname.lower(), year)] = new_p
                setattr(pair, f"{role}_id", pid)
                # снимок + доп. поля (разряд/кю/СШ/тренер) — используются
                # табло как detail-строка; обновляются при каждой регистрации
                snap = getattr(pair, f"{role}_snapshot") or {}
                snap.update({
                    "name": pname, "birth_year": year,
                    "rank": item.get(f"{role}_rank") or "",
                    "kyu": item.get(f"{role}_kyu") or "",
                    "sports_school": item.get(f"{role}_school") or "",
                    "coach": item.get(f"{role}_coach") or "",
                })
                setattr(pair, f"{role}_snapshot", snap)
        # удалить пары, которых нет в новом списке
        for num, pair in existing.items():
            if num not in seen:
                db.session.delete(pair)
        db.session.commit()

    def draw_start_order(self, discipline_id: int, stage: str) -> list[tuple[int, int]]:
        """П.8: жеребьёвка порядка выступления пар в категории."""
        pairs = DisciplinePair.query.filter_by(
            discipline_id=discipline_id, stage=stage).all()
        order = list(range(1, len(pairs) + 1))
        random.shuffle(order)
        mapping = dict(zip([p.pair_number for p in pairs], order))
        for p in pairs:
            p.start_order = mapping[p.pair_number]
        db.session.commit()
        return sorted(mapping.items())

    def get_effective_pairs(self, folder_name: str, kata_key: str,
                            stage: str = "qual") -> list[dict]:
        """п.2: живые данные реестра для активного соревнования,
        снимок — для завершённого. Пустые пары тоже возвращаются (п.5)."""
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return []
        frozen = comp.status == Competition.STATUS_FINISHED
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                         kata_key=kata_key).first()
        if disc is None:
            return []
        rows = []
        for pr in (DisciplinePair.query
                   .filter_by(discipline_id=disc.id, stage=stage)
                   .order_by(DisciplinePair.start_order.is_(None),
                             DisciplinePair.start_order,
                             DisciplinePair.pair_number).all()):
            tori = pr.effective_tori(frozen)
            uke = pr.effective_uke(frozen)
            rows.append({
                "pair_number": pr.pair_number,
                "start_order": pr.start_order,
                "tori": tori, "uke": uke,
                "names": f"{tori.get('name', '')} / {uke.get('name', '')}",
            })
        return rows

    # ---------- Судейские списки (п.6) ----------

    def save_judge_list(self, discipline_id: int, stage: str,
                        judges: list) -> None:
        """judges: [{"name"|"ФИО":..., "category":..., "position":...}] или
        просто список имён (str) — upsert судей в реестр + привязка к
        дисциплине. Поддерживает оба формата для совместимости с UI."""
        existing = {e.judge_id: e for e in JudgeListEntry.query.filter_by(
            discipline_id=discipline_id, stage=stage).all()}
        keep: set[int] = set()
        for pos, item in enumerate(judges, start=1):
            if isinstance(item, str):
                item = {"name": item}
            name = (item.get("name") or item.get("ФИО") or "").strip()
            if not name:
                continue
            j = self.upsert_judge(name, category=item.get("category"))
            entry = existing.get(j.id)
            if entry is None:
                entry = JudgeListEntry(discipline_id=discipline_id, stage=stage,
                                       judge_id=j.id, position=pos)
                db.session.add(entry)
            else:
                entry.position = pos
            keep.add(j.id)
        for jid, entry in existing.items():
            if jid not in keep:
                db.session.delete(entry)
        db.session.commit()

    def get_effective_judges(self, folder_name: str, kata_key: str,
                             stage: str = "qual") -> list[dict]:
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return []
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                         kata_key=kata_key).first()
        if disc is None:
            return []
        out = []
        for e in (JudgeListEntry.query
                  .filter_by(discipline_id=disc.id, stage=stage)
                  .order_by(JudgeListEntry.position).all()):
            d = e.judge.to_dict()          # живые данные реестра (п.6)
            d["position"] = e.position
            out.append(d)
        return out

    # ---------- Оценки (п.7, п.9) ----------

    def save_judge_score(self, folder_name: str, kata_key: str, stage: str,
                         judge_name: str, pair_number: int, raw: dict) -> float:
        """Сохранение/обновление протокола судьи (upsert по уникальному
        индексу). Пересчёт итога и score_items. После коммита вызывающий
        код рассылает socketio-событие — табло на других компах
        обновляется в реальном времени без перезагрузки (п.7)."""
        disc = self.get_or_create_discipline(folder_name, kata_key)
        j = self.upsert_judge(judge_name)
        score = JudgeScore.query.filter_by(
            discipline_id=disc.id, stage=stage, judge_id=j.id,
            pair_number=pair_number).first()
        proto = protocol_from_raw(raw, disc.techniques_count)
        total = proto.compute()
        if score is None:
            score = JudgeScore(discipline_id=disc.id, stage=stage, judge_id=j.id,
                               pair_number=pair_number)
            db.session.add(score)
            db.session.flush()
        score.raw_json = raw
        score.total = total
        ScoreItem.query.filter_by(score_id=score.id).delete()
        for t in proto.techniques:
            db.session.add(ScoreItem(
                score_id=score.id, technique_index=t.index, penalty=t.penalty,
                forgotten=t.forgotten, not_performed=t.not_performed,
                computed_score=t.score))
        db.session.commit()
        return total

    def get_pair_totals(self, folder_name: str, kata_key: str,
                        stage: str = "qual") -> dict[int, float]:
        """{pair_number: итог пары по всем судьям} — один запрос,
        используется табло (п.5: пары без оценок остаются с 0)."""
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return {}
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                          kata_key=kata_key).first()
        if disc is None:
            return {}
        scores = JudgeScore.query.filter_by(discipline_id=disc.id,
                                            stage=stage).all()
        per_pair: dict[int, list[float]] = {}
        for s in scores:
            if s.total is not None:
                per_pair.setdefault(s.pair_number, []).append(float(s.total))
        return {pn: final_score(totals) for pn, totals in per_pair.items()}

    def build_leaderboard(self, folder_name: str, kata_key: str,
                          stage: str = "qual") -> list[dict]:
        """Сводная таблица для табло: ВСЕ заявленные пары + места.
        П.5 фикс: пары без единой оценки не исчезают."""
        pairs = self.get_effective_pairs(folder_name, kata_key, stage)
        totals = self.get_pair_totals(folder_name, kata_key, stage)
        rows = [{**p, "final_score": totals.get(p["pair_number"], 0.0)}
                for p in pairs]
        return rank_pairs(rows)

    def list_judge_scores(self, folder_name: str, kata_key: str, stage: str,
                          judge_name: str, pair_number: int) -> list[dict]:
        """Судейские оценки по паре (для табло): [{name, position, total}]."""
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        if comp is None:
            return []
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                          kata_key=kata_key).first()
        if disc is None:
            return []
        scores = (JudgeScore.query
                  .filter_by(discipline_id=disc.id, stage=stage,
                             pair_number=pair_number).all())
        pos_map = {e.judge_id: e.position for e in JudgeListEntry.query.filter_by(
            discipline_id=disc.id, stage=stage).all()}
        out = [{"name": s.judge.name, "position": pos_map.get(s.judge_id, 0),
                "total": float(s.total or 0.0)} for s in scores]
        out.sort(key=lambda x: x["position"])
        return out

    # ---------- Экспорт протоколов (для скачивания из браузера) ----------

    def export_protocol_rows(self, folder_name: str, kata_key: str,
                             stage: str = "qual") -> list[dict]:
        """Строки для CSV/XLSX/PDF экспорта: пара × судья с итогами."""
        board = self.build_leaderboard(folder_name, kata_key, stage)
        judges = self.get_effective_judges(folder_name, kata_key, stage)
        comp = Competition.query.filter_by(folder_name=folder_name).first()
        disc = None
        if comp is not None:
            disc = Discipline.query.filter_by(competition_id=comp.id,
                                              kata_key=kata_key).first()
        matrix: dict[int, dict[str, float]] = {}
        if disc is not None:
            for s in (JudgeScore.query
                      .filter_by(discipline_id=disc.id, stage=stage).all()):
                matrix.setdefault(s.pair_number, {})[s.judge.name] = s.total or 0.0
        rows = []
        for r in board:
            row = {
                "place": r["place"], "pair_number": r["pair_number"],
                "tori": r["tori"].get("name", ""), "uke": r["uke"].get("name", ""),
            }
            for jd in judges:
                row[f"judge_{jd['name']}"] = matrix.get(
                    r["pair_number"], {}).get(jd["name"], "")
            row["final_score"] = r["final_score"]
            rows.append(row)
        return rows
