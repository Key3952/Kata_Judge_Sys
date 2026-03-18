# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
from datetime import datetime
from csv_manager import CSVManager, CompetitionCSVManager
from scoring import calculate_pair_final_score
import json

# Импортируем DISCIPLINE_ROWS_BY_KEY из technics.py
from technics import DISCIPLINE_ROWS_BY_KEY

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


app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = 'your_secret_key_here_change_in_production'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

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


# ==================== АДМИНИСТРАТИВНАЯ ЧАСТЬ ====================

@app.route('/')
def index():
    """Главная страница"""
    if session.get('admin'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('public_dashboard'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Вход в админку"""
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
    """Выход из админки"""
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
            
            # Обработка баннера
            banner_path = ''
            if 'banner_file' in request.files:
                banner_file = request.files['banner_file']
                if banner_file and banner_file.filename != '':
                    # Сохраняем баннер в папку соревнования
                    from werkzeug.utils import secure_filename
                    filename = secure_filename(banner_file.filename)
                    banner_full_path = os.path.join(comp_full_path, 'banner_' + filename)
                    banner_file.save(banner_full_path)
                    # Сохраняем относительный путь в config
                    banner_path = os.path.join(comp_folder_name, 'banner_' + filename).replace('\\', '/')
            
            # Создаем файл config.json с информацией
            config = {
                'name': comp_name,
                'created': datetime.now().isoformat(),
                'status': 'open',
                'disciplines': [],
                'banner': banner_path
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
        if os.path.isdir(folder_path) and folder not in ['__pycache__']:
            # Получаем количество пар
            pairs_file = os.path.join(folder_path, 'participants_list.csv')
            pair_count = 0
            if os.path.exists(pairs_file):
                with open(pairs_file, 'r', encoding='utf-8') as f:
                    pair_count = len(f.readlines()) - 1  # минус заголовок
            
            disciplines.append({
                'key': folder,
                'name': get_discipline_display_name(folder),
                'pair_count': pair_count
            })
    
    # Получаем все доступные дисциплины
    all_disciplines = list(DISCIPLINE_ROWS_BY_KEY.keys())
    existing_disciplines = [d['key'] for d in disciplines]
    available_disciplines = [{'key': d, 'name': get_discipline_display_name(d)} for d in all_disciplines if d not in existing_disciplines]
    
    return render_template('edit_competition.html', 
                         comp_name=comp_name, 
                         config=config, 
                         disciplines=disciplines,
                         available_disciplines=available_disciplines)


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
    
    return jsonify({'success': True, 'message': 'Дисциплина добавлена'})


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
            
            # Функция проверки полноты данных участника
            # Сохраняем, если заполнены ФИО и год рождения, остальные поля опциональны
            def is_complete_participant(info):
                fio = info.get('ФИО', '').strip()
                birth_year = info.get('год рождения', '').strip()
                return bool(fio) and bool(birth_year)
            
            # Добавляем в глобальные CSV только если есть ФИО и год рождения
            if is_complete_participant(tori_info):
                CSVManager.add_row(PARTICIPANTS_CSV, tori_info, CompetitionCSVManager.PARTICIPANTS_HEADERS)
            if is_complete_participant(uke_info):
                CSVManager.add_row(PARTICIPANTS_CSV, uke_info, CompetitionCSVManager.PARTICIPANTS_HEADERS)
            
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
        
        # Парсим судей
        for judge_pos in range(1, 6):
            judge_name = request.form.get(f'judge_{judge_pos}_name', '').strip()
            if judge_name:
                judge_info = {
                    'место': judge_pos,
                    'ФИО': judge_name
                }
                
                judges_data.append(judge_info)
                
                # Добавляем в глобальный CSV судей
                temp_info = judge_info.copy()
                temp_info.pop('место')
                CSVManager.add_row(JUDGES_CSV, temp_info, CompetitionCSVManager.JUDGES_HEADERS)
        
        # Сохраняем в локальные CSV
        if pairs_data:
            pairs_file = os.path.join(disc_path, 'participants_list.csv')
            CSVManager.write_csv(pairs_file, pairs_data, CompetitionCSVManager.PAIRS_HEADERS)
        
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
    disc_path = os.path.join(comp_path, kata_key)

    # Получаем ФИО пары
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
    pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == int(pair_number)), None)
    if pair_obj:
        tori_fio = pair_obj.get('Тори_ФИО', '')
        uke_fio = pair_obj.get('Уке_ФИО', '')
        protocol_path = CompetitionCSVManager.get_protocol_path(comp_path, kata_key, judge_name, int(judge_position), tori_fio, uke_fio)
    else:
        protocol_path = os.path.join(disc_path, 'protocols', f'{judge_name}_{judge_position}_{pair_number}.csv')

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

    # Получаем ФИО пары
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    disc_path = os.path.join(comp_path, kata_key)
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
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
    protocol_path = CompetitionCSVManager.get_protocol_path(comp_path, kata_key, judge, int(pos), tori_fio, uke_fio)

    os.makedirs(os.path.dirname(protocol_path), exist_ok=True)

    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    technique_data = [{'техника': tech, 'details_json': json.dumps(d)} for tech, d in zip(techniques, details)]

    headers = ['техника', 'details_json']
    CSVManager.write_csv(protocol_path, technique_data, headers)

    if isFinal:
        # Обновляем final_protocol.csv
        final_protocol_path = os.path.join(disc_path, 'final_protocol.csv')
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
                'Тори': tori_fio,
                'Уке': uke_fio,
                'Судья 1': '',
                'Судья 2': '',
                'Судья 3': '',
                'Судья 4': '',
                'Судья 5': '',
                'Сумма': '',
                'Место': ''
            }
            all_results.append(pair_entry)
        
        # Обновляем оценку судьи
        judge_col = f'Судья {pos}'
        pair_entry[judge_col] = total
        
        # Пересчитываем сумму если все судьи заполнили
        scores = []
        for j in range(1, 6):
            s = pair_entry.get(f'Судья {j}', '')
            if s:
                try:
                    scores.append(float(s))
                except ValueError:
                    pass
        
        if len(scores) == 5:
            sorted_scores = sorted(scores)
            final = sum(sorted_scores[1:4])  # Убираем макс и мин
            pair_entry['Сумма'] = f'{final:.1f}'
        
        # Записываем обновленный финальный протокол
        CSVManager.write_csv(final_protocol_path, all_results, CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    
    return jsonify({'success': True})


@app.route('/api/<comp_name>/<kata_key>/get-judge-scores/<judge>/<int:pos>/<tori>/<uke>')
def get_judge_scores(comp_name, kata_key, judge, pos, tori, uke):
    """Получить существующие оценки судьи"""
    comp_path = os.path.join(COMPETITIONS_BASE_DIR, comp_name)
    scores = CompetitionCSVManager.read_judge_scores(comp_path, kata_key, judge, pos, tori, uke)
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
                        if os.path.isdir(folder_path) and folder not in ['__pycache__']:
                            disciplines.append({
                                'key': folder,
                                'name': get_discipline_display_name(folder)
                            })
                    
                    competitions.append({
                        'name': comp_folder,
                        'display_name': config.get('name', comp_folder),
                        'disciplines': disciplines
                    })
    
    competitions.sort(reverse=True)
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
    
    # Получаем список техник
    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    
    # Получаем список пар
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
    
    # Получаем список судей
    judges_file = os.path.join(disc_path, 'judges_list.csv')
    judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    
    if request.method == 'POST':
        judge_name = request.form.get('judge_name', '').strip()
        judge_position = int(request.form.get('judge_position', 1))
        pair_number = int(request.form.get('pair_number', 1))
        
        # Собираем оценки
        technique_scores = []
        technique_data = []
        
        for i, tech in enumerate(techniques):
            score_key = f'technique_{i}_score'
            score = float(request.form.get(score_key, 10.0))
            technique_scores.append(score)
            technique_data.append({
                'техника': tech,
                'оценка': score
            })
        
        # Сохраняем протокол судьи
        tori_fio = ''
        uke_fio = ''
        pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == pair_number), None)
        if pair_obj:
            tori_fio = pair_obj.get('Тори_ФИО', '')
            uke_fio = pair_obj.get('Уке_ФИО', '')
            protocol_path = CompetitionCSVManager.get_protocol_path(comp_path, kata_key, judge_name, judge_position, tori_fio, uke_fio)
        else:
            protocol_path = os.path.join(disc_path, 'protocols', f'{judge_name}_{judge_position}_{tori_fio}-{uke_fio}.csv')
        os.makedirs(os.path.dirname(protocol_path), exist_ok=True)
        
        # Записываем протокол
        headers = ['техника', 'оценка']
        CSVManager.write_csv(protocol_path, technique_data, headers)
        
        # Также сохраняем финальный протокол
        final_protocol_path = os.path.join(disc_path, 'final_protocol.csv')
        all_results = CSVManager.read_csv(final_protocol_path) if os.path.exists(final_protocol_path) else []
        
        # Ищем или создаем запись для этой пары
        pair_entry = None
        for entry in all_results:
            if int(entry.get('номер пары', 0)) == pair_number:
                pair_entry = entry
                break
        
        if not pair_entry:
            pair_obj = next((p for p in pairs if int(p.get('номер пары', 0)) == pair_number), None)
            if pair_obj:
                pair_entry = {
                    'номер пары': pair_number,
                    'Тори': pair_obj.get('Тори_ФИО', ''),
                    'Уке': pair_obj.get('Уке_ФИО', ''),
                    'Судья 1': '',
                    'Судья 2': '',
                    'Судья 3': '',
                    'Судья 4': '',
                    'Судья 5': '',
                    'Сумма': '',
                    'Место': ''
                }
                all_results.append(pair_entry)
        
        # Обновляем оценку судьи в финальном протоколе
        if pair_entry:
            total = sum(technique_scores)
            pair_entry[f'Судья {judge_position}'] = f'{total:.1f}'
            
            # Пересчитываем сумму если все судьи оценили
            scores = []
            for j in range(1, 6):
                s = pair_entry.get(f'Судья {j}', '')
                if s:
                    try:
                        scores.append(float(s))
                    except:
                        pass
            
            if len(scores) == 5:
                sorted_scores = sorted(scores)
                final = sum(sorted_scores[1:4])  # Убираем макс и мин
                pair_entry['Сумма'] = f'{final:.1f}'
            
            # Записываем обновленный финальный протокол
            final_headers = ['номер пары', 'Тори', 'Уке', 'Судья 1', 'Судья 2', 'Судья 3', 'Судья 4', 'Судья 5', 'Сумма', 'Место']
            CSVManager.write_csv(final_protocol_path, all_results, final_headers)
        
        flash('Оценки сохранены', 'success')
        return redirect(url_for('judge_page', comp_name=comp_name, kata_key=kata_key))
    
    return render_template('judge_form.html',
                         comp_name=comp_name,
                         kata_key=kata_key,
                         kata_name=get_discipline_display_name(kata_key),
                         techniques=techniques,
                         pairs=pairs,
                         judges=judges)


# ==================== ТАБЛО ====================

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
    
    # Читаем конфиг
    config_file = os.path.join(comp_path, 'config.json')
    config = {}
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    
    # Получаем список техник
    techniques = DISCIPLINE_ROWS_BY_KEY.get(kata_key, [])
    
    # Получаем пары
    pairs_file = os.path.join(disc_path, 'participants_list.csv')
    pairs = CSVManager.read_csv(pairs_file) if os.path.exists(pairs_file) else []
    
    # Получаем судей
    judges_file = os.path.join(disc_path, 'judges_list.csv')
    judges = CSVManager.read_csv(judges_file) if os.path.exists(judges_file) else []
    
    # Пытаемся прочитать результаты из final_protocol.csv
    final_protocol_path = os.path.join(comp_path, kata_key, 'final_protocol.csv')
    if os.path.exists(final_protocol_path):
        existing_results = CompetitionCSVManager.read_final_protocol(comp_path, kata_key)
        if existing_results:
            # Если есть данные в CSV, используем их
            return render_template('tablo.html',
                                 comp_name=comp_name,
                                 kata_key=kata_key,
                                 kata_name=get_discipline_display_name(kata_key),
                                 judges=judges,
                                 results=existing_results,
                                 config=config)
    
    # Если нет данных в CSV, рассчитываем заново
    results = []
    for pair in pairs:
        pair_number = int(pair.get('номер пары', 0))
        judge_scores = []
        
        for judge in judges:
            judge_pos = int(judge.get('место', 0))
            judge_name = judge.get('ФИО', '')
            scores = CompetitionCSVManager.read_judge_scores(
                comp_path, kata_key, judge_name, judge_pos, pair.get('Тори_ФИО', ''), pair.get('Уке_ФИО', '')
            )
            
            if scores:
                # Рассчитываем scores из details
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
        
        # Если все судьи заполнили - рассчитываем финальный балл
        if len(judge_scores) == 5 and all(s is not None for s in judge_scores):
            final_score = calculate_pair_final_score(judge_scores)
        else:
            final_score = None
        
        results.append({
            'pair_number': pair_number,
            'tori': pair.get('Тори_ФИО', ''),
            'uke': pair.get('Уке_ФИО', ''),
            'judge_scores': judge_scores,
            'final_score': final_score
        })
    
    # Сортируем по финальному баллу (убывание), если есть
    results_with_scores = [r for r in results if r['final_score'] is not None]
    results_without_scores = [r for r in results if r['final_score'] is None]
    
    results_with_scores.sort(key=lambda x: x['final_score'], reverse=True)
    
    # Присваиваем места
    for idx, result in enumerate(results_with_scores):
        result['place'] = idx + 1
    
    final_results = results_with_scores + results_without_scores
    
    # Записываем результаты в final_protocol.csv
    rows = []
    for result in final_results:
        row = {
            'номер пары': result['pair_number'],
            'Тори': result['tori'],
            'Уке': result['uke'],
            'Судья 1': result['judge_scores'][0] if len(result['judge_scores']) > 0 and result['judge_scores'][0] is not None else '',
            'Судья 2': result['judge_scores'][1] if len(result['judge_scores']) > 1 and result['judge_scores'][1] is not None else '',
            'Судья 3': result['judge_scores'][2] if len(result['judge_scores']) > 2 and result['judge_scores'][2] is not None else '',
            'Судья 4': result['judge_scores'][3] if len(result['judge_scores']) > 3 and result['judge_scores'][3] is not None else '',
            'Судья 5': result['judge_scores'][4] if len(result['judge_scores']) > 4 and result['judge_scores'][4] is not None else '',
            'Сумма': result['final_score'] if result['final_score'] is not None else '',
            'Место': result.get('place', '')
        }
        rows.append(row)
    CSVManager.write_csv(final_protocol_path, rows, CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    
    return render_template('tablo.html',
                         comp_name=comp_name,
                         kata_key=kata_key,
                         kata_name=get_discipline_display_name(kata_key),
                         judges=judges,
                         results=final_results,
                         config=config)


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', message='Страница не найдена'), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', message='Внутренняя ошибка сервера'), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
