from flask_sqlalchemy import SQLAlchemy
from flask import Flask

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///judging_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    birth_year = db.Column(db.Integer, nullable=False)
    rank = db.Column(db.String(50))
    kyu = db.Column(db.String(50))
    sports_school = db.Column(db.String(100))
    coach = db.Column(db.String(100))

class Pair(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tori_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    uke_id = db.Column(db.Integer, db.ForeignKey('participant.id'), nullable=False)
    tori = db.relationship('Participant', foreign_keys=[tori_id])
    uke = db.relationship('Participant', foreign_keys=[uke_id])

class Judge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair_id = db.Column(db.Integer, db.ForeignKey('pair.id'), nullable=False)
    judge_id = db.Column(db.Integer, db.ForeignKey('judge.id'), nullable=False)
    judge_position = db.Column(db.Integer, nullable=False)  # 1-5
    technique_scores = db.Column(db.JSON, nullable=False)  # list of 17 scores
    forgotten_flags = db.Column(db.JSON, nullable=False)  # list of 17 booleans
    pair = db.relationship('Pair')
    judge = db.relationship('Judge')
