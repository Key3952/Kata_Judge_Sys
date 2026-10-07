"""Система судейства дзюдо-ката. БД (SQLite/WAL) — единственный источник
данных; CSV оставлен только для импорта старых баз и экспорта протоколов.

Realtime (п.7): SocketIO room 'tablo:<comp>:<kata>:<stage>' — табло на любом
числе компьютеров обновляется при сохранении оценки без F5.

HTML-шаблоны взяты из репозитория; все url_for-эндпоинты, fetch-URL и поля
шаблонов реализованы здесь поверх БД-сервиса.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sys
from pathlib import Path

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_socketio import SocketIO

import config
import technics
from db_service import DBService, configure_sqlite_pragmas
from models import (db, Participant, Judge, Discipline, Competition,
                    DisciplinePair)
from csv_import import import_participants_csv, import_judges_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger("judo_kata")

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = config.SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

svc = DBService()
socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")


# ---------- helpers ----------

def require_admin(fn):  # type: ignore[no-untyped-def]
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def current_competition() -> str:
    return request.args.get("competition") or session.get("competition") or "default"


# ---------- auth ----------

@app.post("/api/login")
def api_login() -> Response:
    data = request.get_json(silent=True) or {}
    if data.get("password") == config.ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


@app.post("/api/logout")
def api_logout() -> Response:
    session.clear()
    return jsonify({"ok": True})


# ---------- участники (п.1, п.2) ----------

@app.get("/api/participants")
def api_participants() -> Response:
    q = request.args.get("q", "")
    items = svc.search_participants(q) if q else Participant.query.order_by(
        Participant.name).limit(500).all()
    return jsonify([p.to_dict() for p in items])


@app.post("/api/participants")
@require_admin
def api_add_participant() -> Response:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    year = data.get("birth_year")
    p = svc.upsert_participant(name, int(year) if year else None,
                               rank=data.get("rank"), kyu=data.get("kyu"),
                               sports_school=data.get("sports_school"),
                               coach=data.get("coach"))
    return jsonify(p.to_dict()), 201


@app.put("/api/participants/<int:pid>")
@require_admin
def api_update_participant(pid: int) -> Response:
    """П.2: правка в реестре автоматически видна во всех активных соревнованиях."""
    fields = {k: v for k, v in (request.get_json(silent=True) or {}).items()
              if k in ("name", "birth_year", "rank", "kyu", "sports_school", "coach")}
    p = svc.update_participant(pid, **fields)
    if p is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(p.to_dict())


@app.post("/api/import/participants-csv")
@require_admin
def api_import_csv() -> Response:
    """Импорт старой базы участников из CSV (единственный сценарий CSV)."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400
    tmp = config.DATA_DIR / "_import_participants.csv"
    f.save(tmp)
    with app.app_context():
        n = import_participants_csv(tmp)
    tmp.unlink(missing_ok=True)
    return jsonify({"imported": n})


# ---------- судьи (п.6) ----------

@app.get("/api/judges")
def api_judges() -> Response:
    return jsonify([j.to_dict() for j in Judge.query.order_by(Judge.name).all()])


@app.post("/api/judges")
@require_admin
def api_add_judge() -> Response:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    j = svc.upsert_judge(name, category=data.get("category"), region=data.get("region"))
    return jsonify(j.to_dict()), 201


@app.put("/api/judges/<int:jid>")
@require_admin
def api_update_judge(jid: int) -> Response:
    fields = {k: v for k, v in (request.get_json(silent=True) or {}).items()
              if k in ("name", "category", "region")}
    j = svc.update_judge(jid, **fields)
    if j is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(j.to_dict())


# ---------- соревнование: meta на горячую (п.3, п.11) ----------

@app.get("/api/competition/meta")
def api_get_meta() -> Response:
    comp = current_competition()
    meta = svc.get_competition_meta(comp)
    meta.setdefault("title", config.DEFAULT_COMPETITION_TITLE)
    return jsonify(meta)


