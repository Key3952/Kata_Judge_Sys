# test_database.py
"""
Тесты для модулей базы данных и редактора данных.
"""

import unittest
import os
import tempfile
import shutil
from app import app
from database import db, Participant, Judge, Competition, Discipline, Pair
from data_editor import DataEditor


class TestDatabaseModels(unittest.TestCase):
    """Тесты моделей базы данных"""
    
    def setUp(self):
        """Настройка тестовой БД"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_judo_kata.db')
        
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{self.db_path}'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        with app.app_context():
            db.create_all()
    
    def tearDown(self):
        """Очистка после теста"""
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.temp_dir)
    
    def test_create_participant(self):
        """Создание участника"""
        with app.app_context():
            p = Participant(
                fio='Иванов Иван',
                birth_year='2000',
                rank='1 дан',
                ky='5',
                school='СШ1',
                coach='Петров П.'
            )
            db.session.add(p)
            db.session.commit()
            
            self.assertIsNotNone(p.id)
            self.assertEqual(p.fio, 'Иванов Иван')
    
    def test_create_judge(self):
        """Создание судьи"""
        with app.app_context():
            j = Judge(fio='Судьин Судья Иванович')
            db.session.add(j)
            db.session.commit()
            
            self.assertIsNotNone(j.id)
            self.assertEqual(j.fio, 'Судьин Судья Иванович')
    
    def test_unique_participant_constraint(self):
        """Проверка уникальности участника (ФИО + год рождения)"""
        with app.app_context():
            p1 = Participant(fio='Иванов Иван', birth_year='2000')
            db.session.add(p1)
            db.session.commit()
            
            # Попытка создать дубликат должна вызвать ошибку
            p2 = Participant(fio='Иванов Иван', birth_year='2000')
            db.session.add(p2)
            
            with self.assertRaises(Exception):
                db.session.commit()
            
            db.session.rollback()
    
    def test_unique_judge_constraint(self):
        """Проверка уникальности судьи (ФИО)"""
        with app.app_context():
            j1 = Judge(fio='Судьин Судья')
            db.session.add(j1)
            db.session.commit()
            
            j2 = Judge(fio='Судьин Судья')
            db.session.add(j2)
            
            with self.assertRaises(Exception):
                db.session.commit()
            
            db.session.rollback()


class TestDataEditor(unittest.TestCase):
    """Тесты CRUD операций через DataEditor"""
    
    def setUp(self):
        """Настройка тестовой БД"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_judo_kata.db')
        
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{self.db_path}'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        with app.app_context():
            db.create_all()
    
    def tearDown(self):
        """Очистка после теста"""
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.temp_dir)
    
    def test_create_and_get_participant(self):
        """Создание и получение участника"""
        with app.app_context():
            data = {
                'ФИО': 'Иванов Иван',
                'год рождения': '2000',
                'разряд': '1 дан',
                'кю': '',
                'СШ': 'СШ1',
                'тренер': 'Петров'
            }
            result = DataEditor.create_participant(data)
            
            self.assertTrue(result['success'])
            self.assertIn('id', result)
            
            # Получение участника
            participant = DataEditor.get_participant_by_id(result['id'])
            self.assertIsNotNone(participant)
            self.assertEqual(participant['ФИО'], 'Иванов Иван')
    
    def test_update_participant(self):
        """Обновление участника"""
        with app.app_context():
            # Создаем
            data = {'ФИО': 'Иванов Иван', 'год рождения': '2000'}
            result = DataEditor.create_participant(data)
            pid = result['id']
            
            # Обновляем
            update_data = {'разряд': '2 дан', 'тренер': 'Новый Тренер'}
            result = DataEditor.update_participant(pid, update_data)
            
            self.assertTrue(result['success'])
            
            # Проверяем
            participant = DataEditor.get_participant_by_id(pid)
            self.assertEqual(participant['разряд'], '2 дан')
            self.assertEqual(participant['тренер'], 'Новый Тренер')
    
    def test_delete_participant(self):
        """Удаление участника"""
        with app.app_context():
            # Создаем
            data = {'ФИО': 'Иванов Иван', 'год рождения': '2000'}
            result = DataEditor.create_participant(data)
            pid = result['id']
            
            # Удаляем
            result = DataEditor.delete_participant(pid)
            self.assertTrue(result['success'])
            
            # Проверяем что удален
            participant = DataEditor.get_participant_by_id(pid)
            self.assertIsNone(participant)
    
    def test_duplicate_participant_error(self):
        """Ошибка при создании дубликата участника"""
        with app.app_context():
            data = {'ФИО': 'Иванов Иван', 'год рождения': '2000'}
            
            result1 = DataEditor.create_participant(data)
            self.assertTrue(result1['success'])
            
            result2 = DataEditor.create_participant(data)
            self.assertFalse(result2['success'])
            self.assertIn('уже существует', result2['error'])
    
    def test_create_and_get_judge(self):
        """Создание и получение судьи"""
        with app.app_context():
            data = {'ФИО': 'Судьин Судья Иванович'}
            result = DataEditor.create_judge(data)
            
            self.assertTrue(result['success'])
            
            judges = DataEditor.get_all_judges()
            self.assertEqual(len(judges), 1)
            self.assertEqual(judges[0]['ФИО'], 'Судьин Судья Иванович')
    
    def test_search_participants(self):
        """Поиск участников"""
        with app.app_context():
            # Создаем нескольких участников
            DataEditor.create_participant({'ФИО': 'Иванов Иван', 'год рождения': '2000'})
            DataEditor.create_participant({'ФИО': 'Петров Петр', 'год рождения': '2001'})
            DataEditor.create_participant({'ФИО': 'Сидоров Сидор', 'год рождения': '2002'})
            
            # Поиск по ФИО
            results = DataEditor.get_all_participants(search='Иванов')
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['ФИО'], 'Иванов Иван')
            
            # Поиск по году рождения
            results = DataEditor.get_all_participants(search='2001')
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['ФИО'], 'Петров Петр')
    
    def test_search_judges(self):
        """Поиск судей"""
        with app.app_context():
            DataEditor.create_judge({'ФИО': 'Судьин А'})
            DataEditor.create_judge({'ФИО': 'Судьин Б'})
            
            results = DataEditor.get_all_judges(search='А')
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['ФИО'], 'Судьин А')


