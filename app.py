# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import csv
import io
from typing import Dict
from datetime import datetime
from csv_manager import (
    CSVManager, CompetitionCSVManager, sort_prelim_results_for_final_transfer, _csv_lock,
    extract_birth_year, format_birth_year, birth_year_upper_limit, BIRTH_YEAR_MIN,
)
from scoring import calculate_pair_final_score
import json

# Импортируем DISCIPLINE_ROWS_BY_KEY из technics.py
from technics import DISCIPLINE_ROWS_BY_KEY
from generate_protocols import generate_competition_protocols, protocol_readiness

# Функция для получения красивого названия дисциплины
def get_discipline_display_name(key):
    """Получает красивое название дисциплины"""
    display_names = {
        'nagenokata': 'Nage-no-kata',
        'katamenokata': 'Katame-no-kata',
        'kimenokata': 'Kime-no-kata',
        'junokata': 'Ju-no-kata',
        'kodokangoshinjutsu': 'Kodokan Goshin-jutsu',
        'koshikinokata': 'Koshiki-no-kata',
        'itsutsunokata': 'Itsutsu-no-kata',
    }
    return display_names.get(key.lower(), key)


MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
}


def format_date_ru(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_RU.get(dt.month, '')} {dt.year} г."


def parse_iso_date(s):
    """Безопасно разбирает 'ГГГГ-ММ-ДД' в date; None при ошибке."""
    from datetime import date as _date
    try:
        y, m, d = str(s).strip().split('-')
        return _date(int(y), int(m), int(d))
    except Exception:
        return None


def format_date_range_ru(start_s, end_s) -> str:
    """Одна дата или период ('15 марта 2026 г.', '3 – 5 апреля 2026 г.'). '' если пусто."""
    d1 = parse_iso_date(start_s) if start_s else None
    d2 = parse_iso_date(end_s) if end_s else None
    if d1 and d2 and d2 > d1:
        if d1.month == d2.month and d1.year == d2.year:
            return f"{d1.day}–{d2.day} {MONTHS_RU.get(d2.month, '')} {d2.year} г."
        return f"{format_date_ru(d1)} — {format_date_ru(d2)}"
    if d1:
        return format_date_ru(d1)
    if d2:
        return format_date_ru(d2)
    return ''


def get_competition_dates_label(config: dict) -> str:
    """Подпись даты соревнования из config.json (event_start / event_end)."""
    return format_date_range_ru(config.get('event_start', ''), config.get('event_end', ''))


# ============ Возрастная категория (субтайтл табло) ============

SUBTITLE_MAX_LEN = 120


def get_effective_subtitle(comp_path: str, kata_key: str, config: dict = None) -> str:
    """Возрастная категория для табло: уровень дисциплины/этапа важнее уровня соревнования."""
    text = ''
    if config is None:
        config_file = os.path.join(comp_path, 'config.json')
        config = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f) or {}
            except Exception:
                config = {}
    comp_sub = str(config.get('age_category', '') or '').strip()
    if comp_sub:
        text = comp_sub
    try:
        stage_cfg = ensure_stage_config(comp_path, kata_key)
        disc_sub = str(stage_cfg.get('age_category', '') or '').strip()
        if disc_sub:
            text = disc_sub
    except Exception:
        pass
    return text[:SUBTITLE_MAX_LEN]


# ============ Число оцениваемых техник (techniques_count) ============

TECHNIQUES_COUNT_MIN = 5
TECHNIQUES_COUNT_MAX = 10


def clamp_techniques_count(value, total: int):
    """Возвращает n в допустом интерфейсом диапазоне [max(5,min(10,total)) .. min(10,total)]
    либо None, если значение не задано (используется полное число техник ката)."""
    if value is None or value == '':
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    upper = min(TECHNIQUES_COUNT_MAX, max(total, 1))
    lower = min(TECHNIQUES_COUNT_MIN, upper)
    return max(lower, min(upper, n))


def validate_techniques_count_input(value, total: int):
    """Проверка пользовательского ввода числа техник.

    Возвращает (n, error): n — принятое значение или None; error — текст причины отказа.
    Диапазон: от TECHNIQUES_COUNT_MIN до фактического числа техник ката (по ТЗ — «не больше 15»).
    """
    if value is None or str(value).strip() == '':
        return None, 'Укажите количество техник'
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None, 'Количество техник должно быть целым числом'
    if n < TECHNIQUES_COUNT_MIN:
        return None, f'Нельзя указать меньше {TECHNIQUES_COUNT_MIN} техник'
    if n > total:
        return None, f'В этом ката всего {total} техник — значение не может быть больше'
    return n, ''


def get_stage_techniques_count(comp_path: str, kata_key: str, stage: str) -> int:
    """Число оцениваемых техник для этапа (квалификация/финал отдельно).
    Ограничение включается флагом techniques_limit_enabled; если флаг выключен
    или значение не задано — полное число техник ката."""
    cfg = ensure_stage_config(comp_path, kata_key)
    total = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
    if not cfg.get('techniques_limit_enabled'):
        return total
    raw = cfg.get('techniques_count_final') if stage == 'final' else cfg.get('techniques_count_prelim')
    n = clamp_techniques_count(raw, total)
    if n is None:
        return total
    return n


def effective_techniques(comp_path: str, kata_key: str, stage: str) -> list:
    """Первые n оцениваемых техник ката для текущего этапа (n = techniques_count)."""
    all_tech = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    n = get_stage_techniques_count(comp_path, kata_key, stage)
    return all_tech[:n] if n else all_tech


def limit_technique_rows(comp_path: str, kata_key: str, stage: str, rows: list) -> list:
    """Единственная общая точка отсечения: оставляет только первые n записей техник
    (техника_№ <= n). При выключенном переключателе возвращает все строки как есть."""
    total = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
    n = get_stage_techniques_count(comp_path, kata_key, stage)
    if not n or n >= total or not isinstance(rows, list):
        return rows
    kept = []
    for idx, row in enumerate(rows):
        num = None
        try:
            num = int(str(row.get('техника_№', '')).strip() or 0) or None
        except (TypeError, ValueError, AttributeError):
            num = None
        if num is None:
            num = idx + 1
        if num <= n:
            kept.append(row)
    return kept


def read_stage_techniques_count_raw(comp_path: str, kata_key: str) -> dict:
    cfg = ensure_stage_config(comp_path, kata_key)
    return {
        'prelim': cfg.get('techniques_count_prelim'),
        'final': cfg.get('techniques_count_final'),
        'enabled': bool(cfg.get('techniques_limit_enabled')),
    }


def stage_data_fingerprint(comp_path: str, kata_key: str) -> float:
    """Модификации ключевых файлов этапа (для кеша и дешёвой проверки изменений табло)."""
    latest = 0.0
    try:
        files = get_stage_files(comp_path, kata_key, stage_for_ops(comp_path, kata_key))
        for key in ('participants', 'final_protocol'):
            p = files.get(key)
            if p and os.path.exists(p):
                latest = max(latest, os.path.getmtime(p))
        disc_cfg = _stage_config_path(comp_path, kata_key)
        if os.path.exists(disc_cfg):
            latest = max(latest, os.path.getmtime(disc_cfg))
        comp_cfg = os.path.join(comp_path, 'config.json')
        if os.path.exists(comp_cfg):
            latest = max(latest, os.path.getmtime(comp_cfg))
        protocols_dir = os.path.join(os.path.dirname(files['participants']), 'protocols')
        if os.path.isdir(protocols_dir):
            for fn in os.listdir(protocols_dir):
                if fn.endswith('.csv'):
                    try:
                        latest = max(latest, os.path.getmtime(os.path.join(protocols_dir, fn)))
                    except OSError:
                        pass
    except Exception:
        pass
    return latest


def has_submitted_scores(comp_path: str, kata_key: str, stage: str) -> bool:
    """Есть ли сданные оценки судей на этапе (файлы протоколов или заполненный итог)."""
    stage_files = get_stage_files(comp_path, kata_key, stage)
    protocols_dir = os.path.join(os.path.dirname(stage_files['participants']), 'protocols')
    if os.path.isdir(protocols_dir):
        for fn in os.listdir(protocols_dir):
            if not fn.endswith('.csv'):
                continue
            rows = CSVManager.read_csv(os.path.join(protocols_dir, fn))
            for r in rows:
                dj = str(r.get('details_json', '') or '').strip()
                if dj and dj not in ('{}', 'null'):
                    try:
                        d = json.loads(dj)
                    except json.JSONDecodeError:
                        continue
                    if any(float(d.get(k, 0) or 0) for k in ('m1', 'm2', 'med', 'big', 'c_minus', 'c_plus')) \
                            or bool(d.get('forgotten', False)):
                        return True
    try:
        fp = stage_files['final_protocol']
        if os.path.exists(fp):
            for row in CSVManager.read_csv(fp):
                if str(row.get('Сумма', '')).strip():
                    return True
    except Exception:
        pass
    return False


def _stage_config_path(comp_path: str, kata_key: str) -> str:
    return os.path.join(comp_path, kata_key, 'stage.json')


