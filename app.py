# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
from datetime import datetime
from csv_manager import CSVManager, CompetitionCSVManager, sort_prelim_results_for_final_transfer
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
    }
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            cfg.update(loaded or {})
        except Exception:
            pass
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
    """prefix: 'Тори_' или 'Уке_'"""
    parts = [
        pair_row.get(f'{prefix}год рождения', '').strip(),
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


app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = 'your_secret_key_here_change_in_production'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Инициализация SocketIO
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    manage_session=False,
    async_mode='threading',
    logger=True,
    engineio_logger=True
)

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
    """Главная страница"""
    if session.get('admin'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('public_dashboard'))


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
    if os.path.exists(COMPETITIONS_BASE_DIR):
        for comp_folder in os.listdir(COMPETITIONS_BASE_DIR):
            comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_folder)
            if os.path.isdir(comp_path):
                competitions.append(comp_folder)
    
    competitions.sort(reverse=True)
    return render_template('admin_dashboard.html', competitions=competitions)


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

    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])

    if len(scores) != len(techniques):
        return jsonify({'error': 'Invalid scores length'}), 400

    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    stage = stage_for_ops(comp_path, kata_key)
    files = get_stage_files(comp_path, kata_key, stage)

    # Получаем ФИО пары
    pairs_file = files['participants']
    pairs = CSVManager.read_csv(pairs_file)
    pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == int(pair_number)), None)
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

    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    technique_data = [{'техника': tech, 'details_json': json.dumps(d)} for tech, d in zip(techniques, details)]

    headers = ['техника', 'details_json']
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
    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])

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
            base_results = existing_results
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
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