class TestCompetitionModels(unittest.TestCase):
    """Тесты моделей соревнований"""
    
    def setUp(self):
        """Настройка тестовой БД"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_judo_kata.db')
        
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{self.db_path}'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        with app.app_context():
            db.create_all()
    
    def tearDown(self):
        """Очистка после теста"""
        with app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.temp_dir)
    
    def test_create_competition(self):
        """Создание соревнования"""
        with app.app_context():
            comp = Competition(
                name='Тестовое соревнование',
                folder_name='test_comp_2024'
            )
            db.session.add(comp)
            db.session.commit()
            
            self.assertIsNotNone(comp.id)
            self.assertEqual(comp.name, 'Тестовое соревнование')
            self.assertEqual(comp.status, 'open')
    
    def test_create_discipline(self):
        """Создание дисциплины"""
        with app.app_context():
            comp = Competition(name='Тест', folder_name='test_comp')
            db.session.add(comp)
            db.session.commit()
            
            disc = Discipline(
                competition_id=comp.id,
                kata_key='nagenokata'
            )
            db.session.add(disc)
            db.session.commit()
            
            self.assertIsNotNone(disc.id)
            self.assertEqual(disc.kata_key, 'nagenokata')
    
    def test_create_pair(self):
        """Создание пары участников"""
        with app.app_context():
            comp = Competition(name='Тест', folder_name='test_comp')
            db.session.add(comp)
            db.session.commit()
            
            disc = Discipline(competition_id=comp.id, kata_key='nagenokata')
            db.session.add(disc)
            db.session.commit()
            
            pair = Pair(
                discipline_id=disc.id,
                pair_number=1,
                tori_fio='Тори Тестов',
                uke_fio='Уке Тестов'
            )
            db.session.add(pair)
            db.session.commit()
            
            self.assertIsNotNone(pair.id)
            self.assertEqual(pair.pair_number, 1)
            self.assertEqual(pair.tori_fio, 'Тори Тестов')


if __name__ == '__main__':
    unittest.main()
