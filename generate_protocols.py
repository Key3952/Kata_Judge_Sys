import csv
import io
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SKIP_DIRS = frozenset({"__pycache__", "results"})

_PDF_FONT: Optional[str] = None
_PDF_FONT_BOLD: Optional[str] = None


def _ensure_pdf_fonts() -> Tuple[str, str]:
    global _PDF_FONT, _PDF_FONT_BOLD
    if _PDF_FONT is not None:
        return _PDF_FONT, _PDF_FONT_BOLD or "Helvetica-Bold"
    candidates = [
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            bp = p.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
            try:
                pdfmetrics.registerFont(TTFont("KataDejaVu", p))
                _PDF_FONT = "KataDejaVu"
                if os.path.isfile(bp):
                    pdfmetrics.registerFont(TTFont("KataDejaVu-Bold", bp))
                    _PDF_FONT_BOLD = "KataDejaVu-Bold"
                else:
                    _PDF_FONT_BOLD = "KataDejaVu"
                return _PDF_FONT, _PDF_FONT_BOLD
            except Exception:
                pass
    _PDF_FONT, _PDF_FONT_BOLD = "Helvetica", "Helvetica-Bold"
    return _PDF_FONT, _PDF_FONT_BOLD


def _discipline_folders(comp_path: str) -> List[str]:
    out = []
    if not os.path.isdir(comp_path):
        return out
    for name in sorted(os.listdir(comp_path)):
        p = os.path.join(comp_path, name)
        if os.path.isdir(p) and name not in SKIP_DIRS and name != "__pycache__":
            out.append(name)
    return out


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None:
            return []
        for row in r:
            rows.append({k: (v or "") for k, v in row.items() if k is not None})
    return rows


def _load_stage(comp_path: str, discipline_key: str) -> Dict[str, str]:
    p = os.path.join(comp_path, discipline_key, "stage.json")
    cfg = {"current_stage": "final", "mode": "final_only", "status": "open"}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    return cfg


def _stage_paths(comp_path: str, discipline_key: str, stage: str) -> Dict[str, str]:
    s = "final" if str(stage).lower() == "final" else "prelim"
    base = os.path.join(comp_path, discipline_key, s)
    return {
        "pairs": os.path.join(base, "participants_list.csv"),
        "final": os.path.join(base, "final_protocol.csv"),
        "protocols": os.path.join(base, "protocols"),
    }


def _decode_participant_cell(cell_value: Any) -> Dict[str, str]:
    if cell_value is None:
        return {"name": "", "detail": ""}
    s = str(cell_value).strip()
    if "||" in s:
        a, _, b = s.partition("||")
        return {"name": a.strip(), "detail": b.strip()}
    return {"name": s, "detail": ""}


def _score_from_detail(d: dict) -> float:
    if not d:
        return 10.0
    if d.get("forgotten"):
        return 0.0
    v = 10.0
    v -= float(d.get("m1", 0) or 0)
    v -= float(d.get("m2", 0) or 0)
    v -= float(d.get("med", 0) or 0)
    v -= float(d.get("big", 0) or 0)
    v -= float(d.get("c_minus", 0) or 0)
    v -= float(d.get("c_plus", 0) or 0)
    return max(0.0, min(10.0, v))


def _judge_total(details: List[dict]) -> float:
    total = sum(_score_from_detail(d) for d in details)
    if any(d.get("forgotten") for d in details):
        total /= 2.0
    return total


def _safe_sheet_name(name: str, used: set) -> str:
    bad = set('[]:*?/\\')
    n = "".join("_" if ch in bad else ch for ch in name).strip() or "Sheet"
    n = n[:31]
    base = n
    i = 1
    while n in used:
        suffix = f"_{i}"
        n = (base[: 31 - len(suffix)] + suffix)
        i += 1
    used.add(n)
    return n


def _theme_gradient(score: float) -> str:
    # палитра как в табло (стандарт)
    p = [
        (230, 124, 115),
        (243, 169, 109),
        (255, 214, 102),
        (171, 201, 120),
        (87, 187, 138),
    ]
    t = max(0.0, min(1.0, (score or 0.0) / 170.0))
    seg = len(p) - 1
    idx = min(seg - 1, int(t * seg))
    lt = t * seg - idx
    r = round(p[idx][0] + (p[idx + 1][0] - p[idx][0]) * lt)
    g = round(p[idx][1] + (p[idx + 1][1] - p[idx][1]) * lt)
    b = round(p[idx][2] + (p[idx + 1][2] - p[idx][2]) * lt)
    return f"{r:02X}{g:02X}{b:02X}"


def _autosize(ws):
    for col in ws.columns:
        m = 0
        l = get_column_letter(col[0].column)
        for c in col:
            if c.value is not None:
                m = max(m, len(str(c.value)))
        ws.column_dimensions[l].width = min(max(10, m + 2), 60)


def _apply_grid(ws, start_row: int, end_row: int, start_col: int, end_col: int):
    thin = Side(style="thin", color="6B7280")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for r in range(start_row, end_row + 1):
        for c in range(start_col, end_col + 1):
            cell = ws.cell(r, c)
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _find_protocol_pair(protocol_file: str, pairs: List[dict]) -> Optional[dict]:
    base = protocol_file[:-4]
    parts = base.split("_", 2)
    if len(parts) < 3:
        return None
    pair_slug = parts[2]
    for p in pairs:
        t = str(p.get("Тори_ФИО", "")).replace(" ", "_")
        u = str(p.get("Уке_ФИО", "")).replace(" ", "_")
        if pair_slug in (f"{t}-{u}", f"{u}-{t}"):
            return p
    return None


def _write_tablo_sheet(wb: Workbook, comp_name: str, discipline: str, stage_label: str, final_rows: List[dict], judges: List[dict]):
    ws = wb.create_sheet("Результаты")
    judges = judges[:5]
    total_cols = 3 + len(judges) + 2
    end_col_letter = get_column_letter(total_cols)

    ws.merge_cells(f"A1:{end_col_letter}1")
    ws["A1"] = comp_name
    ws["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["A1"].fill = PatternFill(fill_type="solid", start_color="1A3D5C", end_color="1A3D5C")

    ws.merge_cells(f"A2:{end_col_letter}2")
    ws["A2"] = f"{discipline} | {stage_label} | {datetime.now().strftime('%d.%m.%Y')}"
    ws["A2"].font = Font(size=12, bold=True, color="FFD84A")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws["A2"].fill = PatternFill(fill_type="solid", start_color="224F87", end_color="224F87")

    # Фон под шапкой, включая область под лого
    for rr in range(1, 4):
        for cc in range(1, total_cols + 1):
            if rr <= 2:
                continue
            ws.cell(rr, cc).fill = PatternFill(fill_type="solid", start_color="224F87", end_color="224F87")

    headers = ["Пара", "Тори", "Уке"] + [f"Судья {j.get('место','')}" for j in judges] + ["Сумма", "Место"]
    ws.append([])
    ws.append(headers)
    hr = ws.max_row
    hf = PatternFill(fill_type="solid", start_color="2D5A7B", end_color="2D5A7B")
    for i, h in enumerate(headers, 1):
        c = ws.cell(hr, i)
        c.value = h
        c.font = Font(color="FFFFFF", bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = hf

    data_start = ws.max_row + 1
    for row in final_rows:
        t = _decode_participant_cell(row.get("Тори", ""))["name"]
        u = _decode_participant_cell(row.get("Уке", ""))["name"]
        judge_vals = []
        for j in judges:
            pos = int(str(j.get("место", 0) or 0))
            v = row.get(f"Судья {pos}", "") if pos else ""
            judge_vals.append(v)
        line = [row.get("номер пары", ""), t, u] + judge_vals + [row.get("Сумма", ""), row.get("Место", "")]
        ws.append(line)
        r = ws.max_row
        start = 4
        for idx, v in enumerate(judge_vals, start):
            try:
                sv = float(str(v).replace(",", "."))
                ws.cell(r, idx).fill = PatternFill(fill_type="solid", start_color=_theme_gradient(sv), end_color=_theme_gradient(sv))
            except Exception:
                pass
        if str(row.get("Место", "")).strip() in {"1", "2", "3"}:
            ws.cell(r, len(headers)).font = Font(size=16, bold=True, color="CF1F25")

    # логотип (по возможности)
    logo = os.path.join(os.path.dirname(__file__), "static", "federation_logo.png")
    if os.path.exists(logo):
        try:
            from openpyxl.drawing.image import Image  # type: ignore
            img = Image(logo)
            img.width = 120
            img.height = 120
            ws.add_image(img, "H1")
        except Exception:
            pass

    # Границы и авто-таблица
    _apply_grid(ws, 4, ws.max_row, 1, total_cols)
    for r in range(4, ws.max_row + 1):
        ws.row_dimensions[r].height = 24

    _autosize(ws)


def _details_for_protocol_rows(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        dj = str(r.get("details_json", "") or "").strip()
        d = {}
        if dj:
            try:
                d = json.loads(dj)
            except json.JSONDecodeError:
                d = {}
        out.append(d)
    return out


def _write_judge_pair_sheet(
    wb: Workbook,
    sheet_name: str,
    comp_name: str,
    discipline: str,
    stage_label: str,
    judge_name: str,
    pair_name: str,
    techniques: List[str],
    details: List[dict],
):
    ws = wb.create_sheet(sheet_name)
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.merge_cells("A1:K1")
    ws["A1"] = comp_name
    ws["A1"].font = Font(size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.merge_cells("C3:K3")
    ws["C3"] = f"{discipline}"
    ws["C3"].alignment = Alignment(horizontal="center")
    ws["C3"].font = Font(size=12, bold=True)

    logo = os.path.join(os.path.dirname(__file__), "static", "federation_logo.png")
    if os.path.exists(logo):
        try:
            from openpyxl.drawing.image import Image  # type: ignore
            img = Image(logo)
            img.width = 85
            img.height = 85
            ws.add_image(img, "I1")
        except Exception:
            pass

    ws["A4"] = "Дата:"
    ws["B4"] = datetime.now().strftime("%d.%m.%Y")
    ws["A5"] = "Этап:"
    ws["B5"] = stage_label
    ws["A6"] = "Судья:"
    ws["B6"] = judge_name
    ws["A7"] = "Пара:"
    ws["B7"] = pair_name
    for r in (4, 5, 6, 7):
        ws[f"A{r}"].font = Font(bold=True)

    # Групповой заголовок
    ws.merge_cells("D8:E8")
    ws["D8"] = "Малая ошибка"
    ws["F8"] = "Средняя"
    ws["G8"] = "Большая"
    ws["H8"] = "+/-"
    ws["I8"] = "Забытая"
    ws["J8"] = "SCORE"
    for c in ("D8", "F8", "G8", "H8", "I8", "J8"):
        ws[c].font = Font(bold=True, color="FFFFFF")
        ws[c].alignment = Alignment(horizontal="center", vertical="center")
    ws["D8"].fill = PatternFill(fill_type="solid", start_color="D06262", end_color="D06262")
    ws["E8"].fill = PatternFill(fill_type="solid", start_color="D06262", end_color="D06262")
    ws["F8"].fill = PatternFill(fill_type="solid", start_color="D08B62", end_color="D08B62")
    ws["G8"].fill = PatternFill(fill_type="solid", start_color="CF5A5A", end_color="CF5A5A")
    ws["H8"].fill = PatternFill(fill_type="solid", start_color="5A8BCF", end_color="5A8BCF")
    ws["I8"].fill = PatternFill(fill_type="solid", start_color="9D62CF", end_color="9D62CF")
    ws["J8"].fill = PatternFill(fill_type="solid", start_color="FFFFFF", end_color="FFFFFF")
    ws["J8"].font = Font(bold=True, color="1F2937")

    headers = [
        "№",
        "",
        "TECHNIQUES",
        "Малая 1",
        "Малая 2",
        "Средняя",
        "Большая",
        "+/-",
        "Забытая",
        "Счет",
        "",
    ]
    row_h = 9
    for i, h in enumerate(headers, 1):
        c = ws.cell(row_h, i)
        c.value = h
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill(fill_type="solid", start_color="2D5A7B", end_color="2D5A7B")
        c.alignment = Alignment(horizontal="center", vertical="center")

    # Базовые значения
    ws["D10"] = "-1"
    ws["E10"] = "-1"
    ws["F10"] = "-3"
    ws["G10"] = "-5"
    ws["H10"] = "±0.5"
    ws["I10"] = "-10"
    for col in "DEFGHI":
        ws[f"{col}10"].font = Font(italic=True)
        ws[f"{col}10"].alignment = Alignment(horizontal="center")

    m1 = m2 = med = big = pm = fgt = 0
    total_score = 0.0
    start = 11
    tech_fill_a = PatternFill(fill_type="solid", start_color="EAF2FF", end_color="EAF2FF")
    tech_fill_b = PatternFill(fill_type="solid", start_color="F4FAE8", end_color="F4FAE8")
    err_fills = {
        4: PatternFill(fill_type="solid", start_color="FFE8E8", end_color="FFE8E8"),  # m1
        5: PatternFill(fill_type="solid", start_color="FFE8E8", end_color="FFE8E8"),  # m2
        6: PatternFill(fill_type="solid", start_color="FFF4DF", end_color="FFF4DF"),  # med
        7: PatternFill(fill_type="solid", start_color="FFDCDC", end_color="FFDCDC"),  # big
        8: PatternFill(fill_type="solid", start_color="E9F7FF", end_color="E9F7FF"),  # +/-
        9: PatternFill(fill_type="solid", start_color="FDEBFF", end_color="FDEBFF"),  # forgotten
    }
    for idx, tech in enumerate(techniques, 1):
        d = details[idx - 1] if idx - 1 < len(details) else {}
        sc = _score_from_detail(d)
        total_score += sc

        ws.cell(start, 1).value = idx
        ws.cell(start, 3).value = tech
        ws.cell(start, 3).fill = tech_fill_a if idx % 2 else tech_fill_b

        if float(d.get("m1", 0) or 0) > 0:
            ws.cell(start, 4).value = "x"
            m1 += 1
        if float(d.get("m2", 0) or 0) > 0:
            ws.cell(start, 5).value = "x"
            m2 += 1
        if float(d.get("med", 0) or 0) > 0:
            ws.cell(start, 6).value = "x"
            med += 1
        if float(d.get("big", 0) or 0) > 0:
            ws.cell(start, 7).value = "x"
            big += 1

        cp = float(d.get("c_plus", 0) or 0)
        cm = float(d.get("c_minus", 0) or 0)
        if cp != 0 or cm != 0:
            ws.cell(start, 8).value = "+" if cp < 0 else "-"
            pm += 1

        if bool(d.get("forgotten", False)):
            ws.cell(start, 9).value = "x"
            fgt += 1

        ws.cell(start, 10).value = round(sc, 1)
        ws.cell(start, 10).fill = PatternFill(fill_type="solid", start_color="FFFFFF", end_color="FFFFFF")
        for cidx in [1, 4, 5, 6, 7, 8, 9, 10]:
            ws.cell(start, cidx).alignment = Alignment(horizontal="center")
        for cidx, f in err_fills.items():
            ws.cell(start, cidx).fill = f
        start += 1

    ws.cell(start, 3).value = "TOTAL"
    ws.cell(start, 3).font = Font(bold=True)
    ws.cell(start, 4).value = m1
    ws.cell(start, 5).value = m2
    ws.cell(start, 6).value = med
    ws.cell(start, 7).value = big
    ws.cell(start, 8).value = pm
    ws.cell(start, 9).value = fgt
    ws.cell(start, 10).value = round(total_score / 2.0 if fgt > 0 else total_score, 1)
    ws.cell(start, 10).fill = PatternFill(fill_type="solid", start_color="FFE08A", end_color="FFE08A")
    for cidx in [4, 5, 6, 7, 8, 9, 10]:
        ws.cell(start, cidx).font = Font(bold=True)
        ws.cell(start, cidx).alignment = Alignment(horizontal="center")

    # Обводка и вертикальные разделители
    _apply_grid(ws, 8, start, 1, 11)
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 3
    ws.column_dimensions["C"].width = 34
    for col in ["D", "E", "F", "G", "H", "I", "J", "K"]:
        ws.column_dimensions[col].width = 10
    for r in range(8, start + 1):
        ws.row_dimensions[r].height = 22

    _autosize(ws)


def _build_pdf(file_path: str, comp_name: str, discipline: str, stage_label: str, final_rows: List[dict], judge_pair_pages: List[Dict[str, Any]]):
    fn, fnb = _ensure_pdf_fonts()
    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], fontName=fnb, fontSize=16)
    body = ParagraphStyle("b", parent=styles["Normal"], fontName=fn, fontSize=9)

    doc = SimpleDocTemplate(file_path, pagesize=landscape(A4), rightMargin=10 * mm, leftMargin=10 * mm)
    story = [
        Paragraph(comp_name, title),
        Spacer(1, 2 * mm),
        Paragraph(f"{discipline} | {stage_label}", body),
        Spacer(1, 5 * mm),
    ]

    headers = ["Пара", "Тори", "Уке", "Сумма", "Место"]
    rows = []
    for r in final_rows:
        rows.append([
            str(r.get("номер пары", "")),
            _decode_participant_cell(r.get("Тори", ""))["name"],
            _decode_participant_cell(r.get("Уке", ""))["name"],
            str(r.get("Сумма", "")),
            str(r.get("Место", "")),
        ])
    t = Table([headers] + rows, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2D5A7B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), fnb),
        ("FONTNAME", (0, 1), (-1, -1), fn),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 6 * mm))

    for p in judge_pair_pages:
        story.append(Paragraph(f"Судья: {p['judge']} | Пара: {p['pair']}", body))
        story.append(Spacer(1, 2 * mm))
        dh = ["№", "Техника", "m1", "m2", "med", "big", "+/-", "forgotten", "score"]
        dr = p["rows"]
        td = Table([dh] + dr, repeatRows=1)
        td.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2D5A7B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), fnb),
            ("FONTNAME", (0, 1), (-1, -1), fn),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        story.append(td)
        story.append(Spacer(1, 5 * mm))

    doc.build(story)