def ensure_stage_config(comp_path: str, kata_key: str) -> dict:
    disc_path = os.path.join(comp_path, kata_key)
    os.makedirs(disc_path, exist_ok=True)
    cfg_path = _stage_config_path(comp_path, kata_key)
    cfg = {
        'mode': 'final_only',
        'current_stage': 'final',
        'status': 'open',
        'final_top_n': 3,
        'age_category': '',
        'techniques_limit_enabled': False,
        'techniques_count_prelim': None,
        'techniques_count_final': None,
    }
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f) or {}
            cfg.update(loaded)
        except Exception:
            loaded = {}
    # Обратная совместимость: раньше ограничение считалось включённым,
    # если значение techniques_count было задано явно.
    if isinstance(loaded, dict) and 'techniques_limit_enabled' not in loaded:
        if cfg.get('techniques_count_prelim') is not None or cfg.get('techniques_count_final') is not None:
            cfg['techniques_limit_enabled'] = True
    cfg['techniques_limit_enabled'] = bool(cfg.get('techniques_limit_enabled'))
    # Нормализация: пустые/некорректные значения techniques_count -> None (все техники).
    # Сохраняем сырое значение, если оно вне диапазона, — интерфейс подсветит причину,
    # но в расчётах такой этап использует полное число техник.
    for tc_key in ('techniques_count_prelim', 'techniques_count_final'):
        total = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
        raw = cfg.get(tc_key)
        if raw is None or raw == '':
            cfg[tc_key] = None
            continue
        try:
            n_raw = int(str(raw).strip())
        except (TypeError, ValueError):
            cfg[tc_key] = None
            continue
        if 1 <= n_raw <= total:
            cfg[tc_key] = n_raw
        else:
            cfg[tc_key] = None
    # Ensure stage files exist:
    # prelim -> root discipline files; final -> subfolder final/
    CompetitionCSVManager.create_discipline_structure(comp_path, kata_key)
    disc_path = os.path.join(comp_path, kata_key)
    CSVManager.ensure_csv_exists(os.path.join(disc_path, 'participants_list.csv'), CompetitionCSVManager.PAIRS_HEADERS)
    CSVManager.ensure_csv_exists(os.path.join(disc_path, 'final_protocol.csv'), CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    final_dir = os.path.join(disc_path, 'final')
    os.makedirs(os.path.join(final_dir, 'protocols'), exist_ok=True)
    CSVManager.ensure_csv_exists(os.path.join(final_dir, 'participants_list.csv'), CompetitionCSVManager.PAIRS_HEADERS)
    CSVManager.ensure_csv_exists(os.path.join(final_dir, 'final_protocol.csv'), CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)

    # <=3 пар -> только прямой финал
    try:
        root_pairs = CSVManager.read_csv(os.path.join(disc_path, 'participants_list.csv'))
        if len(root_pairs) <= 3 and len(root_pairs) > 0:
            cfg['mode'] = 'final_only'
            cfg['current_stage'] = 'final'
    except Exception:
        pass
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def stage_for_ops(comp_path: str, kata_key: str) -> str:
    cfg = ensure_stage_config(comp_path, kata_key)
    return 'final' if cfg.get('current_stage') == 'final' else 'prelim'


def get_stage_files(comp_path: str, kata_key: str, stage: str) -> dict:
    disc_path = os.path.join(comp_path, kata_key)
    is_final = str(stage).lower() == 'final'
    if is_final:
        base = os.path.join(disc_path, 'final')
    else:
        base = disc_path
    return {
        'participants': os.path.join(base, 'participants_list.csv'),
        'final_protocol': os.path.join(base, 'final_protocol.csv'),
    }


def judge_positions_meta(judges: list) -> dict:
    positions = []
    for j in judges:
        try:
            p = int(str(j.get('место', '')).strip())
            if p > 0:
                positions.append(p)
        except Exception:
            continue
    unique_positions = sorted(set(positions))
    n = len(unique_positions)
    if n < 3:
        return {'valid': False, 'error': 'Минимум 3 судьи', 'positions': [], 'effective_positions': [], 'effective_count': 0}
    eff = unique_positions[:5]
    return {'valid': True, 'error': '', 'positions': unique_positions, 'effective_positions': eff, 'effective_count': len(eff)}


def _compute_final_from_entry(pair_entry: dict, effective_positions: list) -> float:
    scores = []
    for p in effective_positions:
        if p < 1 or p > 5:
            continue
        s = pair_entry.get(f'Судья {p}', '')
        if s in (None, ''):
            return None
        try:
            scores.append(float(s))
        except ValueError:
            return None
    return calculate_pair_final_score(scores, judge_count=len(effective_positions))


def _participant_detail_line(pair_row: dict, prefix: str) -> str:
    """prefix: 'Тори_' или 'Уке_'. Хранится только год рождения — выводим 4 цифры."""
    year = extract_birth_year(pair_row.get(f'{prefix}год рождения', ''))
    parts = [
        str(year) if year is not None else '',
        pair_row.get(f'{prefix}разряд', '').strip(),
        pair_row.get(f'{prefix}кю', '').strip(),
        pair_row.get(f'{prefix}СШ', '').strip(),
        pair_row.get(f'{prefix}тренер', '').strip(),
    ]
    return ', '.join(p for p in parts if p)


def encode_participant_for_protocol(pair_row: dict, role: str) -> str:
    """role: 'Тори' или 'Уке'. В CSV: Имя||остальное через запятую"""
    prefix = f'{role}_'
    name = pair_row.get(f'{prefix}ФИО', '').strip()
    detail = _participant_detail_line(pair_row, prefix)
    if detail:
        return f'{name}||{detail}'
    return name


def decode_participant_cell(cell_value) -> dict:
    if cell_value is None:
        return {'name': '', 'detail': ''}
    s = str(cell_value).strip()
    if '||' in s:
        name, _, rest = s.partition('||')
        return {'name': name.strip(), 'detail': rest.strip()}
    return {'name': s, 'detail': ''}


def enrich_result_row_cells(result: dict, pair_row: dict = None) -> None:
    """Тори/Уке для табло: ФИО и детали (год, разряд, кю, СШ, тренер) отдельными полями."""
    if pair_row:
        result['tori_cell'] = {
            'name': pair_row.get('Тори_ФИО', '').strip(),
            'detail': _participant_detail_line(pair_row, 'Тори_'),
        }
        result['uke_cell'] = {
            'name': pair_row.get('Уке_ФИО', '').strip(),
            'detail': _participant_detail_line(pair_row, 'Уке_'),
        }
    else:
        result['tori_cell'] = decode_participant_cell(result.get('tori', ''))
        result['uke_cell'] = decode_participant_cell(result.get('uke', ''))
    # Совместимость: единая строка "Имя, детали"
    for role in ('tori', 'uke'):
        cell = result[f'{role}_cell']
        result[role] = f"{cell['name']}, {cell['detail']}".strip(', ') if cell.get('detail') else cell.get('name', '')


def judge_score_cell_style(score) -> dict:
    if score is None:
        return {'background': 'transparent', 'color': 'inherit'}
    try:
        v = float(score)
    except (TypeError, ValueError):
        return {'background': 'transparent', 'color': 'inherit'}
    t = max(0.0, min(1.0, v / 170.0))
    r = int(round(255 * (1 - t)))
    g = int(round(255 * t))
    b = 32
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    fg = '#0b1320' if lum > 0.62 else '#f8fafc'
    return {'background': f'rgb({r},{g},{b})', 'color': fg}


def tablo_sort_and_assign_places(results: list) -> list:
    """Места только у пар с полной суммой; сортировка: по месту (лучшие выше), без месты — по номеру пары."""
    ranked = [r for r in results if r.get('final_score') is not None]
    unranked = [r for r in results if r.get('final_score') is None]
    ranked.sort(key=lambda x: (-x['final_score'], x['pair_number']))
    for i, r in enumerate(ranked):
        r['place'] = i + 1
    for r in unranked:
        r['place'] = None
    unranked.sort(key=lambda x: x['pair_number'])
    return ranked + unranked


def prepare_tablo_results(results: list, pairs: list) -> list:
    pairs_by_num = {int(p.get('номер пары', 0)): p for p in pairs}
    out = tablo_sort_and_assign_places(results)
    for r in out:
        pr = pairs_by_num.get(r['pair_number'])
        enrich_result_row_cells(r, pr)
        r['judge_cell_styles'] = [judge_score_cell_style(s) for s in r.get('judge_scores', [])]
    return out


# ==================== ТЕМЫ (единый источник для base.html и табло) ====================

THEME_NAMES = [
    ('default', 'Стандартная'),
    ('dark', 'Тёмная'),
    ('light', 'Светлая'),
    ('pistachio', 'Фисташковая'),
    ('ocean', 'Океан'),
    ('sunset', 'Закат'),
]
VALID_THEMES = {k for k, _ in THEME_NAMES}
THEME_STORAGE_KEY = 'kata_judge_theme'

# CSS-переменные тем. Обязательные ключи (все темы): --tablo-head-bg, --tablo-title-color,
# --tablo-sub-color, --tablo-category-color, --tablo-date-color, --tablo-sep-color,
# --tablo-part-color, --tablo-th-color, --tablo-name-color, --tablo-extra-color,
# --tablo-sum-color, --tablo-place-top3-color, --tablo-place-other-color,
# --tablo-empty-color, --tablo-flat-score-color, --tablo-gradient-text-dark,
# --tablo-gradient-text-light, --tablo-toggle-border.
# Опциональные (есть не во всех темах): --surface-1, --surface-2, --hover-accent, --hover-accent-text.
# Значения подобраны с контрастом к фону не ниже WCAG AA (4.5:1).
THEME_CSS_VARS = {
    'default': """
        html[data-theme="default"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.14);
            --tablo-title-color: #ffffff;
            --tablo-sub-color: #ffe9a8;
            --tablo-category-color: #ffffff;
            --tablo-date-color: #eaf2fb;
            --tablo-sep-color: rgba(255, 255, 255, 0.45);
            --tablo-part-color: #f2f6fc;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #14314b;
            --tablo-place-top3-color: #a51218;
            --tablo-place-other-color: #1f2937;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #1d6b45;
            --tablo-gradient-text-dark: #1b2a1f;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: #d7dbe0;
        }
""",
    'dark': """
        html[data-theme="dark"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.05);
            --tablo-title-color: #f2f6fc;
            --tablo-sub-color: #ffd166;
            --tablo-category-color: #e8eef6;
            --tablo-date-color: #cfd9e6;
            --tablo-sep-color: rgba(255, 255, 255, 0.25);
            --tablo-part-color: #cfd9e6;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #ffd166;
            --tablo-place-top3-color: #ff8a8a;
            --tablo-place-other-color: #e5e7eb;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #7ee2b8;
            --tablo-gradient-text-dark: #0b1320;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: rgba(255, 255, 255, 0.18);
        }
""",
    'light': """
        html[data-theme="light"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.78);
            --tablo-title-color: #10233c;
            --tablo-sub-color: #1c3f6e;
            --tablo-category-color: #22303f;
            --tablo-date-color: #33465c;
            --tablo-sep-color: rgba(42, 90, 166, 0.35);
            --tablo-part-color: #33465c;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #1d4e89;
            --tablo-place-top3-color: #b3000e;
            --tablo-place-other-color: #1f2937;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #1d6b45;
            --tablo-gradient-text-dark: #1b2a1f;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: rgba(15, 23, 32, 0.18);
        }
""",
    'pistachio': """
        html[data-theme="pistachio"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.65);
            --tablo-title-color: #122b1e;
            --tablo-sub-color: #1f4d3a;
            --tablo-category-color: #1c3527;
            --tablo-date-color: #2c4636;
            --tablo-sep-color: rgba(31, 77, 58, 0.35);
            --tablo-part-color: #2c4636;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #1e5b40;
            --tablo-place-top3-color: #a3122a;
            --tablo-place-other-color: #173022;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #1c5c3f;
            --tablo-gradient-text-dark: #1b2a1f;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: rgba(31, 77, 58, 0.25);
        }
""",
    'ocean': """
        html[data-theme="ocean"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.62);
            --tablo-title-color: #052733;
            --tablo-sub-color: #0b4b5e;
            --tablo-category-color: #0a3040;
            --tablo-date-color: #14485c;
            --tablo-sep-color: rgba(11, 114, 133, 0.4);
            --tablo-part-color: #14485c;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #0a5c70;
            --tablo-place-top3-color: #a3122a;
            --tablo-place-other-color: #06212a;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #0c6b53;
            --tablo-gradient-text-dark: #1b2a1f;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: rgba(6, 33, 42, 0.2);
        }
""",
    'sunset': """
        html[data-theme="sunset"] {
            --tablo-head-bg: rgba(255, 255, 255, 0.6);
            --tablo-title-color: #320f26;
            --tablo-sub-color: #6e1f45;
            --tablo-category-color: #45132f;
            --tablo-date-color: #5a2a44;
            --tablo-sep-color: rgba(166, 58, 90, 0.4);
            --tablo-part-color: #5a2a44;
            --tablo-th-color: #ffffff;
            --tablo-name-color: var(--page-text);
            --tablo-extra-color: var(--muted-text);
            --tablo-sum-color: #8a2f52;
            --tablo-place-top3-color: #b3000e;
            --tablo-place-other-color: #2a0f21;
            --tablo-empty-color: var(--muted-text);
            --tablo-flat-score-color: #1d6b45;
            --tablo-gradient-text-dark: #1b2a1f;
            --tablo-gradient-text-light: #f8fafc;
            --tablo-toggle-border: rgba(74, 31, 59, 0.22);
        }
""",
}


def build_themes_css() -> str:
    parts = []
    for key, _name in THEME_NAMES:
        parts.append(THEME_CSS_VARS[key])
    return '\n'.join(parts)


app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = 'your_secret_key_here_change_in_production'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True


@app.context_processor
def inject_theme_globals():
    return {
        'THEME_NAMES': THEME_NAMES,
        'THEME_STORAGE_KEY': THEME_STORAGE_KEY,
        'themes_css': build_themes_css(),
    }


def _load_comp_config(comp_path: str) -> dict:
    config_file = os.path.join(comp_path, 'config.json')
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


def save_competition_config(comp_path: str, config: dict) -> bool:
    try:
        with open(os.path.join(comp_path, 'config.json'), 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# Инициализация SocketIO
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    manage_session=False,
    async_mode='threading',
    logger=True,
    engineio_logger=True
)


def broadcast_tablo_update(comp_name: str, discipline_key: str = '') -> None:
    """Уведомляет открытые табло об изменении настроек (название/дата/категория)."""
    try:
        socketio.emit('tablo_update', {
            'comp_name': comp_name,
            'discipline_key': discipline_key,
            'changed': True,
        })
    except Exception:
        pass


# ==================== КЕШ ТАБЛО И ОПТИМИЗАЦИЯ РАБОТЫ ====================

TABLO_CACHE_TTL = 20.0            # секунд — кеш собранного HTML табло (инвалидируется по mtime и событиями)
TABLO_POLL_INTERVAL_MS = 15000    # интервал опроса изменений табло клиентом (10–30 с)
_tablo_cache = {}                 # key -> {'fp': fingerprint, 'html': str, 'ts': float}
_tablo_mutex = threading.Lock()


def tablo_cached_html(comp_path: str, kata_key: str, builder) -> str:
    """Возвращает HTML табло из кеша, если файлы этапа не менялись (дешёвая проверка mtime).

    builder — callable без аргументов, собирающий HTML при промахе кеша.
    """
    key = f"{comp_path}|{kata_key}"
    fp = stage_data_fingerprint(comp_path, kata_key)
    now = time.time()
    with _tablo_mutex:
        entry = _tablo_cache.get(key)
        if entry and entry['fp'] == fp and (now - entry['ts']) < TABLO_CACHE_TTL:
            return entry['html']
    html = builder()
    with _tablo_mutex:
        _tablo_cache[key] = {
            'fp': stage_data_fingerprint(comp_path, kata_key),
            'html': html,
            'ts': time.time(),
        }
    return html


def invalidate_tablo_cache(comp_name: str, kata_key: str = '') -> None:
    """Сбрасывает кеш табло и рассылает всем открытым табло событие обновиться."""
    prefix = f"{os.path.join(COMPETITIONS_BASE_DIR, comp_name)}|"
    with _tablo_mutex:
        for k in [k for k in _tablo_cache
                  if k.startswith(prefix) and (not kata_key or k.endswith('|' + kata_key))]:
            _tablo_cache.pop(k, None)
    broadcast_tablo_update(comp_name, kata_key)

# Глобальные пути
GLOBAL_DATA_DIR = os.path.dirname(__file__)
PARTICIPANTS_CSV = os.path.join(GLOBAL_DATA_DIR, 'participants.csv')
JUDGES_CSV = os.path.join(GLOBAL_DATA_DIR, 'judges.csv')
COMPETITIONS_BASE_DIR = os.path.join(GLOBAL_DATA_DIR, 'competitions')

# Инициализация глобальных CSV файлов
CSVManager.ensure_csv_exists(PARTICIPANTS_CSV, CompetitionCSVManager.PARTICIPANTS_HEADERS)
CSVManager.ensure_csv_exists(JUDGES_CSV, CompetitionCSVManager.JUDGES_HEADERS)
os.makedirs(COMPETITIONS_BASE_DIR, exist_ok=True)

# Простая аутентификация для админки
ADMIN_PASSWORD = 'admin123'


# ==================== СТАТИЧЕСКИЕ ФАЙЛЫ ====================

@app.route('/competitions/<path:filename>')
def serve_competition_files(filename):
    """Служить файлы из папки competitions"""
    filepath = os.path.join(COMPETITIONS_BASE_DIR, filename)
    # Проверяем, что путь находится внутри COMPETITIONS_BASE_DIR
    if os.path.abspath(filepath).startswith(os.path.abspath(COMPETITIONS_BASE_DIR)):
        if os.path.exists(filepath):
            from flask import send_file
            return send_file(filepath)
    return redirect(url_for('public_dashboard'))


# ==================== АДМИНИСТРАТИВНАЯ ПАНЕЛЬ ====================

@app.route('/')
def index():
    """Главная страница — публичная панель со списком активных турниров"""
    return public_dashboard()


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Вход в административную панель"""
    if request.method == 'POST':
        password = request.form['password']
        if password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Неверный пароль', 'danger')
    return render_template('login.html')


@app.route('/admin/logout')
def admin_logout():
    """Выход из административной панели"""
    session.pop('admin', None)
    return redirect(url_for('public_dashboard'))


@app.route('/dashboard/admin')
def admin_dashboard():
    """Главная панель администратора"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    # Получаем список соревнований
    competitions = []
    comp_display_names = {}
    if os.path.exists(COMPETITIONS_BASE_DIR):
        for comp_folder in os.listdir(COMPETITIONS_BASE_DIR):
            comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_folder)
            if os.path.isdir(comp_path):
                competitions.append(comp_folder)
                # Красивое название из config.json (если есть)
                config_file = os.path.join(comp_path, 'config.json')
                try:
                    if os.path.exists(config_file):
                        with open(config_file, 'r', encoding='utf-8') as f:
                            cfg = json.load(f)
                        comp_display_names[comp_folder] = cfg.get('name', comp_folder)
                except (json.JSONDecodeError, OSError):
                    comp_display_names[comp_folder] = comp_folder

    competitions.sort(reverse=True)
    return render_template('admin_dashboard.html', competitions=competitions, comp_display_names=comp_display_names)


