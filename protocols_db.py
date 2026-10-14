"""Генерация протоколов (XLSX/PDF/CSV) напрямую из новой БД.

Прежний generate_protocols.py работал только с CSV-папками competitions/.
Мост удалён, поэтому здесь — компактный генератор поверх db_service:
те же три формата, файлы складываются в config.DOWNLOADS_DIR,
отдача через GET /downloads/<file>.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

import config

logger = logging.getLogger("judo_kata.protocols")


def _rows_for(svc, folder: str, kata: str, stage: str) -> tuple[list[dict], list[dict], dict]:  # type: ignore[no-untyped-def]
    board = svc.build_leaderboard(folder, kata, stage)
    judges = svc.get_effective_judges(folder, kata, stage)
    meta = svc.get_competition_meta(folder)
    from models import JudgeScore, Discipline
    disc = svc.get_discipline(folder, kata)
    matrix: dict[int, dict[int, float]] = {}
    if disc is not None:
        for s in JudgeScore.query.filter_by(discipline_id=disc.id,
                                            stage=stage).all():
            matrix.setdefault(s.pair_number, {})[s.judge_id] = s.total or 0.0
    rows = []
    for r in board:
        rows.append({
            "place": r["place"],
            "pair": f"#{r['pair_number']}",
            "tori": r["tori"].get("name", ""),
            "uke": r["uke"].get("name", ""),
            **{f"judge_{j['position']}":
               matrix.get(r["pair_number"], {}).get(j["id"], "")
               for j in judges},
            "total": r["final_score"],
        })
    return rows, judges, meta


def generate_from_db(svc, folder: str, kata: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Сгенерировать протоколы по всем (или одной) дисциплинам.

    Возвращает {"files": {kata: {"xlsx": url, "pdf": url, "csv": url}}}.
    """
    from models import Competition, Discipline
    comp = Competition.query.filter_by(folder_name=folder).first()
    if comp is None:
        raise ValueError(f"Соревнование {folder} не найдено")
    discs = [d for d in comp.disciplines
             if not kata or d.kata_key == kata]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result: dict[str, dict[str, str]] = {}
    for d in discs:
        stage_cfg = svc.get_stage_cfg(folder, d.kata_key)
        stage = "final" if stage_cfg.get("current_stage") == "final" else "qual"
        rows, judges, meta = _rows_for(svc, folder, d.kata_key, stage)
        base = f"{folder}_{d.kata_key}_{stage}_{stamp}".replace(" ", "_")
        entry: dict[str, str] = {}
        entry["csv"] = _write_csv(config.DOWNLOADS_DIR / f"{base}.csv", rows)
        entry["xlsx"] = _write_xlsx(config.DOWNLOADS_DIR / f"{base}.xlsx",
                                    rows, judges, meta, d, stage)
        entry["pdf"] = _write_pdf(config.DOWNLOADS_DIR / f"{base}.pdf",
                                  rows, judges, meta, d, stage)
        result[d.kata_key] = entry
    logger.info("Протоколы сгенерированы: %s (%d дисциплин)", folder, len(result))
    return {"files": result}


HEADERS = ["Место", "Пара", "Тори", "Уке"]


def _judge_headers(judges: list[dict]) -> list[str]:
    return [f"Судья {j['position']}" for j in judges] + ["Сумма"]


def _row_values(r: dict, judges: list[dict]) -> list:
    vals = [r["place"], r["pair"], r["tori"], r["uke"]]
    vals += [r.get(f"judge_{j['position']}", "") for j in judges]
    vals.append(r["total"])
    return vals


def _write_csv(path, rows: list[dict]) -> str:  # type: ignore[no-untyped-def]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(HEADERS + ["Сумма"])
        for r in rows:
            w.writerow([r["place"], r["pair"], r["tori"], r["uke"], r["total"]])
    return f"/downloads/{path.name}"


def _write_xlsx(path, rows, judges, meta, disc, stage) -> str:  # type: ignore[no-untyped-def]
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = (disc.display_name or disc.kata_key)[:31]
    ws.append([meta.get("title") or config.DEFAULT_COMPETITION_TITLE])
    ws.append([f"{disc.display_name or disc.kata_key} — "
               f"{'Финал' if stage == 'final' else 'Предварительные встречи'}"])
    ws.append([])
    ws.append(HEADERS + _judge_headers(judges))
    for r in rows:
        ws.append(_row_values(r, judges))
    wb.save(path)
    return f"/downloads/{path.name}"


def _write_pdf(path, rows, judges, meta, disc, stage) -> str:  # type: ignore[no-untyped-def]
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4))
    elems = [
        Paragraph(meta.get("title") or config.DEFAULT_COMPETITION_TITLE,
                  styles["Title"]),
        Paragraph(f"{disc.display_name or disc.kata_key} — "
                  f"{'Финал' if stage == 'final' else 'Предварительные встречи'}",
                  styles["Normal"]),
        Spacer(1, 12),
    ]
    headers = HEADERS + _judge_headers(judges)
    data = [headers] + [[str(v) for v in _row_values(r, judges)] for r in rows]
    t = Table(data)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    elems.append(t)
    doc.build(elems)
    return f"/downloads/{path.name}"