@app.post("/api/competition/meta")
@require_admin
def api_set_meta() -> Response:
    comp = current_competition()
    data = request.get_json(silent=True) or {}
    svc.set_competition_meta(
        comp, title=data.get("title"), date=data.get("date"),
        location=data.get("location"), subtitle=data.get("subtitle"))
    socketio.emit("meta_updated", {"competition": comp},
                  room=f"tablo:{comp}")
    return jsonify({"ok": True})


@app.post("/api/competition/finish")
@require_admin
def api_finish() -> Response:
    """Заморозка данных: дальнейшие правки реестра не влияют на комп."""
    ok = svc.finish_competition(current_competition())
    return jsonify({"ok": ok}), (200 if ok else 404)


# ---------- пары и жеребьёвка (п.8) ----------

@app.get("/api/pairs")
def api_pairs() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    stage = request.args.get("stage", "qual")
    return jsonify(svc.get_effective_pairs(comp, kata, stage))


@app.post("/api/pairs")
@require_admin
def api_save_pairs() -> Response:
    if not request.is_json:
        return jsonify({"error": "json required"}), 415
    comp, kata = current_competition(), _norm_kata(request.json.get("kata", ""))
    stage = request.json.get("stage", "qual")
    disc = svc.get_or_create_discipline(comp, kata,
                                        techniques_count=request.json.get(
                                            "techniques_count", 10))
    svc.save_pairs(disc.id, stage, request.json.get("pairs", []))
    socketio.emit("pairs_updated", {"kata": kata, "stage": stage},
                  room=f"tablo:{comp}:{kata}:{stage}")
    return jsonify({"ok": True})


@app.post("/api/pairs/draw")
@require_admin
def api_draw() -> Response:
    """П.8: жеребьёвка порядка выступления."""
    comp, kata = current_competition(), _norm_kata(request.json.get("kata", ""))
    stage = request.json.get("stage", "qual")
    disc = svc.get_or_create_discipline(comp, kata)
    order = svc.draw_start_order(disc.id, stage)
    socketio.emit("pairs_updated", {"kata": kata, "stage": stage},
                  room=f"tablo:{comp}:{kata}:{stage}")
    return jsonify({"order": order})


# ---------- судейские списки ----------

@app.get("/api/judge-list")
def api_judge_list() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    return jsonify(svc.get_effective_judges(comp, kata,
                                            request.args.get("stage", "qual")))


@app.post("/api/judge-list")
@require_admin
def api_save_judge_list() -> Response:
    data = request.get_json(silent=True) or {}
    comp, kata = current_competition(), data.get("kata", "")
    stage = data.get("stage", "qual")
    disc = svc.get_or_create_discipline(comp, kata)
    svc.save_judge_list(disc.id, stage, data.get("judges", []))
    return jsonify({"ok": True})


# ---------- оценки + realtime (п.5, п.7, п.9) ----------

@app.post("/api/score")
def api_save_score() -> Response:
    """Сохранение оценки судьи; пуш leaderboard всем подключённым табло."""
    data = request.get_json(silent=True) or {}
    comp = data.get("competition") or current_competition()
    kata = _norm_kata(data.get("kata", ""))
    stage = data.get("stage", "qual")
    judge_name = (data.get("judge_name") or "").strip()
    pair_number = int(data.get("pair_number", 0) or 0)
    if not judge_name or pair_number <= 0 or not kata:
        return jsonify({"error": "judge_name, pair_number, kata required"}), 400
    total = svc.save_judge_score(comp, kata, stage, judge_name, pair_number,
                                 data.get("techniques_raw", {}))
    board = svc.build_leaderboard(comp, kata, stage)
    socketio.emit("leaderboard", {"rows": board},
                  room=f"tablo:{comp}:{kata}:{stage}")
    return jsonify({"ok": True, "total": total})


@app.get("/api/leaderboard")
def api_leaderboard() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    stage = request.args.get("stage", "qual")
    return jsonify({
        "rows": svc.build_leaderboard(comp, kata, stage),
        "meta": svc.get_competition_meta(comp),
    })


# ---------- экспорт протоколов (скачивание из браузера) ----------