@app.route('/data-editor')
def data_editor():
    """Веб-редактор данных (реестры участников и судей)"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    return render_template('data_editor.html')


# ---------- API редактора данных ----------

_PARTICIPANTS_HEADERS = ['ФИО', 'год рождения', 'разряд', 'кю', 'СШ', 'тренер']
_JUDGES_HEADERS = ['ФИО']


def _rows_with_ids(rows):
    """Добавляет стабильный числовой id к строкам CSV (порядковый номер)."""
    return [dict(row, id=i + 1) for i, row in enumerate(rows)]


@app.route('/api/data/participants', methods=['GET'])
def api_data_participants_list():
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    search = (request.args.get('search') or '').strip().lower()
    rows = CSVManager.read_csv(PARTICIPANTS_CSV)
    if search:
        rows = [r for r in rows
                if search in (r.get('ФИО', '') or '').lower()
                or search in (r.get('год рождения', '') or '')]
    return jsonify(_rows_with_ids(rows))


@app.route('/api/data/participants', methods=['POST'])
def api_data_participants_add():
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json(silent=True) or {}
    fio = (data.get('ФИО') or '').strip()
    birth_year = str(data.get('год рождения') or '').strip()
    if not fio or not birth_year:
        return jsonify({'success': False, 'error': 'Заполните ФИО и год рождения'})
    row = {
        'ФИО': fio,
        'год рождения': birth_year,
        'разряд': (data.get('разряд') or '').strip(),
        'кю': (data.get('кю') or '').strip(),
        'СШ': (data.get('СШ') or '').strip(),
        'тренер': (data.get('тренер') or '').strip(),
    }
    try:
        with _csv_lock:
            existing = CSVManager.read_csv(PARTICIPANTS_CSV)
            for e in existing:
                if (e.get('ФИО', '').strip().lower() == fio.lower()
                        and e.get('год рождения', '').strip() == birth_year):
                    return jsonify({'success': False,
                                    'error': 'Участник с таким ФИО и годом рождения уже есть'})
            CSVManager.add_row(PARTICIPANTS_CSV, row, _PARTICIPANTS_HEADERS)
        return jsonify({'success': True})
    except Exception as exc:
        app.logger.error('api_data_participants_add: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/participants/<int:row_id>', methods=['PUT'])
def api_data_participants_update(row_id):
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json(silent=True) or {}
    fio = (data.get('ФИО') or '').strip()
    if not fio:
        return jsonify({'success': False, 'error': 'ФИО обязательно'})
    try:
        with _csv_lock:
            rows = CSVManager.read_csv(PARTICIPANTS_CSV)
            if not (1 <= row_id <= len(rows)):
                return jsonify({'success': False, 'error': 'Запись не найдена'})
            old = rows[row_id - 1]
            rows[row_id - 1] = {
                'ФИО': fio,
                'год рождения': str(data.get('год рождения') or old.get('год рождения', '')).strip(),
                'разряд': (data.get('разряд') or '').strip(),
                'кю': (data.get('кю') or '').strip(),
                'СШ': (data.get('СШ') or '').strip(),
                'тренер': (data.get('тренер') or '').strip(),
            }
            CSVManager.write_csv(PARTICIPANTS_CSV, rows, _PARTICIPANTS_HEADERS)
        return jsonify({'success': True})
    except Exception as exc:
        app.logger.error('api_data_participants_update: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/participants/<int:row_id>', methods=['DELETE'])
def api_data_participants_delete(row_id):
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    try:
        with _csv_lock:
            rows = CSVManager.read_csv(PARTICIPANTS_CSV)
            if not (1 <= row_id <= len(rows)):
                return jsonify({'success': False, 'error': 'Запись не найдена'})
            del rows[row_id - 1]
            CSVManager.write_csv(PARTICIPANTS_CSV, rows, _PARTICIPANTS_HEADERS)
        return jsonify({'success': True})
    except Exception as exc:
        app.logger.error('api_data_participants_delete: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/judges', methods=['GET'])
def api_data_judges_list():
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    search = (request.args.get('search') or '').strip().lower()
    rows = CSVManager.read_csv(JUDGES_CSV)
    if search:
        rows = [r for r in rows if search in (r.get('ФИО', '') or '').lower()]
    return jsonify(_rows_with_ids(rows))


@app.route('/api/data/judges', methods=['POST'])
def api_data_judges_add():
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    data = request.get_json(silent=True) or {}
    fio = (data.get('ФИО') or '').strip()
    if not fio:
        return jsonify({'success': False, 'error': 'Заполните ФИО'})
    try:
        with _csv_lock:
            existing = CSVManager.read_csv(JUDGES_CSV)
            for e in existing:
                if e.get('ФИО', '').strip().lower() == fio.lower():
                    return jsonify({'success': False, 'error': 'Судья с таким ФИО уже есть'})
            CSVManager.add_row(JUDGES_CSV, {'ФИО': fio}, _JUDGES_HEADERS)
        return jsonify({'success': True})
    except Exception as exc:
        app.logger.error('api_data_judges_add: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/judges/<int:row_id>', methods=['DELETE'])
def api_data_judges_delete(row_id):
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    try:
        with _csv_lock:
            rows = CSVManager.read_csv(JUDGES_CSV)
            if not (1 <= row_id <= len(rows)):
                return jsonify({'success': False, 'error': 'Запись не найдена'})
            del rows[row_id - 1]
            CSVManager.write_csv(JUDGES_CSV, rows, _JUDGES_HEADERS)
        return jsonify({'success': True})
    except Exception as exc:
        app.logger.error('api_data_judges_delete: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


# ---------- Импорт базы участников из CSV ----------

_IMPORT_ALIASES = {
    'фио': 'ФИО', 'ф': 'ФИО', 'ф.и.о': 'ФИО', 'name': 'ФИО', 'fio': 'ФИО',
    'год': 'год рождения', 'год рождения': 'год рождения', 'года рождения': 'год рождения',
    'год рожд': 'год рождения', 'birth_year': 'год рождения', 'birthyear': 'год рождения',
    'рождение': 'год рождения', 'возраст': 'год рождения',
    'дата': 'год рождения', 'дата рождения': 'год рождения', 'date': 'год рождения',
    'birthdate': 'год рождения', 'date of birth': 'год рождения', 'др': 'год рождения',
    'разряд': 'разряд', 'спортразряд': 'разряд', 'rank': 'разряд',
    'кю': 'кю', 'kyu': 'кю', 'кью': 'кю',
    'сш': 'СШ', 'школа': 'СШ', 'sports_school': 'СШ', 'спортивная школа': 'СШ',
    'тренер': 'тренер', 'coach': 'тренер',
}


def _normalize_import_header(h: str) -> str:
    key = (h or '').strip().lower().rstrip(':').replace('\ufeff', '')
    return _IMPORT_ALIASES.get(key, '')


def _extract_year(value: str) -> str:
    """Возвращает 4-значный год из строки ('2001', '14.03.2001', '2001 г.р.') или ''."""
    import re as _re
    m = _re.search(r'(19|20)\d{2}', value or '')
    return m.group(0) if m else (value or '').strip()


def _sniff_csv_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;\t')
    except Exception:
        return csv.excel


@app.route('/api/data/participants/import', methods=['POST'])
def api_data_participants_import():
    """Импорт базы участников из CSV-файла (Excel/LibreOffice, , ; \\t, UTF-8/CP1251)."""
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'Файл не выбран'})
    if not file.filename.lower().endswith('.csv'):
        return jsonify({'success': False, 'error': 'Ожидается файл .csv'})
    raw = file.read(5 * 1024 * 1024)  # лимит 5 МБ
    if len(raw) >= 5 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'Файл слишком большой (макс. 5 МБ)'})
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return jsonify({'success': False, 'error': 'Не удалось определить кодировку файла'})

    dialect = _sniff_csv_dialect(text[:4096])
    rows_in = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows_in:
        return jsonify({'success': False, 'error': 'Файл пуст или не содержит строк данных'})

    # маппинг колонок источника -> канонические поля
    src_fields = [f for f in (rows_in[0].keys() if rows_in[0] else []) if f is not None]
    col_map: Dict[str, str] = {}
    for h in src_fields:
        canon = _normalize_import_header(h)
        if canon and canon not in col_map.values():
            col_map[h] = canon
    if 'ФИО' not in col_map.values():
        return jsonify({'success': False,
                        'error': 'В файле не найдена колонка ФИО. Колонки: ' + ', '.join(src_fields)})

    mode = (request.form.get('mode') or 'merge').strip()  # merge | replace | skip_dupes
    new_rows, updated, skipped = [], 0, []
    seen_keys = set()
    for r in rows_in:
        fio = (r.get(next((h for h, c in col_map.items() if c == 'ФИО'), '')) or '').strip()
        if not fio:
            continue
        yr_field = next((h for h, c in col_map.items() if c == 'год рождения'), '')
        year = _extract_year(r.get(yr_field, '') if yr_field else '')
        row = {'ФИО': fio, 'год рождения': year}
        for canon in ('разряд', 'кю', 'СШ', 'тренер'):
            h = next((h for h, c in col_map.items() if c == canon), '')
            row[canon] = (r.get(h, '') or '').strip() if h else ''
        key = (fio.lower(), year)
        if key in seen_keys:
            skipped.append(f"{fio} ({year}) — дубль внутри файла")
            continue
        seen_keys.add(key)
        new_rows.append(row)

    if not new_rows:
        return jsonify({'success': False, 'error': 'В файле нет валидных строк (нужны ФИО и год рождения)'})

    try:
        with _csv_lock:
            if mode == 'replace':
                CSVManager.write_csv(PARTICIPANTS_CSV, new_rows, _PARTICIPANTS_HEADERS)
                final_rows = new_rows
                added, dupes, updated = len(new_rows), 0, 0
            else:
                existing = CSVManager.read_csv(PARTICIPANTS_CSV)
                index = {}
                for i, e in enumerate(existing):
                    k = ((e.get('ФИО') or '').strip().lower(),
                         _extract_year(e.get('год рождения') or ''))
                    index[k] = i
                added = dupes = updated = 0
                final_rows = existing
                for row in new_rows:
                    k = (row['ФИО'].lower(), row['год рождения'])
                    if k in index:
                        i = index[k]
                        if mode == 'skip_dupes':
                            dupes += 1
                            continue
                        old = final_rows[i]
                        changed = False
                        for field in ('разряд', 'кю', 'СШ', 'тренер'):
                            if row[field] and row[field] != (old.get(field) or ''):
                                old[field] = row[field]
                                changed = True
                        if changed:
                            updated += 1
                    else:
                        final_rows.append(row)
                        index[k] = len(final_rows) - 1
                        added += 1
                if added or updated:
                    CSVManager.write_csv(PARTICIPANTS_CSV, final_rows, _PARTICIPANTS_HEADERS)
        msg = f"Добавлено: {added}"
        if mode != 'replace':
            msg += f", обновлено: {updated}, пропущено дублей: {dupes}"
        if skipped:
            msg += f", повторы в файле: {len(skipped)}"
        return jsonify({'success': True, 'added': added, 'updated': updated,
                        'duplicates': dupes, 'total': len(new_rows), 'message': msg})
    except Exception as exc:
        app.logger.error('api_data_participants_import: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/judges/import', methods=['POST'])
def api_data_judges_import():
    """Импорт базы судей из CSV-файла."""
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'Файл не выбран'})
    if not file.filename.lower().endswith('.csv'):
        return jsonify({'success': False, 'error': 'Ожидается файл .csv'})
    raw = file.read(5 * 1024 * 1024)
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return jsonify({'success': False, 'error': 'Не удалось определить кодировку файла'})
    dialect = _sniff_csv_dialect(text[:4096])
    rows_in = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows_in:
        return jsonify({'success': False, 'error': 'Файл пуст'})
    src_fields = [f for f in (rows_in[0].keys() if rows_in[0] else []) if f is not None]
    fio_col = next((h for h in src_fields if _normalize_import_header(h) == 'ФИО'), src_fields[0])

    names, seen = [], set()
    for r in rows_in:
        fio = (r.get(fio_col) or '').strip()
        if fio and fio.lower() not in seen:
            seen.add(fio.lower())
            names.append({'ФИО': fio})
    if not names:
        return jsonify({'success': False, 'error': 'Не найдено ни одного ФИО судьи'})
    try:
        with _csv_lock:
            existing = CSVManager.read_csv(JUDGES_CSV)
            have = {(e.get('ФИО') or '').strip().lower() for e in existing}
            to_add = [n for n in names if n['ФИО'].lower() not in have]
            if to_add:
                all_rows = existing + to_add
                CSVManager.write_csv(JUDGES_CSV, all_rows, _JUDGES_HEADERS)
        return jsonify({'success': True, 'added': len(to_add),
                        'duplicates': len(names) - len(to_add),
                        'message': f"Добавлено: {len(to_add)}, уже было: {len(names) - len(to_add)}"})
    except Exception as exc:
        app.logger.error('api_data_judges_import: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/data/competitions', methods=['GET'])
def api_data_competitions_list():
    if not session.get('admin'):
        return jsonify({'error': 'Не авторизован'}), 401
    items = []
    if os.path.exists(COMPETITIONS_BASE_DIR):
        for folder in sorted(os.listdir(COMPETITIONS_BASE_DIR), reverse=True):
            comp_path = os.path.join(COMPETITIONS_BASE_DIR, folder)
            if not os.path.isdir(comp_path):
                continue
            cfg = {}
            cfg_file = os.path.join(comp_path, 'config.json')
            if os.path.exists(cfg_file):
                try:
                    with open(cfg_file, 'r', encoding='utf-8') as f:
                        cfg = json.load(f) or {}
                except Exception:
                    cfg = {}
            items.append({
                'name': cfg.get('name', folder),
                'folder_name': folder,
                'created_at': cfg.get('created'),
                'status': cfg.get('status', 'open'),
            })
    return jsonify(items)



@app.route('/config', methods=['GET', 'POST'])
def config_competition():
    """Создание нового соревнования"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        comp_name = request.form.get('comp_name', '').strip()
        comp_path = request.form.get('comp_path', COMPETITIONS_BASE_DIR).strip()
        
        if not comp_name:
            flash('Укажите название соревнования', 'danger')
            return render_template('config.html', default_path=COMPETITIONS_BASE_DIR)
        
        # Нормализуем путь для Windows и Linux
        comp_path = comp_path.replace('\\', os.sep).replace('/', os.sep)

        # Проверяем доступ к директории
        if not os.path.isdir(comp_path) or not os.access(comp_path, os.W_OK):
            comp_path = COMPETITIONS_BASE_DIR
        
        # Создаем папку соревнования
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        comp_folder_name = f"{comp_name}_{timestamp}"
        comp_full_path = os.path.join(comp_path, comp_folder_name)
        
        try:
            os.makedirs(comp_full_path, exist_ok=True)
            
            # Создаем файл config.json с информацией
            config = {
                'name': comp_name,
                'created': datetime.now().isoformat(),
                'status': 'open',
                'disciplines': [],
                'banner': ''
            }
            with open(os.path.join(comp_full_path, 'config.json'), 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            flash(f'Соревнование "{comp_name}" создано', 'success')
            return redirect(url_for('edit_competition', comp_name=comp_folder_name))
        except Exception as e:
            flash(f'Ошибка при создании соревнования: {str(e)}', 'danger')
    
    return render_template('config.html', default_path=COMPETITIONS_BASE_DIR)


@app.route('/admin/<comp_name>')
def edit_competition(comp_name):
    """Редактор соревнования"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Читаем конфиг для красивого названия
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    comp_display_name = config.get('name', comp_name)
    
    # Получаем список дисциплин
    disciplines = []
    for folder in os.listdir(comp_path):
        folder_path = os.path.join(comp_path, folder)
        if os.path.isdir(folder_path) and folder not in ('__pycache__', 'results'):
            # Получаем количество пар
            pairs_file = os.path.join(folder_path, 'participants_list.csv')
            pair_count = 0
            if os.path.exists(pairs_file):
                with open(pairs_file, 'r', encoding='utf-8') as f:
                    pair_count = len(f.readlines()) - 1  # минус заголовок
            
            disciplines.append({
                'key': folder,
                'name': get_discipline_display_name(folder),
                'pair_count': pair_count,
                'stage': ensure_stage_config(comp_path, folder),
            })
    
    # Получаем все доступные дисциплины
    all_disciplines = list(DISCIPLINE_ROWS_BY_KEY.keys())
    existing_disciplines = [d['key'] for d in disciplines]
    available_disciplines = [{'key': d, 'name': get_discipline_display_name(d)} for d in all_disciplines if d not in existing_disciplines]

    proto_status = protocol_readiness(comp_path)

    return render_template('edit_competition.html',
                         comp_name=comp_name,
                         config=config,
                         disciplines=disciplines,
                         available_disciplines=available_disciplines,
                         protocol_status=proto_status)


@app.route('/admin/<comp_name>/protocol-status')
def competition_protocol_status(comp_name):
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    return jsonify(protocol_readiness(comp_path))


@app.route('/admin/<comp_name>/generate-protocols', methods=['POST'])
def competition_generate_protocols(comp_name):
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    data = request.get_json(silent=True) or {}
    dk = (data.get('discipline_key') or '').strip() or None
    if dk:
        disc_path = os.path.join(comp_path, dk)
        if not os.path.isdir(disc_path):
            return jsonify({'error': 'Discipline not found'}), 404
    result = generate_competition_protocols(
        comp_path, comp_name, discipline_key=dk, technique_map=DISCIPLINE_ROWS_BY_KEY
    )
    if result.get('success'):
        result['readiness'] = protocol_readiness(comp_path)
    return jsonify(result)


@app.route('/admin/<comp_name>/add-discipline', methods=['POST'])
def add_discipline(comp_name):
    """Добавить дисциплину к соревнованию"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    discipline_key = request.json.get('discipline_key', '').strip()
    if not discipline_key or discipline_key not in DISCIPLINE_ROWS_BY_KEY:
        return jsonify({'error': 'Invalid discipline'}), 400
    
    # Создаем структуру для дисциплины
    CompetitionCSVManager.create_discipline_structure(comp_path, discipline_key)
    ensure_stage_config(comp_path, discipline_key)
    
    return jsonify({'success': True, 'message': 'Дисциплина добавлена'})


@app.route('/admin/<comp_name>/<kata_key>/stage', methods=['POST'])
def discipline_stage_action(comp_name, kata_key):
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        return jsonify({'error': 'Discipline not found'}), 404
    cfg = ensure_stage_config(comp_path, kata_key)
    data = request.get_json(silent=True) or {}
    action = str(data.get('action', '')).strip()
    top_n = int(data.get('top_n', cfg.get('final_top_n', 3) or 3))
    top_n = max(1, min(16, top_n))
    cfg['final_top_n'] = top_n

    root_pairs = CSVManager.read_csv(os.path.join(disc_path, 'participants_list.csv'))
    if len(root_pairs) <= 3 and len(root_pairs) > 0:
        cfg['mode'] = 'final_only'
        cfg['current_stage'] = 'final'
        cfg['status'] = 'open'
        CSVManager.write_csv(
            os.path.join(disc_path, 'final', 'participants_list.csv'),
            root_pairs,
            CompetitionCSVManager.PAIRS_HEADERS,
        )
        with open(_stage_config_path(comp_path, kata_key), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True, 'stage': cfg, 'message': 'До 3 пар: автоматически прямой финал'})

    if action == 'set_prelim':
        cfg['mode'] = 'prelim_final'
        cfg['current_stage'] = 'prelim'
        cfg['status'] = 'open'
        # prelim данные уже в корне дисциплины — ничего не переносим
    elif action == 'set_final_only':
        cfg['mode'] = 'final_only'
        cfg['current_stage'] = 'final'
        cfg['status'] = 'open'
        master_pairs = CSVManager.read_csv(os.path.join(disc_path, 'participants_list.csv'))
        CSVManager.write_csv(
            CompetitionCSVManager.get_stage_participants_path(comp_path, kata_key, 'final'),
            master_pairs,
            CompetitionCSVManager.PAIRS_HEADERS,
        )
    elif action == 'open_final':
        prelim_final_path = os.path.join(disc_path, 'final_protocol.csv')
        prelim_results = CSVManager.read_csv(prelim_final_path)
        prelim_results = [r for r in prelim_results if str(r.get('Сумма', '')).strip()]
        prelim_results = sort_prelim_results_for_final_transfer(prelim_results)
        winners = prelim_results[:top_n]
        prelim_pairs = CSVManager.read_csv(os.path.join(disc_path, 'participants_list.csv'))
        by_num = {str(p.get('номер пары', '')).strip(): p for p in prelim_pairs}
        final_pairs = []
        for w in winners:
            p = by_num.get(str(w.get('номер пары', '')).strip())
            if p:
                final_pairs.append(p)
        final_pairs_path = os.path.join(disc_path, 'final', 'participants_list.csv')
        CSVManager.write_csv(
            final_pairs_path,
            final_pairs,
            CompetitionCSVManager.PAIRS_HEADERS,
        )
        cfg['mode'] = 'prelim_final'
        cfg['current_stage'] = 'final'
        cfg['status'] = 'open'
    elif action == 'close_stage':
        cfg['status'] = 'closed'
    elif action == 'open_stage':
        cfg['status'] = 'open'
    else:
        return jsonify({'error': 'Unknown action'}), 400

    with open(_stage_config_path(comp_path, kata_key), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({'success': True, 'stage': cfg})


@app.route('/admin/<comp_name>/<kata_key>/techniques-limit', methods=['POST'])
def discipline_techniques_limit(comp_name, kata_key):
    """Настройка «Ограничить число техник» для дисциплины (переключатель + n).

    Сохраняется в stage.json дисциплины: techniques_limit_enabled (bool),
    techniques_count_prelim / techniques_count_final (int|None).
    Оценки судей при изменении n не удаляются — в подсчёт идут только первые n техник.
    """
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        return jsonify({'error': 'Discipline not found'}), 404

    data = request.get_json(silent=True) or {}
    cfg = ensure_stage_config(comp_path, kata_key)
    total = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
    enabled = bool(data.get('enabled'))

    errors = {}
    parsed = {}
    if enabled:
        for stage_key, field in (('prelim', 'techniques_count_prelim'), ('final', 'techniques_count_final')):
            raw = data.get(field)
            if raw is None and field in ('techniques_count_prelim',):
                raw = data.get('techniques_count')  # единое поле для обоих этапов
            if raw is None or str(raw).strip() == '':
                raw = cfg.get(field)
            n, err = validate_techniques_count_input(raw, total)
            if err:
                errors[stage_key] = err
            parsed[field] = n

    if errors:
        return jsonify({'error': 'invalid', 'errors': errors}), 400

    had_scores = has_submitted_scores(comp_path, kata_key, 'prelim') or \
        has_submitted_scores(comp_path, kata_key, 'final')
    old_n_prelim = get_stage_techniques_count(comp_path, kata_key, 'prelim')
    old_n_final = get_stage_techniques_count(comp_path, kata_key, 'final')

    cfg['techniques_limit_enabled'] = enabled
    if enabled:
        cfg['techniques_count_prelim'] = parsed['techniques_count_prelim']
        cfg['techniques_count_final'] = parsed['techniques_count_final']
    # при выключенном переключателе значения сохраняются, но игнорируются

    with open(_stage_config_path(comp_path, kata_key), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    new_n_prelim = get_stage_techniques_count(comp_path, kata_key, 'prelim')
    new_n_final = get_stage_techniques_count(comp_path, kata_key, 'final')
    warning = ''
    if had_scores and (new_n_prelim != old_n_prelim or new_n_final != old_n_final):
        warning = ('Внимание: на этапе уже есть сданные оценки. Они сохранены, '
                   'но в подсчёт теперь попадают только первые N техник.')

    invalidate_tablo_cache(comp_name, kata_key)
    return jsonify({
        'success': True,
        'enabled': enabled,
        'total': total,
        'prelim': new_n_prelim,
        'final': new_n_final,
        'warning': warning,
        'had_scores': had_scores,
    })


@app.route('/api/<comp_name>/<kata_key>/techniques-limit')
def discipline_techniques_limit_get(comp_name, kata_key):
    """Текущая настройка ограничения числа техник для интерфейса."""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(os.path.join(comp_path, kata_key)):
        return jsonify({'error': 'Discipline not found'}), 404
    cfg = ensure_stage_config(comp_path, kata_key)
    total = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))
    return jsonify({
        'enabled': bool(cfg.get('techniques_limit_enabled')),
        'total': total,
        'min': TECHNIQUES_COUNT_MIN,
        'prelim': cfg.get('techniques_count_prelim'),
        'final': cfg.get('techniques_count_final'),
        'effective_prelim': get_stage_techniques_count(comp_path, kata_key, 'prelim'),
        'effective_final': get_stage_techniques_count(comp_path, kata_key, 'final'),
    })


