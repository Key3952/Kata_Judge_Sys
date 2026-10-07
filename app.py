"""Система судейства дзюдо-ката. БД (SQLite/WAL) — единственный источник
данных; CSV оставлен только для импорта старых баз и экспорта протоколов.

Realtime (п.7): SocketIO room 'tablo:<comp>:<kata>:<stage>' — табло на любом
числе компьютеров обновляется при сохранении оценки без F5.
"""
from __future__ import annotations

import csv
import io
import logging
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, request, session
from flask_socketio import SocketIO

import config
from db_service import DBService, configure_sqlite_pragmas
from models import db, Participant, Judge
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
    comp, kata = current_competition(), request.args.get("kata", "")
    stage = request.args.get("stage", "qual")
    return jsonify(svc.get_effective_pairs(comp, kata, stage))


@app.post("/api/pairs")
@require_admin
def api_save_pairs() -> Response:
    comp, kata = current_competition(), request.json.get("kata", "")
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
    comp, kata = current_competition(), request.json.get("kata", "")
    stage = request.json.get("stage", "qual")
    disc = svc.get_or_create_discipline(comp, kata)
    order = svc.draw_start_order(disc.id, stage)
    socketio.emit("pairs_updated", {"kata": kata, "stage": stage},
                  room=f"tablo:{comp}:{kata}:{stage}")
    return jsonify({"order": order})


# ---------- судейские списки ----------

@app.get("/api/judge-list")
def api_judge_list() -> Response:
    comp, kata = current_competition(), request.args.get("kata", "")
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
    kata = data.get("kata", "")
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
    comp, kata = current_competition(), request.args.get("kata", "")
    stage = request.args.get("stage", "qual")
    return jsonify({
        "rows": svc.build_leaderboard(comp, kata, stage),
        "meta": svc.get_competition_meta(comp),
    })


# ---------- экспорт протоколов (скачивание из браузера) ----------

@app.get("/api/export/protocol.csv")
def api_export_csv() -> Response:
    comp, kata = current_competition(), request.args.get("kata", "")
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
    comp, kata = current_competition(), request.args.get("kata", "")
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
    comp, kata = current_competition(), request.args.get("kata", "")
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