@app.get("/api/export/protocol.csv")
def api_export_csv() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    rows = svc.export_protocol_rows(comp, kata, request.args.get("stage", "qual"))
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                                delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=protocol_{kata}.csv"})


@app.get("/api/export/protocol.xlsx")
def api_export_xlsx() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    rows = svc.export_protocol_rows(comp, kata, request.args.get("stage", "qual"))
    try:
        from openpyxl import Workbook
    except ImportError:
        return jsonify({"error": "openpyxl not installed"}), 500
    wb = Workbook()
    ws = wb.active
    ws.title = "Protocol"
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for r in rows:
            ws.append([r[h] for h in headers])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(bio.read(),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet",
                    headers={"Content-Disposition":
                             f"attachment; filename=protocol_{kata}.xlsx"})


@app.get("/api/export/protocol.pdf")
def api_export_pdf() -> Response:
    comp, kata = current_competition(), _norm_kata(request.args.get("kata", ""))
    stage = request.args.get("stage", "qual")
    rows = svc.export_protocol_rows(comp, kata, stage)
    meta = svc.get_competition_meta(comp)
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
    except ImportError:
        return jsonify({"error": "reportlab not installed"}), 500
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    title = meta.get("title") or config.DEFAULT_COMPETITION_TITLE
    elems = [Paragraph(title, styles["Title"]),
             Paragraph(f"{meta.get('subtitle') or ''} — {kata} ({stage})",
                       styles["Normal"]),
             Spacer(1, 12)]
    if rows:
        headers = list(rows[0].keys())
        table_data = [headers] + [[str(r[h]) for h in headers] for r in rows]
        t = Table(table_data)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        elems.append(t)
    doc.build(elems)
    bio.seek(0)
    return Response(bio.read(), mimetype="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename=protocol_{kata}.pdf"})


# ---------- SocketIO ----------

@socketio.on("subscribe_tablo")
def on_subscribe(payload: dict) -> None:
    from flask_socketio import join_room

    comp = payload.get("competition", "default")
    kata = payload.get("kata", "")
    stage = payload.get("stage", "qual")
    join_room(f"tablo:{comp}:{kata}:{stage}")
    join_room(f"tablo:{comp}")
    emit_rows(comp, kata, stage)


def emit_rows(comp: str, kata: str, stage: str) -> None:
    from flask_socketio import emit as sio_emit

    sio_emit("leaderboard", {
        "rows": svc.build_leaderboard(comp, kata, stage),
        "meta": svc.get_competition_meta(comp),
    })


# ---------- HTML-страницы (шаблоны взяты из репозитория) ----------

def _kata_display(kata_key: str) -> str:
    norm = lambda s: s.lower().replace(" ", "").replace("-", "").replace("_", "")
    for name in technics.Technics:
        if norm(name) == norm(kata_key):
            return name
    return kata_key or ""


def _norm_kata(kata_key: str) -> str:
    """Приведение ключа ката к каноническому виду technics._disc_key."""
    norm = lambda s: s.lower().replace(" ", "").replace("-", "").replace("_", "")
    for key in technics.DISCIPLINE_ROWS_BY_KEY:
        if norm(key) == norm(kata_key):
            return key
    return (kata_key or "").lower()


@app.get("/")
def index() -> Response:
    return redirect(url_for("main_tablo"))


@app.get("/login")
def admin_login() -> Response:
    return render_template("login.html")


@app.post("/login")
def admin_login_post() -> Response:
    password = request.form.get("password", "")
    if password == config.ADMIN_PASSWORD:
        session["is_admin"] = True
        return redirect(url_for("admin_dashboard"))
    return render_template("login.html", message="Неверный пароль"), 401


@app.get("/logout")
def admin_logout() -> Response:
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@app.get("/admin/dashboard")
def admin_dashboard() -> Response:
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    competitions = svc.get_all_competitions()
    return render_template("admin_dashboard.html", competitions=competitions)