@app.route('/admin/<comp_name>/set-main-tablo', methods=['POST'])
def set_main_tablo_discipline(comp_name):
    """Установить дисциплину для главного табло и уведомить всех зрителей через WebSocket"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404

    discipline_key = request.json.get('discipline_key', '').strip()

    # Читаем конфиг
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

    # Устанавливаем выбранную дисциплину
    config['main_tablo_discipline'] = discipline_key

    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    # Отправляем WebSocket событие всем подключенным клиентам
    socketio.emit('tablo_update', {
        'comp_name': comp_name,
        'discipline_key': discipline_key
    }, room=f'tablo_{comp_name}')

    return jsonify({'success': True, 'message': 'Дисциплина для главного табло установлена'})


@app.route('/admin/<comp_name>/remove-discipline', methods=['POST'])
def remove_discipline(comp_name):
    """Удалить дисциплину из соревнования"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    discipline_key = request.json.get('discipline_key', '').strip()
    disc_path = os.path.join(comp_path, discipline_key)
    
    if os.path.isdir(disc_path):
        import shutil
        shutil.rmtree(disc_path)
    
    return jsonify({'success': True, 'message': 'Дисциплина удалена'})


@app.route('/admin/<comp_name>/close', methods=['POST'])
def close_competition(comp_name):
    """Закрыть соревнование"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    import json
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    config['status'] = 'closed'
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return jsonify({'success': True, 'message': 'Соревнование закрыто'})


@app.route('/admin/<comp_name>/open', methods=['POST'])
def open_competition(comp_name):
    """Открыть соревнование"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    import json
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    config['status'] = 'open'
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return jsonify({'success': True, 'message': 'Соревнование открыто'})


