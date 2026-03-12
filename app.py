from flask import Flask, render_template, request, redirect, url_for, flash, session
from models import db, Participant, Pair, Judge, Score
from scoring import calculate_technique_score, calculate_judge_total_score, calculate_pair_final_score
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///judging_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Инициализация БД
with app.app_context():
    db.create_all()

# Простая аутентификация для админки
ADMIN_PASSWORD = 'admin123'

@app.route('/')
def index():
    return redirect(url_for('admin_login'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form['password']
        if password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Неверный пароль')
    return render_template('login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    participants = Participant.query.all()
    pairs = Pair.query.all()
    judges = Judge.query.all()
    return render_template('admin.html', participants=participants, pairs=pairs, judges=judges)

@app.route('/admin/add_participant', methods=['POST'])
def add_participant():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    name = request.form['name']
    birth_year = int(request.form['birth_year'])
    rank = request.form['rank']
    kyu = request.form['kyu']
    sports_school = request.form['sports_school']
    coach = request.form['coach']

    participant = Participant(name=name, birth_year=birth_year, rank=rank, kyu=kyu, sports_school=sports_school, coach=coach)
    db.session.add(participant)
    db.session.commit()
    flash('Участник добавлен')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_pair', methods=['POST'])
def add_pair():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    tori_id = int(request.form['tori_id'])
    uke_id = int(request.form['uke_id'])

    pair = Pair(tori_id=tori_id, uke_id=uke_id)
    db.session.add(pair)
    db.session.commit()
    flash('Пара добавлена')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_judge', methods=['POST'])
def add_judge():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    name = request.form['name']
    judge = Judge(name=name)
    db.session.add(judge)
    db.session.commit()
    flash('Судья добавлен')
    return redirect(url_for('admin_dashboard'))

@app.route('/judge/<int:judge_id>')
def judge_page(judge_id):
    judge = Judge.query.get_or_404(judge_id)
    pairs = Pair.query.all()
    return render_template('judge.html', judge=judge, pairs=pairs)

@app.route('/judge/<int:judge_id>/submit', methods=['POST'])
def submit_score(judge_id):
    pair_id = int(request.form['pair_id'])
    judge_position = int(request.form['judge_position'])

    technique_scores = []
    forgotten_flags = []

    for i in range(1, 18):
        penalties = []
        if request.form.get(f'tech{i}_minor1'):
            penalties.append(-1)
        if request.form.get(f'tech{i}_minor2'):
            penalties.append(-1)
        if request.form.get(f'tech{i}_medium'):
            penalties.append(-3)
        if request.form.get(f'tech{i}_big'):
            penalties.append(-5)
        if request.form.get(f'tech{i}_plus05'):
            penalties.append(0.5)
        if request.form.get(f'tech{i}_minus05'):
            penalties.append(-0.5)

        forgotten = bool(request.form.get(f'tech{i}_forgotten'))

        score = calculate_technique_score(penalties, forgotten)
        technique_scores.append(score)
        forgotten_flags.append(forgotten)

    score = Score(pair_id=pair_id, judge_id=judge_id, judge_position=judge_position,
                  technique_scores=technique_scores, forgotten_flags=forgotten_flags)
    db.session.add(score)
    db.session.commit()
    flash('Оценка отправлена')
    return redirect(url_for('judge_page', judge_id=judge_id))

@app.route('/results')
def results():
    pairs = Pair.query.all()
    judges = Judge.query.all()

    results = []
    for pair in pairs:
        judge_scores = {}
        for judge in judges:
            score = Score.query.filter_by(pair_id=pair.id, judge_id=judge.id).first()
            if score:
                total = calculate_judge_total_score(score.technique_scores, score.forgotten_flags)
                judge_scores[judge.id] = total
            else:
                judge_scores[judge.id] = 0.0

        judge_totals = list(judge_scores.values())
        final_score = calculate_pair_final_score(judge_totals)

        results.append({
            'pair': pair,
            'judge_scores': judge_scores,
            'final_score': final_score
        })

    # Сортировка по итоговому баллу убыванию
    results.sort(key=lambda x: x['final_score'], reverse=True)

    # Добавляем места
    for i, res in enumerate(results, 1):
        res['place'] = i

    return render_template('results.html', results=results, judges=judges)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
