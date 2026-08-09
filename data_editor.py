# data_editor.py
"""
Модуль для редактирования данных через веб-интерфейс.
Позволяет управлять участниками, судьями и парами через БД.
"""

from typing import List, Dict, Any, Optional
from database import db, Participant, Judge, Pair, Discipline, Competition, JudgeList


class DataEditor:
    """Класс для CRUD операций с данными"""
    
    # ==================== УЧАСТНИКИ ====================
    
    @staticmethod
    def get_all_participants(search: str = None, limit: int = 100) -> List[Dict]:
        """Получить всех участников с опциональным поиском"""
        query = Participant.query
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Participant.fio.ilike(search_pattern)) |
                (Participant.birth_year.like(search_pattern))
            )
        
        participants = query.order_by(Participant.fio).limit(limit).all()
        return [p.to_dict() for p in participants]
    
    @staticmethod
    def get_participant_by_id(participant_id: int) -> Optional[Dict]:
        """Получить участника по ID"""
        participant = Participant.query.get(participant_id)
        return participant.to_dict() if participant else None
    
    @staticmethod
    def create_participant(data: Dict[str, str]) -> Dict[str, Any]:
        """Создать нового участника"""
        # Проверка на дубликат
        existing = Participant.query.filter_by(
            fio=data.get('ФИО', '').strip(),
            birth_year=data.get('год рождения', '').strip()
        ).first()
        
        if existing:
            return {'success': False, 'error': 'Участник уже существует', 'id': existing.id}
        
        participant = Participant.from_dict(data)
        db.session.add(participant)
        db.session.commit()
        
        return {'success': True, 'id': participant.id, 'data': participant.to_dict()}
    
    @staticmethod
    def update_participant(participant_id: int, data: Dict[str, str]) -> Dict[str, Any]:
        """Обновить данные участника"""
        participant = Participant.query.get(participant_id)
        if not participant:
            return {'success': False, 'error': 'Участник не найден'}
        
        # Обновляем поля
        for key, value in data.items():
            if key == 'ФИО':
                participant.fio = value.strip()
            elif key == 'год рождения':
                participant.birth_year = value.strip()
            elif key == 'разряд':
                participant.rank = value.strip()
            elif key == 'кю':
                participant.ky = value.strip()
            elif key == 'СШ':
                participant.school = value.strip()
            elif key == 'тренер':
                participant.coach = value.strip()
        
        db.session.commit()
        return {'success': True, 'data': participant.to_dict()}
    
    @staticmethod
    def delete_participant(participant_id: int) -> Dict[str, Any]:
        """Удалить участника"""
        participant = Participant.query.get(participant_id)
        if not participant:
            return {'success': False, 'error': 'Участник не найден'}
        
        db.session.delete(participant)
        db.session.commit()
        return {'success': True}
    
    # ==================== СУДЬИ ====================
    
    @staticmethod
    def get_all_judges(search: str = None, limit: int = 100) -> List[Dict]:
        """Получить всех судей"""
        query = Judge.query
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(Judge.fio.ilike(search_pattern))
        
        judges = query.order_by(Judge.fio).limit(limit).all()
        return [j.to_dict() for j in judges]
    
    @staticmethod
    def get_judge_by_id(judge_id: int) -> Optional[Dict]:
        """Получить судью по ID"""
        judge = Judge.query.get(judge_id)
        return judge.to_dict() if judge else None
    
    @staticmethod
    def create_judge(data: Dict[str, str]) -> Dict[str, Any]:
        """Создать нового судью"""
        fio = data.get('ФИО', '').strip()
        
        # Проверка на дубликат
        existing = Judge.query.filter_by(fio=fio).first()
        if existing:
            return {'success': False, 'error': 'Судья уже существует', 'id': existing.id}
        
        judge = Judge(fio=fio)
        db.session.add(judge)
        db.session.commit()
        
        return {'success': True, 'id': judge.id, 'data': judge.to_dict()}
    
    @staticmethod
    def update_judge(judge_id: int, data: Dict[str, str]) -> Dict[str, Any]:
        """Обновить данные судьи"""
        judge = Judge.query.get(judge_id)
        if not judge:
            return {'success': False, 'error': 'Судья не найден'}
        
        fio = data.get('ФИО', '').strip()
        if fio:
            judge.fio = fio
        
        db.session.commit()
        return {'success': True, 'data': judge.to_dict()}
    
    @staticmethod
    def delete_judge(judge_id: int) -> Dict[str, Any]:
        """Удалить судью"""
        judge = Judge.query.get(judge_id)
        if not judge:
            return {'success': False, 'error': 'Судья не найден'}
        
        db.session.delete(judge)
        db.session.commit()
        return {'success': True}
    
    # ==================== ПАРЫ ====================
    
    @staticmethod
    def get_pairs_for_discipline(discipline_id: int, stage: str = 'prelim') -> List[Dict]:
        """Получить все пары для дисциплины"""
        pairs = Pair.query.filter_by(
            discipline_id=discipline_id,
            stage=stage
        ).order_by(Pair.pair_number).all()
        return [p.to_dict() for p in pairs]
    
    @staticmethod
    def create_pair(discipline_id: int, data: Dict[str, str], stage: str = 'prelim') -> Dict[str, Any]:
        """Создать новую пару"""
        try:
            pair = Pair.from_dict(discipline_id, data, stage)
            db.session.add(pair)
            db.session.commit()
            return {'success': True, 'id': pair.id, 'data': pair.to_dict()}
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def update_pair(pair_id: int, data: Dict[str, str]) -> Dict[str, Any]:
        """Обновить данные пары"""
        pair = Pair.query.get(pair_id)
        if not pair:
            return {'success': False, 'error': 'Пара не найдена'}
        
        # Обновляем поля
        if 'номер пары' in data:
            pair.pair_number = int(data['номер пары'])
        if 'Тори_ФИО' in data:
            pair.tori_fio = data['Тори_ФИО'].strip()
        if 'Тори_год рождения' in data:
            pair.tori_birth_year = data['Тори_год рождения'].strip()
        if 'Тори_разряд' in data:
            pair.tori_rank = data['Тори_разряд'].strip()
        if 'Тори_кю' in data:
            pair.tori_ky = data['Тори_кю'].strip()
        if 'Тори_СШ' in data:
            pair.tori_school = data['Тори_СШ'].strip()
        if 'Тори_тренер' in data:
            pair.tori_coach = data['Тори_тренер'].strip()
        if 'Уке_ФИО' in data:
            pair.uke_fio = data['Уке_ФИО'].strip()
        if 'Уке_год рождения' in data:
            pair.uke_birth_year = data['Уке_год рождения'].strip()
        if 'Уке_разряд' in data:
            pair.uke_rank = data['Уке_разряд'].strip()
        if 'Уке_кю' in data:
            pair.uke_ky = data['Уке_кю'].strip()
        if 'Уке_СШ' in data:
            pair.uke_school = data['Уке_СШ'].strip()
        if 'Уке_тренер' in data:
            pair.uke_coach = data['Уке_тренер'].strip()
        
        db.session.commit()
        return {'success': True, 'data': pair.to_dict()}
    
    @staticmethod
    def delete_pair(pair_id: int) -> Dict[str, Any]:
        """Удалить пару"""
        pair = Pair.query.get(pair_id)
        if not pair:
            return {'success': False, 'error': 'Пара не найдена'}
        
        db.session.delete(pair)
        db.session.commit()
        return {'success': True}
    
    # ==================== СУДЬИ В ДИСЦИПЛИНЕ ====================
    
    @staticmethod
    def get_judge_list_for_discipline(discipline_id: int, stage: str = 'prelim') -> List[Dict]:
        """Получить список судей для дисциплины"""
        judges = JudgeList.query.filter_by(
            discipline_id=discipline_id,
            stage=stage
        ).order_by(JudgeList.position).all()
        return [j.to_dict() for j in judges]
    
    @staticmethod
    def add_judge_to_discipline(discipline_id: int, position: int, judge_fio: str, stage: str = 'prelim') -> Dict[str, Any]:
        """Добавить судью в дисциплину"""
        # Проверка: позиция уже занята
        existing = JudgeList.query.filter_by(
            discipline_id=discipline_id,
            position=position,
            stage=stage
        ).first()
        
        if existing:
            return {'success': False, 'error': f'Позиция {position} уже занята'}
        
        judge_list = JudgeList(
            discipline_id=discipline_id,
            position=position,
            judge_fio=judge_fio.strip(),
            stage=stage
        )
        db.session.add(judge_list)
        db.session.commit()
        
        return {'success': True, 'data': judge_list.to_dict()}
    
    @staticmethod
    def update_judge_in_discipline(judge_list_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Обновить судью в дисциплине"""
        judge_list = JudgeList.query.get(judge_list_id)
        if not judge_list:
            return {'success': False, 'error': 'Запись не найдена'}
        
        if 'место' in data:
            judge_list.position = int(data['место'])
        if 'ФИО' in data:
            judge_list.judge_fio = data['ФИО'].strip()
        
        db.session.commit()
        return {'success': True, 'data': judge_list.to_dict()}
    
    @staticmethod
    def remove_judge_from_discipline(judge_list_id: int) -> Dict[str, Any]:
        """Удалить судью из дисциплины"""
        judge_list = JudgeList.query.get(judge_list_id)
        if not judge_list:
            return {'success': False, 'error': 'Запись не найдена'}
        
        db.session.delete(judge_list)
        db.session.commit()
        return {'success': True}
    
    # ==================== СОРЕВНОВАНИЯ ====================
    
    @staticmethod
    def get_all_competitions() -> List[Dict]:
        """Получить все соревнования"""
        competitions = Competition.query.order_by(Competition.created_at.desc()).all()
        return [c.to_dict() for c in competitions]
    
    @staticmethod
    def get_competition_by_folder(folder_name: str) -> Optional[Dict]:
        """Получить соревнование по имени папки"""
        competition = Competition.query.filter_by(folder_name=folder_name).first()
        return competition.to_dict() if competition else None
    
    @staticmethod
    def create_competition(name: str, folder_name: str) -> Dict[str, Any]:
        """Создать новое соревнование"""
        # Проверка на дубликат
        existing = Competition.query.filter_by(folder_name=folder_name).first()
        if existing:
            return {'success': False, 'error': 'Соревнование уже существует'}
        
        competition = Competition(
            name=name.strip(),
            folder_name=folder_name.strip()
        )
        db.session.add(competition)
        db.session.commit()
        
        return {'success': True, 'id': competition.id, 'data': competition.to_dict()}
    
    @staticmethod
    def update_competition_status(folder_name: str, status: str) -> Dict[str, Any]:
        """Обновить статус соревнования"""
        competition = Competition.query.filter_by(folder_name=folder_name).first()
        if not competition:
            return {'success': False, 'error': 'Соревнование не найдено'}
        
        competition.status = status
        db.session.commit()
        return {'success': True, 'data': competition.to_dict()}
    
    @staticmethod
    def delete_competition(folder_name: str) -> Dict[str, Any]:
        """Удалить соревнование"""
        competition = Competition.query.filter_by(folder_name=folder_name).first()
        if not competition:
            return {'success': False, 'error': 'Соревнование не найдено'}
        
        db.session.delete(competition)
        db.session.commit()
        return {'success': True}