def _collect_discipline_payload(comp_path: str, comp_display_name: str, dk: str, technique_map: Dict[str, List[str]]) -> Dict[str, Any]:
    cfg = _load_stage(comp_path, dk)
    stage = "final" if cfg.get("current_stage") == "final" else "prelim"
    stage_label = "ФИНАЛ" if stage == "final" else "ПРЕДВАРИТЕЛЬНЫЕ ВСТРЕЧИ"

    sp = _stage_paths(comp_path, dk, stage)
    pairs = _read_csv_rows(sp["pairs"])
    final_rows = _read_csv_rows(sp["final"])
    if not final_rows:
        # fallback
        final_rows = _read_csv_rows(os.path.join(comp_path, dk, "final_protocol.csv"))

    judges = _read_csv_rows(os.path.join(comp_path, dk, "judges_list.csv"))
    judges_sorted = sorted(judges, key=lambda x: int(str(x.get("место", 9999) or 9999)))
    judges_for_tablo = judges_sorted[:5]

    pair_by_slug = {}
    for p in pairs:
        t = str(p.get("Тори_ФИО", "")).replace(" ", "_")
        u = str(p.get("Уке_ФИО", "")).replace(" ", "_")
        pair_by_slug[f"{t}-{u}"] = p
        pair_by_slug[f"{u}-{t}"] = p

    judge_pair_pages = []
    protocols_dir = sp["protocols"] if os.path.isdir(sp["protocols"]) else os.path.join(comp_path, dk, "protocols")
    if os.path.isdir(protocols_dir):
        for pf in sorted(os.listdir(protocols_dir)):
            if not pf.endswith(".csv"):
                continue
            parts = pf[:-4].split("_", 2)
            if len(parts) < 3:
                continue
            jn, jp, slug = parts[0], parts[1], parts[2]
            pair = pair_by_slug.get(slug)
            if not pair:
                pair = _find_protocol_pair(pf, pairs)
            if not pair:
                continue
            pair_num = str(pair.get("номер пары", ""))
            pair_name = f"#{pair_num}: {pair.get('Тори_ФИО','')} - {pair.get('Уке_ФИО','')}"
            rows_csv = _read_csv_rows(os.path.join(protocols_dir, pf))
            details = _details_for_protocol_rows(rows_csv)
            techniques = technique_map.get(dk, [r.get("техника", "") for r in rows_csv])

            table_rows = []
            for i, tech in enumerate(techniques, 1):
                d = details[i - 1] if i - 1 < len(details) else {}
                cp = float(d.get("c_plus", 0) or 0)
                cm = float(d.get("c_minus", 0) or 0)
                pm = "+" if cp < 0 else ("-" if cm > 0 else "")
                table_rows.append([
                    i,
                    tech,
                    "x" if float(d.get("m1", 0) or 0) > 0 else "",
                    "x" if float(d.get("m2", 0) or 0) > 0 else "",
                    "x" if float(d.get("med", 0) or 0) > 0 else "",
                    "x" if float(d.get("big", 0) or 0) > 0 else "",
                    pm,
                    "x" if bool(d.get("forgotten", False)) else "",
                    round(_score_from_detail(d), 1),
                ])
            judge_pair_pages.append({
                "judge": f"{jn} (м. {jp})",
                "pair": pair_name,
                "rows": table_rows,
                "techniques": techniques,
                "details": details,
            })

    return {
        "discipline": dk,
        "stage": stage,
        "stage_label": stage_label,
        "comp_name": comp_display_name,
        "pairs": pairs,
        "judges": judges_for_tablo,
        "final_rows": final_rows,
        "judge_pair_pages": judge_pair_pages,
    }