@app.route('/admin/<comp_name>/delete', methods=['POST'])
def delete_competition(comp_name):
    """Удалить соревнование"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    try:
        import shutil
        shutil.rmtree(comp_path)
        return jsonify({'success': True, 'message': 'Соревнование удалено'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/clear-participants', methods=['POST'])
def clear_participants():
    """Очистить глобальный CSV участников"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    CSVManager.write_csv(PARTICIPANTS_CSV, [], CompetitionCSVManager.PARTICIPANTS_HEADERS)
    return jsonify({'success': True, 'message': 'CSV участников очищен'})


@app.route('/admin/clear-judges', methods=['POST'])
def clear_judges():
    """Очистить глобальный CSV судей"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    CSVManager.write_csv(JUDGES_CSV, [], CompetitionCSVManager.JUDGES_HEADERS)
    return jsonify({'success': True, 'message': 'CSV судей очищен'})


# ==================== РЕГИСТРАЦИЯ УЧАСТНИКОВ ====================

@app.route('/<comp_name>/<kata_key>/reg', methods=['GET', 'POST'])
def register_participants(comp_name, kata_key):
    """Регистрация участников"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        flash('Дисциплина не найдена', 'danger')
        return redirect(url_for('edit_competition', comp_name=comp_name))
    
    if request.method == 'POST':
        # Получаем данные из формы
        pairs_data = []
        judges_data = []
        
        # Парсим пары
        pair_index = 0
        while True:
            tori_name = request.form.get(f'pair_{pair_index}_tori_name', '').strip()
            uke_name = request.form.get(f'pair_{pair_index}_uke_name', '').strip()
            
            if not tori_name or not uke_name:
                break
            
            tori_info = {
                'ФИО': tori_name,
                'год рождения': request.form.get(f'pair_{pair_index}_tori_birth', ''),
                'разряд': request.form.get(f'pair_{pair_index}_tori_rank', ''),
                'кю': request.form.get(f'pair_{pair_index}_tori_kyu', ''),
                'СШ': request.form.get(f'pair_{pair_index}_tori_school', ''),
                'тренер': request.form.get(f'pair_{pair_index}_tori_coach', '')
            }
            
            uke_info = {
                'ФИО': uke_name,
                'год рождения': request.form.get(f'pair_{pair_index}_uke_birth', ''),
                'разряд': request.form.get(f'pair_{pair_index}_uke_rank', ''),
                'кю': request.form.get(f'pair_{pair_index}_uke_kyu', ''),
                'СШ': request.form.get(f'pair_{pair_index}_uke_school', ''),
                'тренер': request.form.get(f'pair_{pair_index}_uke_coach', '')
            }
            
            def is_fully_filled_participant(info):
                for h in CompetitionCSVManager.PARTICIPANTS_HEADERS:
                    if not str(info.get(h, '')).strip():
                        return False
                return True
            
            if is_fully_filled_participant(tori_info):
                CSVManager.upsert_participant(PARTICIPANTS_CSV, tori_info, CompetitionCSVManager.PARTICIPANTS_HEADERS)
            if is_fully_filled_participant(uke_info):
                CSVManager.upsert_participant(PARTICIPANTS_CSV, uke_info, CompetitionCSVManager.PARTICIPANTS_HEADERS)
            
            # Добавляем в локальный CSV пар
            pairs_data.append({
                'номер пары': pair_index + 1,
                'Тори_ФИО': tori_name,
                'Тори_год рождения': request.form.get(f'pair_{pair_index}_tori_birth', ''),
                'Тори_разряд': request.form.get(f'pair_{pair_index}_tori_rank', ''),
                'Тори_кю': request.form.get(f'pair_{pair_index}_tori_kyu', ''),
                'Тори_СШ': request.form.get(f'pair_{pair_index}_tori_school', ''),
                'Тори_тренер': request.form.get(f'pair_{pair_index}_tori_coach', ''),
                'Уке_ФИО': uke_name,
                'Уке_год рождения': request.form.get(f'pair_{pair_index}_uke_birth', ''),
                'Уке_разряд': request.form.get(f'pair_{pair_index}_uke_rank', ''),
                'Уке_кю': request.form.get(f'pair_{pair_index}_uke_kyu', ''),
                'Уке_СШ': request.form.get(f'pair_{pair_index}_uke_school', ''),
                'Уке_тренер': request.form.get(f'pair_{pair_index}_uke_coach', '')
            })
            
            pair_index += 1
        
        # Парсим судей (поддержка произвольного количества)
        judge_items = []
        for k, v in request.form.items():
            if not k.startswith('judge_') or not k.endswith('_name'):
                continue
            name = str(v or '').strip()
            if not name:
                continue
            mid = k[len('judge_'):-len('_name')]
            try:
                pos = int(mid)
            except ValueError:
                continue
            if pos <= 0:
                continue
            judge_items.append((pos, name))
        judge_items.sort(key=lambda x: x[0])

        for judge_pos, judge_name in judge_items:
            judge_info = {
                'место': judge_pos,
                'ФИО': judge_name
            }
            judges_data.append(judge_info)
            # Добавляем в глобальный CSV судей
            CSVManager.add_row(JUDGES_CSV, {'ФИО': judge_name}, CompetitionCSVManager.JUDGES_HEADERS)
        
        # Сохраняем в локальные CSV
        if pairs_data:
            pairs_file = os.path.join(disc_path, 'participants_list.csv')
            CSVManager.write_csv(pairs_file, pairs_data, CompetitionCSVManager.PAIRS_HEADERS)
            cfg = ensure_stage_config(comp_path, kata_key)
            # prelim хранится в корне дисциплины (уже записано выше)
            if cfg.get('mode') == 'final_only':
                CSVManager.write_csv(
                    os.path.join(disc_path, 'final', 'participants_list.csv'),
                    pairs_data,
                    CompetitionCSVManager.PAIRS_HEADERS,
                )
        
        if judges_data:
            judges_file = os.path.join(disc_path, 'judges_list.csv')
            CSVManager.write_csv(judges_file, judges_data, CompetitionCSVManager.JUDGES_LIST_HEADERS)
        
        flash('Участники и судьи зарегистрированы', 'success')
        return redirect(url_for('edit_competition', comp_name=comp_name))
    
    # Загружаем существующие данные
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    judges_file = os.path.join(disc_path, 'judges_list.csv')
    
    existing_pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
    existing_judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    
    # Получаем список техник для дисциплины
    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    
    # Получаем красивое название соревнования
    comp_display_name = ''
    config_file = os.path.join(comp_path, 'config.json')
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
            comp_display_name = config.get('name', comp_name)
    
    return render_template('registration.html',
                         comp_name=comp_name,
                         kata_key=kata_key,
                         kata_name=get_discipline_display_name(kata_key),
                         comp_display_name=comp_display_name,
                         techniques=techniques,
                         existing_pairs=existing_pairs,
                         existing_judges=existing_judges)


