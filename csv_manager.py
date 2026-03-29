# csv_manager.py
import csv
import os
import json
from pathlib import Path
from typing import List, Dict, Optional

class CSVManager:
    """Менеджер для работы с CSV файлами участников и судей"""
    
    @staticmethod
    def ensure_csv_exists(filepath: str, headers: List[str]) -> None:
        """Создает CSV файл с заголовками, если его нет"""
        if not os.path.exists(filepath):
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
    
    @staticmethod
    def read_csv(filepath: str) -> List[Dict]:
        """Читает CSV файл и возвращает список словарей"""
        if not os.path.exists(filepath):
            return []
        
        rows = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    
    @staticmethod
    def write_csv(filepath: str, rows: List[Dict], headers: List[str]) -> None:
        """Пишет данные в CSV файл"""
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
    
    @staticmethod
    def add_row(filepath: str, row: Dict, headers: List[str]) -> None:
        """Добавляет новую строку в CSV файл"""
        rows = CSVManager.read_csv(filepath)
        
        # Проверяем, что ФИО не пусто
        fio = row.get('ФИО', '').strip()
        if not fio:
            return
        
        # Для глобальной базы участников требуется и ФИО и год рождения
        # (определяется по наличию ключа 'год рождения' в row)
        if 'год рождения' in row:
            birth_year = row.get('год рождения', '').strip()
            if not birth_year:
                return  # Не сохраняем участников без года рождения
        
        # Проверяем, есть ли уже такая запись (по ФИО и году рождения для участников)
        if 'год рождения' in row:
            birth_year = row.get('год рождения', '').strip()
            for existing_row in rows:
                existing_fio = existing_row.get('ФИО', '').strip()
                existing_birth = existing_row.get('год рождения', '').strip()
                if existing_fio.lower() == fio.lower() and existing_birth == birth_year:
                    return  # Уже существует
        else:
            # Для судей - проверяем по ФИО
            for existing_row in rows:
                if existing_row.get('ФИО', '').strip().lower() == fio.lower():
                    return  # Уже существует
        
        rows.append(row)
        CSVManager.write_csv(filepath, rows, headers)
    
    @staticmethod
    def search_by_name(filepath: str, name: str) -> Optional[Dict]:
        """Ищет запись по ФИО (частичное совпадение)"""
        rows = CSVManager.read_csv(filepath)
        for row in rows:
            if row.get('ФИО', '').lower() == name.lower():
                return row
        return None
    
    @staticmethod
    def get_name_suggestions(filepath: str, prefix: str, limit: int = 10) -> List[str]:
        """Возвращает подсказки по префиксу ФИО"""
        rows = CSVManager.read_csv(filepath)
        suggestions = []
        prefix_lower = prefix.lower()
        
        for row in rows:
            name = row.get('ФИО', '')
            if name.lower().startswith(prefix_lower) and name not in suggestions:
                suggestions.append(name)
                if len(suggestions) >= limit:
                    break
        
        return suggestions


