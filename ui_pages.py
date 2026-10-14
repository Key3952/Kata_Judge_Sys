"""Прежние HTML-страницы админки/табло поверх новой БД-архитектуры.

Legacy-мост (CSV-проекция) удалён: все страницы и их JSON-API читают и
пишут напрямую в SQLAlchemy (db_service). Единые соглашения:
- этап: 'qual' (прежний prelim) | 'final';
- id соревнования в URL = Competition.folder_name;
- пары: {pair_number, tori_name, uke_name, ...}; судьи: {name, position}.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from flask import (Blueprint, Response, abort, jsonify, redirect,
                   render_template, request, session, url_for)

import config
from models import (db, Participant, Judge, Competition, Discipline,
                     DisciplinePair, JudgeScore)
from technics import DISCIPLINE_ROWS_BY_KEY, Technics, _disc_key

logger = logging.getLogger("judo_kata.ui")

STAGE_LABELS = {"qual": "Предварительные встречи", "final": "Финал"}
pages = Blueprint("pages", __name__)


def register(app, svc, socketio) -> None:  # type: ignore[no-untyped-def]
    """Подключает все прежние страницы/JSON-API к приложению поверх БД."""
    _bind(svc, socketio)
    app.register_blueprint(pages)

    # Шаблоны обращаются к эндпоинтам без префикса blueprint
    # (url_for('tablo') вместо url_for('pages.tablo')) — регистрируем те же
    # view-функции под короткими именами на тех же правилах URL.
    seen: set = set()
    for rule in list(app.url_map.iter_rules()):
        if not rule.endpoint.startswith("pages."):
            continue
        short = rule.endpoint[len("pages."):]
        key = (str(rule), short)
        if short in app.view_functions or key in seen:
            continue
        seen.add(key)
        app.add_url_rule(str(rule), endpoint=short,
                         view_func=app.view_functions[rule.endpoint],
                         methods=sorted(rule.methods - {"HEAD", "OPTIONS"}))


def _wants_json() -> bool:
    return (request.headers.get("X-Requested-With") == "fetch"
            or request.is_json)


def stage_norm(stage: str | None) -> str:
    s = (stage or "").lower()
    return "final" if s in ("final", "f") else "qual"


def _push(socketio, comp: str, kata: str, stage: str) -> None:  # type: ignore[no-untyped-def]
    """Realtime-обновления табло без перезагрузки (прежнее поведение)."""
    if socketio is None:
        return
    try:
        socketio.emit("tablo_refresh", {}, room=f"tablo:{comp}:{kata}:{stage}")
        socketio.emit("tablo_update", {"competition": comp, "kata": kata,
                                       "stage": stage}, room=f"tablo:{comp}")
    except Exception:  # pragma: no cover - realtime best-effort
        logger.exception("socketio emit failed")


# ---------- helpers ----------

def get_comp_or_404(folder: str) -> Competition:
    comp = Competition.query.filter_by(folder_name=folder).first()
    if comp is None:
        abort(404, description=f"Соревнование '{folder}' не найдено")
    return comp


def display_name(svc, folder: str) -> str:  # type: ignore[no-untyped-def]
    meta = svc.get_competition_meta(folder)
    return meta.get("title") or folder


def current_stage_of(svc, folder: str, kata: str) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    cfg = svc.get_stage_cfg(folder, kata)
    return stage_norm(cfg.get("current_stage")), cfg


def tablo_payload(svc, folder: str, kata: str, stage: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """Единый контекст обычного табло (использует страница и главное табло)."""
    cfg = svc.get_stage_cfg(folder, kata)
    if stage is None:
        stage, cfg = current_stage_of(svc, folder, kata)
    else:
        stage = stage_norm(stage)
    board = svc.build_leaderboard(folder, kata, stage)
    judges = svc.get_effective_judges(folder, kata, stage)
    disc = svc.get_discipline(folder, kata)
    tcount = disc.techniques_count if disc else 10
    max_total = round(tcount * 10.0, 1)
    comp = Competition.query.filter_by(folder_name=folder).first()
    meta = svc.get_competition_meta(folder)
    rows = []
    for r in board:
        per_judge = [None] * len(judges)
        scores = (JudgeScore.query.filter_by(
            discipline_id=disc.id, stage=stage, pair_number=r["pair_number"]).all()
            if disc is not None else [])
        by_jid = {s.judge_id: s.total for s in scores}
        for i, jd in enumerate(judges):
            per_judge[i] = by_jid.get(jd["id"])
        rows.append({
            "pair_number": r["pair_number"],
            "tori_cell": {"name": r["tori"].get("name", ""),
                          "detail": _detail(r["tori"], tcount)},
            "uke_cell": {"name": r["uke"].get("name", ""),
                         "detail": _detail(r["uke"], tcount)},
            "judge_scores": per_judge,
            "final_score": r["final_score"],
            "place": r["place"],
        })
    date_str = meta.get("date") or ""
    try:
        display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        display_date = date_str
    banner = comp.banner if comp else None
    return {
        "comp_name": folder,
        "comp_display_name": meta.get("title") or config.DEFAULT_COMPETITION_TITLE,
        "kata_key": kata,
        "kata_name": (disc.display_name if disc and disc.display_name else kata),
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "display_date": display_date,
        "config": {"banner": banner, "main_tablo_discipline":
                   comp.main_tablo_discipline if comp else None},
        "judges": [{"name": j["name"], "position": j["position"],
                    "место": j["position"]} for j in judges],
        "results": rows,
        "score_max": max_total,
    }


def _detail(p: dict, techniques_count: int) -> str:
    parts = []
    if p.get("birth_year"):
        parts.append(str(p["birth_year"]))
    if p.get("sports_school"):
        parts.append(str(p["sports_school"]))
    if p.get("coach"):
        parts.append("тренер: " + str(p["coach"]))
    return " · ".join(parts)


def _ru(d: dict) -> dict:
    """Формат data_editor.html: русские ключи + id для PUT/DELETE."""
    out = {"id": d.get("id"), "ФИО": d.get("name", ""),
           "год рождения": d.get("birth_year") or "",
           "разряд": d.get("rank") or "", "кю": d.get("kyu") or "",
           "СШ": d.get("sports_school") or "", "тренер": d.get("coach") or ""}
    if "category" in d:
        out["категория"] = d.get("category") or ""
    if "region" in d:
        out["регион"] = d.get("region") or ""
    return out


# ---------- auth & pages ----------

def _bind(svc, socketio) -> None:  # type: ignore[no-untyped-def]

    @pages.get("/")
    def index() -> Response:
        return redirect(url_for("pages.public_dashboard"))

    @pages.get("/login")
    def admin_login():  # type: ignore[no-untyped-def]
        if session.get("is_admin"):
            return redirect(url_for("pages.admin_dashboard"))
        return render_template("login.html")

    @pages.post("/login")
    def admin_login_post():  # type: ignore[no-untyped-def]
        pwd = request.form.get("password") or (request.get_json(silent=True) or {}).get("password")
        if pwd == config.ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("pages.admin_dashboard"))
        return render_template("login.html", error="Неверный пароль"), 401

    @pages.get("/logout")
    @pages.post("/logout")
    def admin_logout():  # type: ignore[no-untyped-def]
        session.clear()
        return redirect(url_for("pages.public_dashboard"))

    @pages.get("/admin")
    def admin_dashboard():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return redirect(url_for("pages.admin_login"))
        comps = [{"name": c["folder"], "display_name": c["name"],
                  "status": c["status"]} for c in svc.list_competitions()]
        return render_template("admin_dashboard.html", competitions=comps,
                               stats={"participants": Participant.query.count(),
                                      "judges": Judge.query.count()})

    @pages.get("/dashboard")
    def public_dashboard():  # type: ignore[no-untyped-def]
        comps = []
        for c in svc.list_competitions():
            folder = c["folder"]
            discs = []
            for d in Discipline.query.filter_by(
                    competition_id=Competition.query.filter_by(
                        folder_name=folder).first().id).all():
                st, _cfg = current_stage_of(svc, folder, d.kata_key)
                discs.append({"key": d.kata_key,
                              "name": d.display_name or d.kata_key,
                              "stage_label": STAGE_LABELS[st]})
            comps.append({"name": folder, "display_name": c["name"],
                          "disciplines": discs})
        return render_template("public_dashboard.html", competitions=comps)

    @pages.get("/admin/new")
    def new_competition():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return redirect(url_for("pages.admin_login"))
        return render_template("config.html", default_path="",
                               disciplines=Technics)

    @pages.post("/admin/create")
    def create_competition():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or request.form
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        folder = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60] \
            or datetime.now().strftime("comp_%Y%m%d%H%M%S")
        comp = svc.get_or_create_competition(folder)
        title = (data.get("title") or name).strip()
        svc.set_competition_meta(folder, title=title,
                                 date=data.get("date") or None,
                                 location=data.get("location") or None)
        return redirect(url_for("pages.edit_competition", comp_name=comp.folder_name))

    @pages.get("/admin/<comp_name>")
    def edit_competition(comp_name: str):  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return redirect(url_for("pages.admin_login"))
        get_comp_or_404(comp_name)
        ctx = svc.competition_ui_context(comp_name)
        ps_items = []
        for d in ctx.get("disciplines", []):
            d_obj = svc.get_discipline(comp_name, d["key"])
            stg = stage_norm(d["stage"].get("current_stage"))
            n_files = JudgeScore.query.filter_by(
                discipline_id=d_obj.id, stage=stg).count() if d_obj else 0
            ready = d["final_with_total"] > 0 and d["pairs_registered"] > 0
            ps_items.append({"key": d["key"],
                             "pairs_registered": d["pairs_registered"],
                             "final_with_total": d["final_with_total"],
                             "judge_protocol_files": n_files,
                             "ready": ready})
        protocol_status = {
            "disciplines": ps_items,
            "overall_ready": any(p["ready"] for p in ps_items),
        }
        return render_template("edit_competition.html",
                               comp_name=comp_name,
                               config=ctx.get("config", {}),
                               disciplines=ctx.get("disciplines", []),
                               available_disciplines=[
                                   {"key": k, "name": k} for k in Technics],
                               protocol_status=protocol_status)

    # ---------- регистрация ----------

    @pages.get("/register/<comp_name>/<kata_key>")
    def register_participants(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        stage, cfg = current_stage_of(svc, comp_name, kata_key)
        pairs = svc.get_effective_pairs(comp_name, kata_key, stage)
        reg_rows = [{"pair_number": p["pair_number"],
                     "start_order": p.get("start_order"),
                     "tori_name": p["tori"].get("name", ""),
                     "uke_name": p["uke"].get("name", ""),
                     "tori_year": p["tori"].get("birth_year"),
                     "uke_year": p["uke"].get("birth_year")} for p in pairs]
        return render_template("registration.html",
                               comp_name=comp_name,
                               comp_display_name=display_name(svc, comp_name),
                               kata_key=kata_key, kata_name=kata_key,
                               stage=stage, stage_label=STAGE_LABELS[stage],
                               existing_pairs=reg_rows,
                               next_pair=(max([p["pair_number"] for p in pairs],
                                              default=0) + 1))

    @pages.get("/api/participants/search")
    def api_participants_search():  # type: ignore[no-untyped-def]
        q = request.args.get("q", "")
        return jsonify([p.to_dict() for p in svc.search_participants(q, limit=30)])

    @pages.get("/api/participants/column-suggestions")
    def api_participants_column_suggestions():  # type: ignore[no-untyped-def]
        """Подсказки по колонке реестра участников (автодополнение в регистрации)."""
        field = request.args.get("field", "name")
        q = (request.args.get("q") or "").strip().lower()
        col_map = {"name": Participant.name, "birth_year": Participant.birth_year,
                   "rank": Participant.rank, "kyu": Participant.kyu,
                   "sports_school": Participant.sports_school, "coach": Participant.coach}
        col = col_map.get(field, Participant.name)
        rows = (db.session.query(col).distinct()
                .filter(col.isnot(None), col != "")
                .order_by(col).limit(200).all())
        vals = [str(r[0]) for r in rows if not q or q in str(r[0]).lower()]
        return jsonify(vals[:30])

    @pages.get("/api/participants/info")
    def api_participants_info():  # type: ignore[no-untyped-def]
        """Карточка участника по ФИО для автозаполнения формы регистрации."""
        name = (request.args.get("name") or "").strip().lower()
        p = (Participant.query
             .filter(db.func.lower(Participant.name) == name).first())
        if p is None:
            return jsonify({})
        d = p.to_dict()
        return jsonify({"ФИО": d.get("name", ""),
                        "год рождения": d.get("birth_year") or "",
                        "разряд": d.get("rank") or "",
                        "кю": d.get("kyu") or "",
                        "СШ": d.get("sports_school") or "",
                        "тренер": d.get("coach") or ""})

    @pages.get("/api/judges/search")
    def api_judges_search():  # type: ignore[no-untyped-def]
        q = (request.args.get("q") or "").strip().lower()
        jq = Judge.query
        if q:
            jq = jq.filter(db.func.lower(Judge.name).like(f"%{q}%"))
        return jsonify([j.to_dict() for j in jq.order_by(Judge.name).limit(30).all()])

    @pages.get("/api/<comp_name>/<kata_key>/registration-data")
    def registration_data(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        stage, _cfg = current_stage_of(svc, comp_name, kata_key)
        pairs = svc.get_effective_pairs(comp_name, kata_key, stage)
        judges = svc.get_effective_judges(comp_name, kata_key, stage)
        return jsonify({
            "existing_pairs": [{
                "номер пары": p["pair_number"],
                "Тори_ФИО": p["tori"].get("name", ""),
                "Уке_ФИО": p["uke"].get("name", ""),
                "Тори_год рождения": p["tori"].get("birth_year") or "",
                "Уке_год рождения": p["uke"].get("birth_year") or "",
                "Тори_разряд": p["tori"].get("rank") or "",
                "Уке_разряд": p["uke"].get("rank") or "",
                "Тори_кю": p["tori"].get("kyu") or "",
                "Уке_кю": p["uke"].get("kyu") or "",
                "Тори_СШ": p["tori"].get("sports_school") or "",
                "Уке_СШ": p["uke"].get("sports_school") or "",
                "Тори_тренер": p["tori"].get("coach") or "",
                "Уке_тренер": p["uke"].get("coach") or "",
            } for p in pairs],
            "existing_judges": [{"ФИО": j["name"], "место": j["position"]}
                                for j in judges],
        })

    @pages.post("/register/<comp_name>/<kata_key>")
    def post_registration(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        stage, _cfg = current_stage_of(svc, comp_name, kata_key)
        disc = svc.get_or_create_discipline(comp_name, kata_key)
        form = request.form
        items = []
        # прежняя разметка формы: pair_<id>_tori_<field>, id нумеруется с 0
        ids = sorted({int(k.split("_")[1]) for k in form
                      if k.startswith("pair_") and len(k.split("_")) >= 4})
        for i in ids:
            def f(role: str, field: str) -> str:  # type: ignore[no-untyped-def]
                return (form.get(f"pair_{i}_{role}_{field}")
                        or form.get(f"{role}_name_{i + 1}")
                        or "").strip()
            tori_name = f("tori", "name")
            uke_name = f("uke", "name")
            if not tori_name and not uke_name:
                continue
            year_re = re.compile(r"^(\d{4})$")

            def year(v: str):  # type: ignore[no-untyped-def]
                v = (v or "").strip()[:4]
                return int(v) if year_re.match(v) else None
            items.append({
                "pair_number": i + 1,
                "tori_name": tori_name, "uke_name": uke_name,
                "tori_birth_year": year(f("tori", "birth")),
                "uke_birth_year": year(f("uke", "birth")),
                "tori_rank": f("tori", "rank"), "uke_rank": f("uke", "rank"),
                "tori_kyu": f("tori", "kyu"), "uke_kyu": f("uke", "kyu"),
                "tori_sports_school": f("tori", "school"),
                "uke_sports_school": f("uke", "school"),
                "tori_coach": f("tori", "coach"), "uke_coach": f("uke", "coach"),
            })
        if items or form:
            svc.save_pairs(disc.id, stage, items)
        judge_names = [form.get(k).strip() for k in sorted(form)
                       if k.startswith("judge_") and k.endswith("_name")
                       and form.get(k, "").strip()]
        if judge_names:
            entries = [{"name": n, "position": idx + 1}
                       for idx, n in enumerate(judge_names)]
            svc.save_judge_list(disc.id, stage, entries)
        _push(socketio, comp_name, kata_key, stage)
        if _wants_json():
            return jsonify({"ok": True, "saved": len(items)})
        return redirect(url_for("pages.register_participants",
                                comp_name=comp_name, kata_key=kata_key))

    # ---------- судейская форма ----------

    @pages.get("/judge/<comp_name>/<kata_key>")
    def judge_page(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        stage, cfg = current_stage_of(svc, comp_name, kata_key)
        disc = svc.get_discipline(comp_name, kata_key)
        stage_error = None
        if cfg.get("status") == "closed":
            stage_error = f"Этап «{STAGE_LABELS[stage]}» закрыт администратором."
        tcount = disc.techniques_count if disc else 10
        techniques = DISCIPLINE_ROWS_BY_KEY.get(_disc_key(kata_key),
                                                [])[:tcount]
        judges = svc.get_effective_judges(comp_name, kata_key, stage)
        pairs = svc.get_effective_pairs(comp_name, kata_key, stage)
        return render_template("judge_form.html",
                               comp_name=comp_name,
                               comp_display_name=display_name(svc, comp_name),
                               kata_key=kata_key, kata_name=kata_key,
                               stage=stage, stage_error=stage_error,
                               judges=[{"ФИО": j["name"], "name": j["name"],
                                        "position": j["position"]} for j in judges],
                               judge_positions=sorted({j["position"] for j in judges} or [1]),
                               pairs=[{"номер пары": p["pair_number"],
                                       "pair_number": p["pair_number"],
                                       "Тори_ФИО": p["tori"].get("name", ""),
                                       "tori_name": p["tori"].get("name", ""),
                                       "Уке_ФИО": p["uke"].get("name", ""),
                                       "uke_name": p["uke"].get("name", "")}
                                      for p in pairs],
                               techniques=techniques,
                               score_max=round(tcount * 10.0, 1))

    @pages.post("/api/<comp_name>/<kata_key>/save-judge-action")
    def save_judge_action(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        data = request.get_json(silent=True) or {}
        stage = stage_norm(data.get("stage") or
                           current_stage_of(svc, comp_name, kata_key)[0])
        details = data.get("details") or {}
        pos = data.get("pos") or data.get("position")
        pair_no = int(data.get("pair", 0) or data.get("pair_number", 0) or 0)
        if isinstance(details, dict):
            if pos is not None:
                details = {**details, "position": int(pos)}
        elif isinstance(details, list):
            # judge_form шлёт массив состояний техник; position приходит
            # отдельным полем запроса — не смешиваем форматы
            pass
        total = svc.save_judge_action(
            comp_name, kata_key, stage, data.get("judge", ""),
            pair_no, details,
            position=(int(pos) if pos is not None else None))
        if pos is not None:
            from models import JudgeScore as JS
            j = Judge.query.filter_by(name=(data.get("judge") or "").strip()).first()
            disc = svc.get_discipline(comp_name, kata_key)
            if j and disc:
                s = JS.query.filter_by(discipline_id=disc.id, stage=stage,
                                       judge_id=j.id,
                                       pair_number=int(data.get("pair", 0))).first()
                if s:
                    s.position = int(pos)
                    db.session.commit()
        _push(socketio, comp_name, kata_key, stage)
        return jsonify({"ok": True, "success": True, "total": total})

    @pages.get("/api/<comp_name>/<kata_key>/get-judge-scores/<judge>/<pos>/<tori>/<uke>")  # noqa: E501
    def get_judge_scores(comp_name: str, kata_key: str, judge: str,
                         pos: str, tori: str, uke: str):  # type: ignore[no-untyped-def]
        stage = stage_norm(request.args.get("stage")
                           or current_stage_of(svc, comp_name, kata_key)[0])
        pair_number = int(request.args.get("pair", 0) or 0)
        if not pair_number:
            # прежний UI кладёт в URL ФИО тори — найдём номер пары по нему
            pairs = svc.get_effective_pairs(comp_name, kata_key, stage)
            for p in pairs:
                if p["tori"].get("name", "").strip().lower() == tori.strip().lower():
                    pair_number = p["pair_number"]
                    break
        raw = svc.get_judge_pair_raw(comp_name, kata_key, stage, judge, pair_number)
        if isinstance(raw, dict) and "details" in raw:
            raw = raw["details"]
        return jsonify(raw or {})

    # ---------- табло ----------

    @pages.get("/tablo/<comp_name>/<kata_key>")
    @pages.get("/tablo/<comp_name>/<kata_key>/<stage>", endpoint="tablo_stage")
    def tablo(comp_name: str, kata_key: str, stage: str | None = None):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        ctx = tablo_payload(svc, comp_name, kata_key, stage_norm(stage) if stage else None)
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ctx)
        return render_template("tablo.html", **ctx)

    @pages.get("/main-tablo/<comp_name>")
    def main_tablo(comp_name: str):  # type: ignore[no-untyped-def]
        comp = get_comp_or_404(comp_name)
        meta = svc.get_competition_meta(comp_name)
        ctx_base = {"comp_name": comp_name,
                    "comp_display_name": meta.get("title") or comp_name,
                    "config": {"main_tablo_discipline": comp.main_tablo_discipline}}
        if not comp.main_tablo_discipline:
            return render_template("main_tablo_empty.html", **ctx_base)
        return render_template("main_tablo_dynamic.html", **ctx_base)

    @pages.get("/api/main-tablo/<comp_name>/state")
    def main_tablo_state(comp_name: str):  # type: ignore[no-untyped-def]
        comp = get_comp_or_404(comp_name)
        if not comp.main_tablo_discipline:
            return jsonify({"discipline": None})
        ctx = tablo_payload(svc, comp_name, comp.main_tablo_discipline)
        return jsonify({"discipline": comp.main_tablo_discipline,
                        "url": url_for("pages.tablo", comp_name=comp_name,
                                       kata_key=comp.main_tablo_discipline),
                        "html": render_template("tablo.html", **ctx)})

    @pages.get("/results/<comp_name>/<kata_key>")
    def results_page(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        stage = current_stage_of(svc, comp_name, kata_key)[0]
        board = svc.build_leaderboard(comp_name, kata_key, stage)
        judges = svc.get_effective_judges(comp_name, kata_key, stage)
        disc = svc.get_discipline(comp_name, kata_key)
        scores = (JudgeScore.query.filter_by(discipline_id=disc.id,
                                             stage=stage).all()
                  if disc is not None else [])
        by_pair_judge: dict[int, dict[int, float]] = {}
        for s in scores:
            by_pair_judge.setdefault(s.pair_number, {})[s.judge_id] = s.total
        rows = []
        for r in board:
            pj = by_pair_judge.get(r["pair_number"], {})
            rows.append({
                "place": r["place"],
                "pair": {"tori": r["tori"], "uke": r["uke"]},
                "judge_scores": {j["id"]: pj.get(j["id"]) or 0.0
                                 for j in judges},
                "final_score": r["final_score"],
            })
        return render_template("results.html",
                               comp_name=comp_name, kata_key=kata_key,
                               judges=judges, results=rows)

    # ---------- SocketIO rooms ----------

    @socketio.on("subscribe_tablo")
    def on_subscribe_tablo(payload: dict):  # type: ignore[no-untyped-def]
        from flask_socketio import join_room
        comp = payload.get("competition", "") or payload.get("comp_name", "")
        kata = payload.get("kata", "") or payload.get("discipline", "")
        stage = stage_norm(payload.get("stage"))
        if not kata:
            join_room(f"tablo:{comp}")
            return
        join_room(f"tablo:{comp}:{kata}:{stage}")

    @socketio.on("join_tablo")
    def on_join_tablo(payload: dict):  # type: ignore[no-untyped-def]
        """Прежний коннект главного табло: {comp_name} — подписка на все
        дисциплины соревнования; {comp_name, kata_key, stage} — конкретное."""
        from flask_socketio import join_room
        comp = (payload or {}).get("comp_name", "")
        if not comp:
            return
        join_room(f"tablo:{comp}")
        kata = payload.get("kata_key") or payload.get("kata")
        if kata:
            stage = stage_norm(payload.get("stage") or "qual")
            join_room(f"tablo:{comp}:{kata}:{stage}")

    # ---------- admin JSON API ----------

    @pages.post("/admin/<comp_name>/add-discipline")
    def add_discipline(comp_name: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        data = request.get_json(silent=True) or request.form
        key = (data.get("kata") or data.get("kata_key") or data.get("discipline") or "").strip()
        if not key:
            return jsonify({"error": "kata required"}), 400
        svc.get_or_create_discipline(comp_name, key,
                                     display_name=data.get("display_name"))
        return jsonify({"ok": True})

    @pages.post("/admin/<comp_name>/remove-discipline")
    def remove_discipline(comp_name: str):  # type: ignore[no-untyped-def]
        data = request.get_json(silent=True) or request.form
        ok = svc.delete_discipline(comp_name, data.get("kata", ""))
        return jsonify({"ok": ok})

    @pages.post("/admin/<comp_name>/<new_status>")
    def set_comp_status(comp_name: str, new_status: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        if new_status == "close":
            ok = svc.finish_competition(comp_name)
        elif new_status == "open":
            comp = Competition.query.filter_by(folder_name=comp_name).first()
            comp.status = Competition.STATUS_ACTIVE
            db.session.commit()
            ok = True
        else:
            return jsonify({"error": "unknown status"}), 400
        return jsonify({"ok": ok})

    @pages.post("/admin/<comp_name>/set-main-tablo")
    def set_main_tablo(comp_name: str):  # type: ignore[no-untyped-def]
        comp = get_comp_or_404(comp_name)
        data = request.get_json(silent=True) or request.form
        comp.main_tablo_discipline = (data.get("discipline") or "").strip() or None
        db.session.commit()
        _push(socketio, comp_name, comp.main_tablo_discipline or "", "qual")
        return jsonify({"ok": True})

    @pages.post("/admin/<comp_name>/<kata_key>/stage")
    def stage_action(comp_name: str, kata_key: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        data = request.get_json(silent=True) or request.form
        action = data.get("action", "")
        moved = 0
        if action == "set_prelim":
            svc.set_stage_cfg(comp_name, kata_key,
                              {"current_stage": "prelim", "status": "open"})
        elif action == "open_final":
            moved = svc.move_pairs_to_final(comp_name, kata_key,
                                            data.get("top_n") or None)
            svc.set_stage_cfg(comp_name, kata_key,
                              {"current_stage": "final", "status": "open"})
        elif action == "set_final_only":
            svc.set_stage_cfg(comp_name, kata_key,
                              {"mode": "final_only", "current_stage": "final",
                               "status": "open"})
        elif action == "close_stage":
            svc.set_stage_cfg(comp_name, kata_key, {"status": "closed"})
        else:
            return jsonify({"error": f"unknown action {action}"}), 400
        stage = stage_norm("final" if action in ("open_final", "set_final_only")
                           else "qual")
        _push(socketio, comp_name, kata_key, stage)
        return jsonify({"ok": True, "moved": moved})

    @pages.post("/admin/clear-participants")
    def clear_participants():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"cleared": svc.clear_participants()})

    @pages.post("/admin/clear-judges")
    def clear_judges():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"cleared": svc.clear_judges()})

    @pages.post("/admin/<comp_name>/delete")
    def delete_competition(comp_name: str):  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"ok": svc.delete_competition(comp_name)})

    # ---------- реестры (data_editor) ----------

    @pages.get("/data-editor")
    def data_editor():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return redirect(url_for("pages.admin_login"))
        return render_template("data_editor.html")

    @pages.get("/api/data/participants")
    def data_participants():  # type: ignore[no-untyped-def]
        search = request.args.get("search") or request.args.get("q") or ""
        items = svc.search_participants(search, limit=1000) if search else \
            Participant.query.order_by(Participant.name).limit(1000).all()
        return jsonify([p.to_dict() for p in items])

    @pages.post("/api/data/participants")
    def data_add_participant():  # type: ignore[no-untyped-def]
        d = request.get_json(silent=True) or {}
        name = (d.get("name") or d.get("ФИО") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        year = d.get("birth_year") or d.get("год рождения")
        p = svc.upsert_participant(name, int(year) if year else None,
                                   rank=d.get("rank"), kyu=d.get("kyu"),
                                   sports_school=d.get("sports_school") or d.get("СШ"),
                                   coach=d.get("coach"))
        return jsonify(_ru(p.to_dict())), 201

    @pages.put("/api/data/participants/<int:pid>")
    def data_upd_participant(pid: int):  # type: ignore[no-untyped-def]
        d = request.get_json(silent=True) or {}
        fields = {k: v for k, v in d.items()
                  if k in ("name", "birth_year", "rank", "kyu",
                           "sports_school", "coach")}
        if "ФИО" in d:
            fields["name"] = d["ФИО"]
        if "год рождения" in d:
            fields["birth_year"] = d["год рождения"]
        if "СШ" in d:
            fields["sports_school"] = d["СШ"]
        p = svc.update_participant(pid, **fields)
        return (jsonify(_ru(p.to_dict())), 200) \
            if p else (jsonify({"error": "not found"}), 404)

    @pages.delete("/api/data/participants/<int:pid>")
    def data_del_participant(pid: int):  # type: ignore[no-untyped-def]
        p = db.session.get(Participant, pid)
        if p is None:
            return jsonify({"error": "not found"}), 404
        db.session.execute(DisciplinePair.__table__.delete().where(
            (DisciplinePair.tori_id == pid) | (DisciplinePair.uke_id == pid)))
        db.session.delete(p)
        db.session.commit()
        return jsonify({"ok": True})

    @pages.get("/api/data/judges")
    def data_judges():  # type: ignore[no-untyped-def]
        search = (request.args.get("search") or "").strip().lower()
        jq = Judge.query
        if search:
            jq = jq.filter(db.func.lower(Judge.name).like(f"%{search}%"))
        return jsonify([j.to_dict() for j in jq.order_by(Judge.name).limit(1000).all()])

    @pages.post("/api/data/judges")
    def data_add_judge():  # type: ignore[no-untyped-def]
        d = request.get_json(silent=True) or {}
        name = (d.get("name") or d.get("ФИО") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        j = svc.upsert_judge(name, category=d.get("category") or d.get("Категория"),
                             region=d.get("region"))
        return jsonify(j.to_dict()), 201

    @pages.put("/api/data/judges/<int:jid>")
    def data_upd_judge(jid: int):  # type: ignore[no-untyped-def]
        d = request.get_json(silent=True) or {}
        fields = {k: v for k, v in d.items() if k in ("name", "category", "region")}
        if "ФИО" in d:
            fields["name"] = d["ФИО"]
        j = svc.update_judge(jid, **fields)
        return (jsonify(j.to_dict()), 200) if j else (jsonify({"error": "not found"}), 404)

    @pages.delete("/api/data/judges/<int:jid>")
    def data_del_judge(jid: int):  # type: ignore[no-untyped-def]
        j = db.session.get(Judge, jid)
        if j is None:
            return jsonify({"error": "not found"}), 404
        db.session.delete(j)
        db.session.commit()
        return jsonify({"ok": True})

    @pages.get("/api/data/competitions")
    def data_competitions():  # type: ignore[no-untyped-def]
        return jsonify(svc.list_competitions())

    # ---------- протоколы ----------

    @pages.post("/admin/<comp_name>/generate-protocols")
    def generate_protocols_route(comp_name: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        data = request.get_json(silent=True) or {}
        kata = data.get("kata")
        try:
            from protocols_db import generate_from_db
            files = generate_from_db(svc, comp_name, kata).get("files", {})
        except Exception as exc:  # pragma: no cover
            logger.exception("protocol generation failed")
            return jsonify({"error": str(exc)}), 500
        if not files:
            return jsonify({"ok": True, "files": {},
                            "message": "Нет данных для генерации"})
        first = next(iter(files.values()))
        # прежний контракт edit_competition.html: плоский {csv,xlsx,pdf}
        return jsonify({"ok": True, "files": first, "by_discipline": files})

    @pages.get("/admin/<comp_name>/protocols")
    def protocols_page(comp_name: str):  # type: ignore[no-untyped-def]
        get_comp_or_404(comp_name)
        ctx = svc.competition_ui_context(comp_name)
        return render_template("admin/protocols.html", comp_name=comp_name,
                               disciplines=ctx.get("disciplines", []))

    @pages.get("/downloads/<path:fname>")
    def download(fname: str):  # type: ignore[no-untyped-def]
        from flask import send_from_directory
        return send_from_directory(config.DOWNLOADS_DIR, fname,
                                   as_attachment=True)

    # ---------- misc old routes ----------

    @pages.get("/config")
    def config_competition():  # type: ignore[no-untyped-def]
        if not session.get("is_admin"):
            return redirect(url_for("pages.admin_login"))
        return redirect(url_for("pages.new_competition"))

    @pages.app_errorhandler(404)
    def err404(e):  # type: ignore[no-untyped-def]
        return render_template("error.html", message=str(e)), 404