# ==================== API ENDPOINTS ====================

@app.route('/api/participants/search')
def search_participants():
    """API для поиска участников по ФИО"""
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify([])
    
    suggestions = CSVManager.get_name_suggestions(PARTICIPANTS_CSV, query)
    return jsonify(suggestions)


@app.route('/api/participants/column-suggestions')
def participants_column_suggestions():
    """Подсказки по уникальным значениям столбца СШ или тренер (глобальный participants.csv)."""
    field = request.args.get('field', '').strip()
    q = request.args.get('q', '').strip()
    if field not in ('СШ', 'тренер') or len(q) < 1:
        return jsonify([])
    rows = CSVManager.read_csv(PARTICIPANTS_CSV)
    out = []
    seen = set()
    ql = q.lower()
    for row in rows:
        v = (row.get(field) or '').strip()
        if not v or v.lower() in seen:
            continue
        if v.lower().startswith(ql):
            seen.add(v.lower())
            out.append(v)
        if len(out) >= 20:
            break
    return jsonify(out)


@app.route('/api/participants/info')
def get_participant_info():
    """API для получения информации о участнике"""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({})
    
    participant = CSVManager.search_by_name(PARTICIPANTS_CSV, name)
    return jsonify(participant or {})


@app.route('/api/judges/search')
def search_judges():
    """API для поиска судей по ФИО"""
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify([])
    
    suggestions = CSVManager.get_name_suggestions(JUDGES_CSV, query)
    return jsonify(suggestions)