class CompetitionCSVManager:
    """Менеджер для работы с CSV файлами соревнования"""
    
    PARTICIPANTS_HEADERS = ['ФИО', 'год рождения', 'разряд', 'кю', 'СШ', 'тренер']
    JUDGES_HEADERS = ['ФИО']
    PAIRS_HEADERS = ['номер пары', 'Тори_ФИО', 'Тори_год рождения', 'Тори_разряд', 'Тори_кю', 'Тори_СШ', 'Тори_тренер', 'Уке_ФИО', 'Уке_год рождения', 'Уке_разряд', 'Уке_кю', 'Уке_СШ', 'Уке_тренер']
    JUDGES_LIST_HEADERS = ['место', 'ФИО']
    FINAL_PROTOCOL_HEADERS = ['номер пары', 'Тори', 'Уке', 'Судья 1', 'Судья 2', 'Судья 3', 'Судья 4', 'Судья 5', 'Сумма', 'Место']
    
    @staticmethod
    def get_discipline_path(comp_base_path: str, discipline_key: str) -> str:
        """Возвращает путь до папки дисциплины"""
        return os.path.join(comp_base_path, discipline_key)
    
    @staticmethod
    def create_discipline_structure(comp_base_path: str, discipline_key: str) -> None:
        """Создает структуру папок и файлов для дисциплины"""
        disc_path = CompetitionCSVManager.get_discipline_path(comp_base_path, discipline_key)
        
        # Создаем папки
        os.makedirs(disc_path, exist_ok=True)
        os.makedirs(os.path.join(disc_path, 'protocols'), exist_ok=True)
        
        # Создаем пустые CSV файлы
        participants_file = os.path.join(disc_path, 'participants_list.csv')
        judges_file = os.path.join(disc_path, 'judges_list.csv')
        final_protocol_file = os.path.join(disc_path, 'final_protocol.csv')
        
        CSVManager.ensure_csv_exists(participants_file, CompetitionCSVManager.PAIRS_HEADERS)
        CSVManager.ensure_csv_exists(judges_file, CompetitionCSVManager.JUDGES_LIST_HEADERS)
        CSVManager.ensure_csv_exists(final_protocol_file, CompetitionCSVManager.FINAL_PROTOCOL_HEADERS)
    
    @staticmethod
    def get_protocol_path(comp_base_path: str, discipline_key: str, judge_name: str, judge_position: int, tori: str, uke: str) -> str:
        """Возвращает путь до файла протокола судьи"""
        disc_path = CompetitionCSVManager.get_discipline_path(comp_base_path, discipline_key)
        protocols_path = os.path.join(disc_path, 'protocols')
        return os.path.join(protocols_path, f'{judge_name}_{judge_position}_{tori}-{uke}.csv')

    @staticmethod
    def save_judge_scores(comp_base_path: str, discipline_key: str, pair_number: int, 
                         judge_position: int, technique_scores: List[float], 
                         technique_names: List[str]) -> None:
        """Сохраняет оценки судьи в файл"""
        protocol_path = CompetitionCSVManager.get_protocol_path(
            comp_base_path, discipline_key, pair_number, judge_position
        )
        
        rows = []
        for i, (tech_name, score) in enumerate(zip(technique_names, technique_scores)):
            rows.append({
                'техника': tech_name,
                'оценка': score
            })
        
        headers = ['техника', 'оценка']
        CSVManager.write_csv(protocol_path, rows, headers)
    
    @staticmethod
    def read_judge_scores(comp_base_path: str, discipline_key: str, judge_name: str, judge_position: int, tori: str, uke: str) -> Dict[str, Dict]:
        """Читает детали оценок судьи из файла"""
        protocol_path = CompetitionCSVManager.get_protocol_path(
            comp_base_path, discipline_key, judge_name, judge_position, tori, uke
        )
        
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
        
        return details

    @staticmethod
    def read_final_protocol(comp_base_path: str, discipline_key: str) -> List[Dict]:
        """Читает финальный протокол из файла"""
        final_protocol_path = os.path.join(comp_base_path, discipline_key, 'final_protocol.csv')
        if not os.path.exists(final_protocol_path):
            return []
        
        rows = CSVManager.read_csv(final_protocol_path)
        results = []
        for row in rows:
            judge_scores = []
            for i in range(1, 6):
                score_str = row.get(f'Судья {i}', '')
                try:
                    score = float(score_str) if score_str else None
                except ValueError:
                    score = None
                judge_scores.append(score)
            
            try:
                final_score = float(row.get('Сумма', '')) if row.get('Сумма') else None
            except ValueError:
                final_score = None
            
            try:
                place = int(row.get('Место', '')) if row.get('Место') else None
            except ValueError:
                place = None
            
            results.append({
                'pair_number': int(row.get('номер пары', 0)),
                'tori': row.get('Тори', ''),
                'uke': row.get('Уке', ''),
                'judge_scores': judge_scores,
                'final_score': final_score,
                'place': place
            })
        
        return results