@app.get("/config")
@app.get("/admin/config")
@app.get("/competition/config")
@app.route("/config/competition", endpoint="config_competition")
def config_page() -> Response:
    comp = current_competition()
    meta = svc.get_competition_meta(comp)
    meta.setdefault("name", meta.get("title") or config.DEFAULT_COMPETITION_TITLE)
    meta.setdefault("status", "open")
    meta.setdefault("created", meta.get("date") or "")
    disciplines = []
    for key, rows in technics.DISCIPLINE_ROWS_BY_KEY.items():
        disciplines.append({
            "key": key, "name": _kata_display(key),
            "pair_count": len(svc.get_effective_pairs(comp, key, "qual")),
            "stage": {"mode": "qual", "status": "open", "final_top_n": 3},
            "stage_label": "Квалификация",
        })
    return render_template("config.html", config=meta, disciplines=disciplines)


@app.get("/admin/<comp_name>")
@app.get("/admin/competition/<comp_name>")
def edit_competition(comp_name: str) -> Response:
    """Страница редактирования соревнования (данные — из БД)."""
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    svc.get_or_create_competition(comp_name)
    meta = svc.get_competition_meta(comp_name)
    config_ctx = {
        "name": meta.get("title") or comp_name,
        "display_name": meta.get("title") or comp_name,
        "status": meta.get("status", "open"),
        "main_tablo_discipline": meta.get("main_tablo_discipline") or "",
        "created": meta.get("date") or "",
        "banner": meta.get("subtitle") or "",
    }
    disciplines = []
    for d in Discipline.query.filter_by(
            competition_id=svc.get_or_create_competition(comp_name).id).all():
        disciplines.append({
            "key": d.kata_key, "name": d.display_name or _kata_display(d.kata_key),
            "pair_count": len(svc.get_effective_pairs(comp_name, d.kata_key, "qual")),
            "stage": {"mode": "qual", "status": "open",
                      "final_top_n": meta.get("final_top_n", 3)},
            "stage_label": "Квалификация",
        })
    available = [{"key": k, "name": _kata_display(k)}
                 for k in technics.DISCIPLINE_ROWS_BY_KEY]
    protocol_status = {"disciplines": [
        {"key": d["key"], "name": d["name"],
         "pairs_registered": d["pair_count"] > 0,
         "judge_protocol_files": [], "ready": False}
        for d in disciplines]}
    return render_template("edit_competition.html",
                           comp_name=comp_name, config=config_ctx,
                           disciplines=disciplines,
                           available_disciplines=available,
                           protocol_status=protocol_status)


@app.get("/data-editor")
def data_editor() -> Response:
    return render_template("data_editor.html")


@app.get("/tablo")
@app.get("/tablo/<comp_name>/<kata_key>")
@app.get("/tablo/<comp_name>/<kata_key>/<stage>")
def tablo(comp_name: str | None = None, kata_key: str = "",
          stage: str = "qual") -> Response:
    comp = comp_name or current_competition()
    kata_key = _norm_kata(kata_key)
    meta = svc.get_competition_meta(comp)
    cfg = {
        "name": meta.get("title") or config.DEFAULT_COMPETITION_TITLE,
        "display_name": meta.get("title") or config.DEFAULT_COMPETITION_TITLE,
        "banner": meta.get("subtitle") or "",
        "main_tablo_discipline": meta.get("main_tablo_discipline") or kata_key,
    }
    kata = kata_key or cfg["main_tablo_discipline"] or ""
    judges = svc.get_effective_judges(comp, kata, stage)
    board = svc.build_leaderboard(comp, kata, stage)
    results = []
    for r in board:
        per_judge = svc.list_judge_scores(comp, kata, stage, "", r["pair_number"])
        score_map = {j["name"]: j["total"] for j in per_judge}
        results.append({
            "place": r["place"], "pair_number": r["pair_number"],
            "tori_cell": {"name": r["tori"].get("name", ""), "detail": None},
            "uke_cell": {"name": r["uke"].get("name", ""), "detail": None},
            "judge_scores": [score_map.get(j["name"]) for j in judges],
            "final_score": r["final_score"],
        })
    return render_template("tablo.html", comp_name=comp, kata_key=kata,
                           stage=stage, config=cfg, judges=judges,
                           results=results, display_date=meta.get("date") or "")