@app.route('/api/judges/info')
def get_judge_info():
    """API для получения информации о судье"""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({})
    
    judge = CSVManager.search_by_name(JUDGES_CSV, name)
    return jsonify(judge or {})


@app.route('/api/judges/validate')
def validate_judge():
    """API для проверки существует ли судья в списке"""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'exists': False})
    
    # Проверяем точное совпадение имени судьи
    all_judges = CSVManager.read_csv(JUDGES_CSV)
    for judge in all_judges:
        judge_name = judge.get('ФИО', '').strip()
        if judge_name.lower() == name.lower():
            return jsonify({'exists': True})
    
    return jsonify({'exists': False})


@app.route('/api/<comp_name>/<kata_key>/registration-data')
def get_registration_data(comp_name, kata_key):
    """API для получения данных регистрации"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        return jsonify({'error': 'Competition not found'}), 404
    
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        return jsonify({'error': 'Discipline not found'}), 404
    
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    judges_file = os.path.join(disc_path, 'judges_list.csv')
    
    existing_pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
    existing_judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    
    return jsonify({
        'existing_pairs': existing_pairs,
        'existing_judges': existing_judges
    })


@app.route('/api/<comp_name>/<kata_key>/save-scores', methods=['POST'])
def save_judge_scores(comp_name, kata_key):
    """Сохранить оценки судьи"""
    if not request.json:
        return jsonify({'error': 'No data'}), 400

    judge_name = request.json.get('judge_name')
    judge_position = request.json.get('judge_position')
    pair_number = request.json.get('pair_number')
    scores = request.json.get('scores', [])

    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    stage = stage_for_ops(comp_path, kata_key)
    files = get_stage_files(comp_path, kata_key, stage)

    # Получаем ФИО пары
    pairs_file = files['participants']
    pairs = CSVManager.read_csv(pairs_file)
    pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == int(pair_number)), None)
    techniques = effective_techniques(comp_path, kata_key, stage)

    if len(scores) != len(techniques):
        return jsonify({'error': 'Invalid scores length'}), 400

    if pair_obj:
        tori_fio = pair_obj.get('Тори_ФИО', '')
        uke_fio = pair_obj.get('Уке_ФИО', '')
        protocol_path = CompetitionCSVManager.get_stage_protocol_path(comp_path, kata_key, stage, judge_name, int(judge_position), tori_fio, uke_fio)
    else:
        protocol_path = os.path.join(CompetitionCSVManager.get_stage_path(comp_path, kata_key, stage), 'protocols', f'{judge_name}_{judge_position}_{pair_number}.csv')

    os.makedirs(os.path.dirname(protocol_path), exist_ok=True)

    technique_data = [{'техника': tech, 'оценка': score} for tech, score in zip(techniques, scores)]

    headers = ['техника', 'оценка']
    CSVManager.write_csv(protocol_path, technique_data, headers)

    return jsonify({'success': True})


@app.route('/api/<comp_name>/<kata_key>/save-judge-action', methods=['POST'])
def save_judge_action(comp_name, kata_key):
    """Сохранить действие судьи"""
    data = request.json
    judge = data.get('judge')
    pos = data.get('pos')
    pair = data.get('pair')
    details = data.get('details', [])
    total = data.get('total')
    isFinal = data.get('isFinal', False)

    if not judge or not pos or not pair:
        return jsonify({'error': 'Missing data'}), 400
    try:
        pos_int = int(pos)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid judge place'}), 400
    if pos_int < 1 or pos_int > 5:
        return jsonify({'error': 'Для оценивания используются места 1..5'}), 400

    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    stage = stage_for_ops(comp_path, kata_key)
    stage_cfg = ensure_stage_config(comp_path, kata_key)
    if stage_cfg.get('status') == 'closed':
        return jsonify({'error': 'Stage is closed'}), 400
    files = get_stage_files(comp_path, kata_key, stage)
    pairs = CSVManager.read_csv(files['participants'])
    pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == int(pair)), None)
    if not pair_obj:
        return jsonify({'error': 'Pair not found'}), 400
    tori_fio = pair_obj.get('Тори_ФИО', '')
    uke_fio = pair_obj.get('Уке_ФИО', '')

    # Оцениваются только первые n техник (настройка дисциплины); лишние отбрасываются.
    techniques = effective_techniques(comp_path, kata_key, stage)
    details = list(details or [])[:len(techniques)]

    # Рассчитываем scores из details
    scores = []
    for d in details:
        if d.get('forgotten', False):
            scores.append(0.0)
        else:
            score = 10.0
            score -= d.get('m1', 0)
            score -= d.get('m2', 0)
            score -= d.get('med', 0)
            score -= d.get('big', 0)
            score -= d.get('c_minus', 0)
            score -= d.get('c_plus', 0)  # c_plus is -0.5, so subtracting it adds 0.5
            scores.append(max(0, min(10, score)))

    # Сохраняем файл
    protocol_path = CompetitionCSVManager.get_stage_protocol_path(comp_path, kata_key, stage, judge, pos_int, tori_fio, uke_fio)

    os.makedirs(os.path.dirname(protocol_path), exist_ok=True)

    technique_data = [{'техника': tech, 'техника_№': i + 1, 'details_json': json.dumps(d)}
                      for i, (tech, d) in enumerate(zip(techniques, details))]

    headers = ['техника', 'техника_№', 'details_json']
    CSVManager.write_csv(protocol_path, technique_data, headers)

    if isFinal:
        judges_file = os.path.join(comp_path, kata_key, 'judges_list.csv')
        judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
        meta = judge_positions_meta(judges)
        if not meta['valid']:
            return jsonify({'error': meta['error']}), 400
        final_protocol_path = files['final_protocol']
        all_results = CSVManager.read_csv(final_protocol_path) if os.path.exists(final_protocol_path) else []
        
        # Ищем или создаем запись для этой пары
        pair_entry = None
        for entry in all_results:
            if int(entry.get('номер пары', 0)) == int(pair):
                pair_entry = entry
                break
        
        if not pair_entry:
            pair_entry = {
                'номер пары': pair,
                'Тори': encode_participant_for_protocol(pair_obj, 'Тори'),
                'Уке': encode_participant_for_protocol(pair_obj, 'Уке'),
                'Судья 1': '',
                'Судья 2': '',
                'Судья 3': '',
                'Судья 4': '',
                'Судья 5': '',
                'Сумма': '',
                'Место': ''
            }
            all_results.append(pair_entry)
        else:
            pair_entry['Тори'] = encode_participant_for_protocol(pair_obj, 'Тори')
            pair_entry['Уке'] = encode_participant_for_protocol(pair_obj, 'Уке')
        
        # Обновляем оценку судьи
        judge_col = f'Судья {pos_int}'
        pair_entry[judge_col] = total
        final = _compute_final_from_entry(pair_entry, meta['effective_positions'])
        pair_entry['Сумма'] = f'{final:.1f}' if final is not None else ''
        
        # Записываем обновленный финальный протокол
        CSVManager.write_csv(final_protocol_path, all_results, CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    
    return jsonify({'success': True})


@app.route('/api/<comp_name>/<kata_key>/get-judge-scores/<judge>/<int:pos>/<tori>/<uke>')
def get_judge_scores(comp_name, kata_key, judge, pos, tori, uke):
    """Получить существующие оценки судьи"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    stage = stage_for_ops(comp_path, kata_key)
    protocol_path = CompetitionCSVManager.resolve_stage_protocol_path(comp_path, kata_key, stage, judge, pos, tori, uke)
    details = {}
    rows = CSVManager.read_csv(protocol_path)
    for row in rows:
        tech_name = row.get('техника', '')
        details_json = row.get('details_json', '{}')
        try:
            detail = json.loads(details_json)
        except json.JSONDecodeError:
            detail = {}
        details[tech_name] = detail
    scores = details
    return jsonify(scores)


# ==================== СУДЕЙСКАЯ ЧАСТЬ ====================