def _save_discipline_protocol(comp_path: str, payload: Dict[str, Any]) -> Dict[str, str]:
    dk = payload["discipline"]
    stage_label = payload["stage_label"]
    name_base = f"{dk} protokol" + (" ФИНАЛ" if payload["stage"] == "final" else "")
    xlsx_path = os.path.join(comp_path, dk, f"{name_base}.xlsx")
    pdf_path = os.path.join(comp_path, dk, f"{name_base}.pdf")

    wb = Workbook()
    wb.remove(wb.active)
    _write_tablo_sheet(wb, payload["comp_name"], dk, stage_label, payload["final_rows"], payload["judges"])

    used = {"ТАБЛО"}
    for page in payload["judge_pair_pages"]:
        # имя листа: judge+pair
        raw = f"{page['judge']}_{page['pair']}"
        sn = _safe_sheet_name(raw, used)
        _write_judge_pair_sheet(
            wb,
            sn,
            payload["comp_name"],
            dk,
            stage_label,
            page["judge"],
            page["pair"],
            page["techniques"],
            page["details"],
        )

    wb.save(xlsx_path)

    # Финальный CSV с полной детализацией участников
    csv_path = os.path.join(comp_path, dk, f"{name_base}.csv")
    csv_headers = [
        "номер пары",
        "Тори",
        "Тори детали",
        "Уке",
        "Уке детали",
        "Сумма",
        "Место",
        "Судья 1",
        "Судья 2",
        "Судья 3",
        "Судья 4",
        "Судья 5",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_headers)
        w.writeheader()
        for r in payload["final_rows"]:
            td = _decode_participant_cell(r.get("Тори", ""))
            ud = _decode_participant_cell(r.get("Уке", ""))
            w.writerow({
                "номер пары": r.get("номер пары", ""),
                "Тори": td["name"],
                "Тори детали": td["detail"],
                "Уке": ud["name"],
                "Уке детали": ud["detail"],
                "Сумма": r.get("Сумма", ""),
                "Место": r.get("Место", ""),
                "Судья 1": r.get("Судья 1", ""),
                "Судья 2": r.get("Судья 2", ""),
                "Судья 3": r.get("Судья 3", ""),
                "Судья 4": r.get("Судья 4", ""),
                "Судья 5": r.get("Судья 5", ""),
            })

    _build_pdf(pdf_path, payload["comp_name"], dk, stage_label, payload["final_rows"], payload["judge_pair_pages"])
    return {"xlsx": xlsx_path, "pdf": pdf_path, "csv": csv_path}