@app.get("/main-tablo")
@app.get("/main_tablo")
def main_tablo() -> Response:
    comp = current_competition()
    meta = svc.get_competition_meta(comp)
    kata = meta.get("main_tablo_discipline") or ""
    stage = meta.get("current_stage", "qual")
    judges = svc.get_effective_judges(comp, kata, stage)
    board = svc.build_leaderboard(comp, kata, stage)
    results = []
    for r in board:
        per_judge = svc.list_judge_scores(comp, kata, stage, "", r["pair_number"])
        score_map = {j["name"]: j["total"] for j in per_judge}
        results.append({
            "place": r["place"], "pair_number": r["pair_number"],
            "tori_cell": {"name": r["tori"].get("name", ""), "detail": None},
            "uke_cell": {"name": r["uke"].get("name", ""), "detail": None},
            "judge_scores": [score_map.get(j["name"]) for j in judges],
            "final_score": r["final_score"],
        })
    cfg = {"name": meta.get("title") or config.DEFAULT_COMPETITION_TITLE,
           "banner": meta.get("subtitle") or "",
           "main_tablo_discipline": kata}
    return render_template("main_tablo_dynamic.html", config=cfg,
                           comp_name=comp, kata_key=kata, stage=stage,
                           judges=judges, results=results)


@app.get("/dashboard")
@app.get("/public")
def public_dashboard() -> Response:
    comps = []
    for c in svc.get_all_competitions():
        folder = c.get("folder_name") or c.get("name", "")
        ds = Discipline.query.filter_by(competition_id=c.get("id")).all() \
            if c.get("id") else []
        comps.append({**c, "disciplines": [
            {"key": d.kata_key, "name": d.display_name or _kata_display(d.kata_key)}
            for d in ds]})
    return render_template("public_dashboard.html", competitions=comps)


@app.get("/registration/<comp_name>/<kata_key>")
def register_participants(comp_name: str, kata_key: str) -> Response:
    kata_key = _norm_kata(kata_key)
    pairs = svc.get_effective_pairs(comp_name, kata_key,
                                    request.args.get("stage", "qual"))
    return render_template("registration.html", comp_name=comp_name,
                           kata_key=kata_key, pairs=pairs,
                           disciplines=list(technics.DISCIPLINE_ROWS_BY_KEY))