@app.route('/dashboard')
def public_dashboard():
    """Публичная панель со списком активных турниров"""
    competitions = []
    if os.path.exists(COMPETITIONS_BASE_DIR):
        for comp_folder in os.listdir(COMPETITIONS_BASE_DIR):
            comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_folder)
            if os.path.isdir(comp_path):
                import json
                config_file = os.path.join(comp_path, 'config.json')
                config = {}
                if os.path.exists(config_file):
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                
                if config.get('status') == 'open':
                    # Получаем дисциплины
                    disciplines = []
                    for folder in os.listdir(comp_path):
                        folder_path = os.path.join(comp_path, folder)
                        if os.path.isdir(folder_path) and folder not in ('__pycache__', 'results'):
                            stage_cfg = ensure_stage_config(comp_path, folder)
                            stage_label = 'Финал' if stage_cfg.get('current_stage') == 'final' else 'Предварительные встречи'
                            disciplines.append({
                                'key': folder,
                                'name': get_discipline_display_name(folder),
                                'stage_label': stage_label,
                            })
                    
                    competitions.append({
                        'name': comp_folder,
                        'display_name': config.get('name', comp_folder),
                        'disciplines': disciplines
                    })
    
    # Сортируем по имени соревнования в обратном порядке
    competitions.sort(key=lambda x: x['name'], reverse=True)
    return render_template('public_dashboard.html', competitions=competitions)


@app.route('/judge/<comp_name>/<kata_key>', methods=['GET', 'POST'])
def judge_page(comp_name, kata_key):
    """Форма судьи для оценки"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('public_dashboard'))
    
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        flash('Дисциплина не найдена', 'danger')
        return redirect(url_for('public_dashboard'))
    
    stage_cfg = ensure_stage_config(comp_path, kata_key)
    stage = stage_for_ops(comp_path, kata_key)
    if stage_cfg.get('status') == 'closed':
        flash('Этап дисциплины закрыт', 'warning')
    techniques = effective_techniques(comp_path, kata_key, stage)
    total_techniques = len(DISCIPLINE_ROWS_BY_KEY.get(kata_key, []))

    stage_files = get_stage_files(comp_path, kata_key, stage)
    pairs = CSVManager.read_csv(stage_files['participants'])

    judges_file = os.path.join(disc_path, 'judges_list.csv')
    judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    meta = judge_positions_meta(judges)
    judge_positions = [p for p in meta['effective_positions'] if 1 <= p <= 5]

    return render_template('judge_form.html',
                         comp_name=comp_name,
                         kata_key=kata_key,
                         kata_name=get_discipline_display_name(kata_key),
                         techniques=techniques,
                         total_techniques=total_techniques,
                         pairs=pairs,
                         judges=judges,
                         judge_positions=judge_positions,
                         stage=stage,
                         stage_error='' if meta['valid'] else meta['error'])


# ==================== ТАБЛО ====================

@app.route('/tablo/<comp_name>')
def main_tablo(comp_name):
    """Динамическое главное табло с автоматическим обновлением через WebSocket"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('public_dashboard'))

    # Читаем конфигурацию соревнования
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

    comp_display_name = config.get('name', comp_name)

    # Рендерим динамическое табло (без перенаправления)
    return render_template('main_tablo_dynamic.html',
                         comp_name=comp_name,
                         comp_display_name=comp_display_name,
                         config=config)


@app.route('/tablo/<comp_name>/<kata_key>')
def tablo(comp_name, kata_key):
    """Итоговая таблица результатов"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    if not os.path.isdir(comp_path):
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('public_dashboard'))
    
    disc_path = os.path.join(comp_path, kata_key)
    if not os.path.isdir(disc_path):
        flash('Дисциплина не найдена', 'danger')
        return redirect(url_for('public_dashboard'))
    
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    comp_display_name = config.get('name', comp_name)
    
    stage_cfg = ensure_stage_config(comp_path, kata_key)
    stage = stage_for_ops(comp_path, kata_key)
    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    stage_files = get_stage_files(comp_path, kata_key, stage)
    pairs = CSVManager.read_csv(stage_files['participants'])

    judges_file = os.path.join(disc_path, 'judges_list.csv')
    judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    meta = judge_positions_meta(judges)
    effective_positions = [p for p in meta['effective_positions'] if 1 <= p <= 5]
    final_protocol_path = stage_files['final_protocol']
    
    def build_results_from_pairs():
        results = []
        for pair in pairs:
            pair_number = int(pair.get('номер пары', 0))
            judge_scores = []
            for judge_pos in effective_positions:
                judge_obj = next((j for j in judges if str(j.get('место', '')).strip() == str(judge_pos)), {})
                judge_name = judge_obj.get('ФИО', '')
                protocol_path = CompetitionCSVManager.resolve_stage_protocol_path(
                    comp_path, kata_key, stage, judge_name, judge_pos, pair.get('Тори_ФИО', ''), pair.get('Уке_ФИО', '')
                )
                scores = {}
                for row in CSVManager.read_csv(protocol_path):
                    tech_name = row.get('техника', '')
                    try:
                        scores[tech_name] = json.loads(row.get('details_json', '{}'))
                    except json.JSONDecodeError:
                        scores[tech_name] = {}
                if scores:
                    technique_scores = []
                    forgotten_flags = []
                    for tech in techniques:
                        detail = scores.get(tech, {})
                        if detail.get('forgotten', False):
                            technique_scores.append(0.0)
                            forgotten_flags.append(True)
                        else:
                            score = 10.0
                            score -= detail.get('m1', 0)
                            score -= detail.get('m2', 0)
                            score -= detail.get('med', 0)
                            score -= detail.get('big', 0)
                            score -= detail.get('c_minus', 0)
                            score -= detail.get('c_plus', 0)
                            technique_scores.append(max(0, min(10, score)))
                            forgotten_flags.append(False)
                    judge_total = sum(technique_scores)
                    if any(forgotten_flags):
                        judge_total /= 2
                    judge_scores.append(judge_total)
                else:
                    judge_scores.append(None)
            if judge_scores and all(s is not None for s in judge_scores):
                final_score = calculate_pair_final_score(judge_scores, judge_count=max(3, len(effective_positions)))
            else:
                final_score = None
            results.append({
                'pair_number': pair_number,
                'tori': encode_participant_for_protocol(pair, 'Тори'),
                'uke': encode_participant_for_protocol(pair, 'Уке'),
                'judge_scores': judge_scores,
                'final_score': final_score,
            })
        return results
    
    def merge_with_pairs(base_list):
        """Гарантия: ни одна заявленная пара не исчезает с табло, даже без оценок."""
        by_num = {int(r.get('pair_number', 0)): r for r in base_list}
        merged = []
        for pair in pairs:
            pn = int(pair.get('номер пары', 0))
            if pn in by_num:
                merged.append(by_num[pn])
            else:
                merged.append({
                    'pair_number': pn,
                    'tori': encode_participant_for_protocol(pair, 'Тори'),
                    'uke': encode_participant_for_protocol(pair, 'Уке'),
                    'judge_scores': [None] * len(effective_positions),
                    'final_score': None,
                })
        declared = {int(p.get('номер пары', 0)) for p in pairs}
        for r in base_list:
            if int(r.get('pair_number', 0)) not in declared:
                merged.append(r)
        return merged

    if os.path.exists(final_protocol_path):
        existing_results = []
        rows_existing = CSVManager.read_csv(final_protocol_path)
        for row in rows_existing:
            judge_scores = []
            for p in effective_positions:
                if 1 <= p <= 5:
                    try:
                        v = float(row.get(f'Судья {p}', '')) if row.get(f'Судья {p}', '') != '' else None
                    except ValueError:
                        v = None
                    judge_scores.append(v)
            try:
                final_score = float(row.get('Сумма', '')) if row.get('Сумма') else None
            except ValueError:
                final_score = None
            existing_results.append({
                'pair_number': int(row.get('номер пары', 0)),
                'tori': row.get('Тори', ''),
                'uke': row.get('Уке', ''),
                'judge_scores': judge_scores,
                'final_score': final_score,
                'place': int(row.get('Место', 0)) if str(row.get('Место', '')).strip().isdigit() else None,
            })
        if existing_results:
            base_results = merge_with_pairs(existing_results)
        else:
            base_results = build_results_from_pairs()
    else:
        base_results = build_results_from_pairs()
    
    final_results = prepare_tablo_results(base_results, pairs)
    
    rows = []
    for result in final_results:
        row = {
            'номер пары': result['pair_number'],
            'Тори': result['tori'],
            'Уке': result['uke'],
            'Судья 1': '',
            'Судья 2': '',
            'Судья 3': '',
            'Судья 4': '',
            'Судья 5': '',
            'Сумма': result['final_score'] if result['final_score'] is not None else '',
            'Место': result['place'] if result.get('place') is not None else '',
        }
        for idx, p in enumerate(effective_positions):
            if 1 <= p <= 5 and idx < len(result['judge_scores']) and result['judge_scores'][idx] is not None:
                row[f'Судья {p}'] = result['judge_scores'][idx]
        rows.append(row)
    CSVManager.write_csv(final_protocol_path, rows, CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    
    return render_template(
        'tablo.html',
        comp_name=comp_name,
        comp_display_name=comp_display_name,
        kata_key=kata_key,
        kata_name=get_discipline_display_name(kata_key),
        judges=[j for j in judges if str(j.get('место', '')).strip().isdigit() and int(j.get('место', 0)) in effective_positions],
        results=final_results,
        config=config,
        stage=stage,
        stage_label='Финал' if stage == 'final' else 'Предварительные встречи',
        display_date=format_date_ru(datetime.now()),
    )


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', message='Страница не найдена'), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', message='Внутренняя ошибка сервера'), 500


# ==================== WEBSOCKET HANDLERS ====================

@socketio.on('connect')
def handle_connect():
    """Обработка подключения клиента"""
    print(f'✅ Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения клиента"""
    print(f'⚠️ Client disconnected: {request.sid}')

@socketio.on('join_tablo')
def handle_join_tablo(data):
    """Клиент присоединяется к комнате главного табло"""
    comp_name = data.get('comp_name')
    print(f'📥 Received join_tablo request: {data}')

    if comp_name:
        room = f'tablo_{comp_name}'
        join_room(room)
        print(f'✅ Client {request.sid} joined room: {room}')

        # Отправляем текущую дисциплину клиенту
        comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
        config_file = os.path.join(comp_path, 'config.json')
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            discipline = config.get('main_tablo_discipline', '')
            print(f'📤 Sending current discipline to client: {discipline}')
            emit('tablo_update', {
                'comp_name': comp_name,
                'discipline_key': discipline
            })
        else:
            print(f'⚠️ Config file not found: {config_file}')

@socketio.on('leave_tablo')
def handle_leave_tablo(data):
    """Клиент покидает комнату главного табло"""
    comp_name = data.get('comp_name')
    if comp_name:
        room = f'tablo_{comp_name}'
        leave_room(room)
        print(f'👋 Client {request.sid} left room: {room}')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