def protocol_readiness(comp_path: str) -> dict:
    out = []
    for dk in _discipline_folders(comp_path):
        cfg = _load_stage(comp_path, dk)
        stage = "final" if cfg.get("current_stage") == "final" else "prelim"
        sp = _stage_paths(comp_path, dk, stage)
        rows = _read_csv_rows(sp["final"])
        with_sum = sum(1 for r in rows if str(r.get("Сумма", "") or "").strip())
        n_proto = 0
        if os.path.isdir(sp["protocols"]):
            n_proto = len([f for f in os.listdir(sp["protocols"]) if f.endswith(".csv")])
        out.append(
            {
                "key": dk,
                "pairs_registered": len(rows),
                "final_with_total": with_sum,
                "judge_protocol_files": n_proto,
                "ready": with_sum > 0,
            }
        )
    return {"disciplines": out, "overall_ready": any(x["ready"] for x in out) if out else False}


def generate_competition_protocols(
    comp_path: str,
    comp_name: str,
    discipline_key: Optional[str] = None,
    technique_map: Optional[Dict[str, List[str]]] = None,
) -> dict:
    try:
        cfg_file = os.path.join(comp_path, "config.json")
        if not os.path.exists(cfg_file):
            return {"success": False, "message": "Конфигурация соревнования не найдена"}
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        comp_display_name = cfg.get("name", comp_name)

        tmap = technique_map or {}
        dlist = [discipline_key] if discipline_key else _discipline_folders(comp_path)
        if not dlist:
            return {"success": False, "message": "Нет дисциплин для генерации"}

        generated: Dict[str, Any] = {"disciplines": {}, "results": []}
        for dk in dlist:
            if dk not in _discipline_folders(comp_path):
                continue
            payload = _collect_discipline_payload(comp_path, comp_display_name, dk, tmap)
            files = _save_discipline_protocol(comp_path, payload)
            generated["disciplines"][dk] = [files["xlsx"], files["pdf"], files["csv"]]

        # итоги в results/: только ссылки на дисциплинарные файлы (как индекс)
        results_dir = os.path.join(comp_path, "results")
        os.makedirs(results_dir, exist_ok=True)
        index_xlsx = os.path.join(results_dir, "results_index.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "Results"
        ws.append(["Дисциплина", "Файлы"]) 
        for dk, files in generated["disciplines"].items():
            ws.append([dk, " ; ".join(files)])
        wb.save(index_xlsx)
        generated["results"] = [index_xlsx]

        return {"success": True, "generated": generated}
    except Exception as e:
        return {"success": False, "message": f"Ошибка при генерации протоколов: {e}"}