@app.get("/judge/<comp_name>/<kata_key>/<judge_name>")
@app.get("/judge/<comp_name>/<kata_key>/<judge_name>/<position>")
def judge_page(comp_name: str, kata_key: str, judge_name: str,
               position: str = "1") -> Response:
    kata_key = _norm_kata(kata_key)
    stage = request.args.get("stage", "qual")
    disc = None
    comp = Competition.query.filter_by(folder_name=comp_name).first()
    if comp is not None:
        disc = Discipline.query.filter_by(competition_id=comp.id,
                                          kata_key=kata_key).first()
    techniques = list(technics.DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
    if disc is not None and disc.techniques_count:
        techniques = techniques[:disc.techniques_count]
    pairs = svc.get_effective_pairs(comp_name, kata_key, stage)
    judges = svc.get_effective_judges(comp_name, kata_key, stage)
    return render_template("judge_form.html", comp_name=comp_name,
                           kata_key=kata_key, judge=judge_name,
                           judge_name=judge_name, stage=stage,
                           techniques=techniques, pairs=pairs, judges=judges,
                           judge_positions=[1, 2, 3, 4, 5, 6, 7],
                           stage_error=None)


@app.get("/results/<comp_name>/<kata_key>")
def results_page(comp_name: str, kata_key: str) -> Response:
    kata_key = _norm_kata(kata_key)
    stage = request.args.get("stage", "qual")
    board = svc.build_leaderboard(comp_name, kata_key, stage)
    judges = svc.get_effective_judges(comp_name, kata_key, stage)
    results = []
    for r in board:
        per_judge = svc.list_judge_scores(comp_name, kata_key, stage, "",
                                          r["pair_number"])
        score_map = {j["name"]: j["total"] for j in per_judge}
        results.append({
            "place": r["place"], "pair_number": r["pair_number"],
            "scores": [score_map.get(j["name"]) for j in judges],
            "final_score": r["final_score"],
            "pair": {"tori": r["tori"], "uke": r["uke"]},
        })
    return render_template("results.html", results=results, judges=judges,
                           comp_name=comp_name, kata_key=kata_key)


# ---------- админские JSON-действия для шаблонов ----------

def _reload_board(comp: str, kata: str, stage: str) -> None:
    socketio.emit("leaderboard", {"rows": svc.build_leaderboard(comp, kata, stage)},
                  room=f"tablo:{comp}:{kata}:{stage}")


@app.post("/admin/create")
@require_admin
def api_create_competition() -> Response:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    folder = re.sub(r"[^\w\-]+", "_", name)[:80]
    comp = svc.get_or_create_competition(folder)
    svc.set_competition_meta(folder, title=name)
    return jsonify({"ok": True, "folder": folder, "id": comp.id}), 201


@app.post("/admin/clear-participants")
@require_admin
def api_clear_participants() -> Response:
    with app.app_context():
        n = svc.clear_participants()
    return jsonify({"ok": True, "deleted": n})


@app.post("/admin/clear-judges")
@require_admin
def api_clear_judges() -> Response:
    with app.app_context():
        n = svc.clear_judges()
    return jsonify({"ok": True, "deleted": n})


@app.post("/admin/<comp_name>/add-discipline")
@require_admin
def api_add_discipline(comp_name: str) -> Response:
    data = request.get_json(silent=True) or {}
    kata = _norm_kata(data.get("kata") or data.get("key") or "")
    if kata not in technics.DISCIPLINE_ROWS_BY_KEY:
        return jsonify({"error": "unknown discipline"}), 400
    disc = svc.get_or_create_discipline(comp_name, kata,
                                        techniques_count=int(
                                            data.get("techniques_count", 10)))
    return jsonify({"ok": True, "id": disc.id})


@app.post("/admin/<comp_name>/remove-discipline")
@require_admin
def api_remove_discipline(comp_name: str) -> Response:
    data = request.get_json(silent=True) or {}
    kata = _norm_kata(data.get("kata") or data.get("key") or "")
    comp = Competition.query.filter_by(folder_name=comp_name).first()
    if comp is None:
        return jsonify({"error": "not found"}), 404
    Discipline.query.filter_by(competition_id=comp.id, kata_key=kata)\
        .delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/admin/<comp_name>/set-main-tablo")
@require_admin
def api_set_main_tablo(comp_name: str) -> Response:
    data = request.get_json(silent=True) or {}
    svc.set_main_tablo(comp_name, data.get("discipline") or data.get("kata") or None)
    socketio.emit("meta_updated", {"competition": comp_name},
                  room=f"tablo:{comp_name}")
    return jsonify({"ok": True})


@app.post("/admin/<comp_name>/generate-protocols")
@require_admin
def api_generate_protocols(comp_name: str) -> Response:
    # протоколы формируются на лету; проверка целостности данных
    ok = Competition.query.filter_by(folder_name=comp_name).first() is not None
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.post("/admin/<comp_name>/<new_status>")
@require_admin
def api_set_comp_status(comp_name: str, new_status: str) -> Response:
    if new_status not in ("open", "close"):
        return jsonify({"error": "bad status"}), 400
    svc.set_comp_status(comp_name, new_status)
    return jsonify({"ok": True})


@app.post("/admin/<comp_name>/<discipline_key>/stage")
@require_admin
def api_set_stage(comp_name: str, discipline_key: str) -> Response:
    discipline_key = _norm_kata(discipline_key)
    data = request.get_json(silent=True) or {}
    stage = data.get("stage") or data.get("mode") or "qual"
    top_n = int(data.get("final_top_n", 3) or 3)
    if stage == "final":
        svc.promote_top_to_final(comp_name, discipline_key, top_n)
    svc.set_current_stage(comp_name, stage, top_n)
    _reload_board(comp_name, discipline_key, "qual")
    _reload_board(comp_name, discipline_key, "final")
    return jsonify({"ok": True})


@app.delete("/admin/<comp_name>/delete")
@app.post("/admin/<comp_name>/delete")
@require_admin
def api_delete_competition(comp_name: str) -> Response:
    ok = svc.delete_competition(comp_name)
    return jsonify({"ok": ok}), (200 if ok else 404)


# ---------- API для шаблонов (поиск, автодополнение, регистрация, судьи) ----------

@app.get("/api/participants/search")
def api_participants_search() -> Response:
    q = request.args.get("q", "")
    return jsonify([p.to_dict() for p in svc.search_participants(q)])


@app.get("/api/participants/column-suggestions")
def api_column_suggestions() -> Response:
    field = request.args.get("field", "")
    q = request.args.get("q", "")
    return jsonify(svc.column_suggestions(field, q))


@app.get("/api/participants/info")
def api_participant_info() -> Response:
    info = svc.participant_info(request.args.get("name", ""))
    return jsonify(info or {})


@app.get("/api/judges/search")
def api_judges_search() -> Response:
    q = request.args.get("q", "").lower().strip()
    items = Judge.query.order_by(Judge.name).limit(500).all()
    if q:
        items = [j for j in items if q in (j.name or "").lower()]
    return jsonify([j.to_dict() for j in items])


@app.get("/api/<comp_name>/<kata_key>/registration-data")
def api_registration_data(comp_name: str, kata_key: str) -> Response:
    kata_key = _norm_kata(kata_key)
    stage = request.args.get("stage", "qual")
    return jsonify({
        "pairs": svc.get_effective_pairs(comp_name, kata_key, stage),
        "participants": [p.to_dict() for p in
                         Participant.query.order_by(Participant.name).limit(1000).all()],
    })


@app.post("/api/<comp_name>/<kata_key>/registration")
@require_admin
def api_registration_save(comp_name: str, kata_key: str) -> Response:
    kata_key = _norm_kata(kata_key)
    data = request.get_json(silent=True) or {}
    stage = data.get("stage", "qual")
    svc.get_or_create_discipline(comp_name, kata_key,
                                 techniques_count=int(
                                     data.get("techniques_count", 10) or 10))
    svc.save_pairs(svc.get_or_create_discipline(comp_name, kata_key).id,
                   stage, data.get("pairs", []))
    _reload_board(comp_name, kata_key, stage)
    return jsonify({"ok": True})


@app.post("/api/<comp_name>/<kata_key>/save-judge-action")
def api_save_judge_action(comp_name: str, kata_key: str) -> Response:
    """Сохранение оценки с формы судьи (template judge_form.html)."""
    kata_key = _norm_kata(kata_key)
    data = request.get_json(silent=True) or {}
    stage = data.get("stage", "qual")
    judge_name = (data.get("judge") or data.get("judge_name") or "").strip()
    pair_number = int(data.get("pair_number") or data.get("pair") or 0)
    raw = data.get("techniques_raw") or data.get("techniques") or data.get("scores") or {}
    if not judge_name or pair_number <= 0:
        return jsonify({"error": "judge and pair_number required"}), 400
    total = svc.save_judge_score(comp_name, kata_key, stage, judge_name,
                                 pair_number, raw)
    _reload_board(comp_name, kata_key, stage)
    return jsonify({"ok": True, "total": total})


@app.get("/api/data/<table_name>")
def api_data_get(table_name: str) -> Response:
    return _data_editor_dispatch(table_name)


@app.put("/api/data/<table_name>/<int:item_id>")
@app.post("/api/data/<table_name>/<int:item_id>")
@require_admin
def api_data_put(table_name: str, item_id: int) -> Response:
    return _data_editor_dispatch(table_name, item_id=item_id, method="PUT")


@app.delete("/api/data/<table_name>/<int:item_id>")
@require_admin
def api_data_delete(table_name: str, item_id: int) -> Response:
    return _data_editor_dispatch(table_name, item_id=item_id, method="DELETE")


@app.post("/api/data/<table_name>")
@require_admin
def api_data_post(table_name: str) -> Response:
    return _data_editor_dispatch(table_name, method="POST")


def _data_editor_dispatch(table_name: str, item_id: int | None = None,
                          method: str = "GET") -> Response:
    search = request.args.get("search", "")
    if table_name == "participants":
        if method == "GET":
            items = svc.search_participants(search, limit=500) if search else \
                Participant.query.order_by(Participant.name).limit(500).all()
            return jsonify({"items": [p.to_dict() for p in items]})
        data = request.get_json(silent=True) or {}
        if method == "POST":
            p = svc.upsert_participant(
                data.get("name", "").strip(),
                int(data["birth_year"]) if data.get("birth_year") else None,
                rank=data.get("rank"), kyu=data.get("kyu"),
                sports_school=data.get("sports_school"), coach=data.get("coach"))
            return jsonify(p.to_dict()), 201
        if method == "PUT" and item_id:
            fields = {k: v for k, v in data.items()
                      if k in ("name", "birth_year", "rank", "kyu",
                               "sports_school", "coach")}
            p = svc.update_participant(item_id, **fields)
            return (jsonify(p.to_dict()), 200) if p else \
                (jsonify({"error": "not found"}), 404)
        if method == "DELETE" and item_id:
            p = db.session.get(Participant, item_id)
            if p is None:
                return jsonify({"error": "not found"}), 404
            db.session.delete(p)
            db.session.commit()
            return jsonify({"ok": True})
    elif table_name == "judges":
        if method == "GET":
            items = Judge.query.order_by(Judge.name).limit(500).all()
            if search:
                items = [j for j in items if search.lower() in (j.name or "").lower()]
            return jsonify({"items": [j.to_dict() for j in items]})
        data = request.get_json(silent=True) or {}
        if method == "POST":
            j = svc.upsert_judge(data.get("name", "").strip(),
                                 category=data.get("category"),
                                 region=data.get("region"))
            return jsonify(j.to_dict()), 201
        if method == "PUT" and item_id:
            fields = {k: v for k, v in data.items()
                      if k in ("name", "category", "region")}
            j = svc.update_judge(item_id, **fields)
            return (jsonify(j.to_dict()), 200) if j else \
                (jsonify({"error": "not found"}), 404)
        if method == "DELETE" and item_id:
            j = db.session.get(Judge, item_id)
            if j is None:
                return jsonify({"error": "not found"}), 404
            db.session.delete(j)
            db.session.commit()
            return jsonify({"ok": True})
    elif table_name == "competitions":
        if method == "GET":
            return jsonify({"items": svc.get_all_competitions()})
        if method == "POST":
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            folder = data.get("folder_name") or re.sub(
                r"[^\w\-]+", "_", name)[:80]
            c = svc.get_or_create_competition(folder)
            svc.set_competition_meta(folder, title=name)
            return jsonify({"id": c.id, "folder_name": folder}), 201
        if method == "DELETE" and item_id:
            c = db.session.get(Competition, item_id)
            if c is None:
                return jsonify({"error": "not found"}), 404
            db.session.delete(c)
            db.session.commit()
            return jsonify({"ok": True})
    return jsonify({"error": f"unsupported table/method: {table_name}/{method}"}), 400


# ---------- init ----------

def init_db(with_import: bool = True) -> None:
    with app.app_context():
        db.create_all()
        configure_sqlite_pragmas(db.engine)
        logger.info("Таблицы базы данных созданы/проверены")
        if with_import:
            import_participants_csv(config.PARTICIPANTS_CSV)
            import_judges_csv(config.JUDGES_CSV)


socketio.init_app(app)
init_db()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    socketio.run(app, host="0.0.0.0", port=port, debug=False,
                 allow_unsafe_werkzeug=True)
