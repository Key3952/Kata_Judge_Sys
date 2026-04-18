# csv_manager.py
import csv
import os
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple

class CSVManager:
    """Менеджер для работы с CSV файлами участников и судей"""

    @staticmethod
    def _read_text_with_fallback(filepath: str) -> str:
        """Читает текстовый файл с fallback по кодировкам."""
        with open(filepath, 'rb') as fb:
            raw = fb.read()
        for enc in ('utf-8-sig', 'utf-8', 'cp1251', 'latin-1'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        # Крайний случай: не падаем, а заменяем битые символы
        return raw.decode('utf-8', errors='replace')
    
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
        try:
            text = CSVManager._read_text_with_fallback(filepath)
            import io
            f = io.StringIO(text)
            reader = csv.DictReader(f)
            # Поврежденный/пустой заголовок: считаем файл пустым
            if reader.fieldnames is None:
                return []
            for row in reader:
                if row is None:
                    continue
                # Приводим None к пустым строкам
                clean = {k: ('' if v is None else v) for k, v in row.items() if k is not None}
                rows.append(clean)
        except (csv.Error, TypeError, UnicodeDecodeError):
            # Некорректный CSV — не падаем в рантайме
            return []
        return rows
    
    @staticmethod
    def write_csv(filepath: str, rows: List[Dict], headers: List[str]) -> None:
        """Пишет данные в CSV файл"""
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
            writer.writeheader()
            normalized_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                normalized_rows.append({h: row.get(h, '') for h in headers})
            writer.writerows(normalized_rows)
    
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
    def upsert_participant(filepath: str, row: Dict, headers: List[str]) -> None:
        """Добавляет или полностью перезаписывает участника по ФИО (без учёта регистра).

        Запись создаётся/обновляется только если заполнены все поля из headers.
        При совпадении ФИО с существующей строкой данные заменяются новыми.
        """
        fio = row.get('ФИО', '').strip()
        if not fio:
            return
        for h in headers:
            val = row.get(h, '')
            if val is None or str(val).strip() == '':
                return
        rows = CSVManager.read_csv(filepath)
        new_row = {h: str(row.get(h, '')).strip() for h in headers}
        found_idx = None
        for i, existing in enumerate(rows):
            if existing.get('ФИО', '').strip().lower() == fio.lower():
                found_idx = i
                break
        if found_idx is not None:
            rows[found_idx] = new_row
        else:
            rows.append(new_row)
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


def sort_prelim_results_for_final_transfer(prelim_results: List[dict]) -> List[dict]:
    """Порядок переноса в финал: по возрастанию «Место»; если места нет — по убыванию «Сумма», затем «номер пары»."""

    def key(r: dict):
        m = str(r.get('Место', '')).strip()
        try:
            s = float(str(r.get('Сумма', '0')).replace(',', '.'))
        except ValueError:
            s = 0.0
        pn = int(str(r.get('номер пары', 0)) or 0)
        if m.isdigit():
            ip = int(m)
            if ip > 0:
                return (0, ip, 0.0, 0)
        return (1, 0, -s, pn)

    return sorted(prelim_results, key=key)


def safe_float(value: str, default: float = 0.0) -> float:
    """Безопасное преобразование строки в float"""
    try:
        return float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        return default


def safe_int(value: str, default: int = 0) -> int:
    """Безопасное преобразование строки в int"""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def normalize_protocol_token(s: str) -> str:
    """Единая нормализация ФИО/фрагментов для имён файлов протоколов (пробелы → _)."""
    if s is None:
        return ""
    t = str(s).strip()
    t = re.sub(r"[\s/\\]+", "_", t)
    t = re.sub(r"_+", "_", t)
    return t.strip("_")


class CompetitionCSVManager:
    """Менеджер для работы с CSV файлами соревнования"""
    
    PARTICIPANTS_HEADERS = ['ФИО', 'год рождения', 'разряд', 'кю', 'СШ', 'тренер']
    JUDGES_HEADERS = ['ФИО']
    PAIRS_HEADERS = ['номер пары', 'Тори_ФИО', 'Тори_год рождения', 'Тори_разряд', 'Тори_кю', 'Тори_СШ', 'Тори_тренер', 'Уке_ФИО', 'Уке_год рождения', 'Уке_разряд', 'Уке_кю', 'Уке_СШ', 'Уке_тренер']
    JUDGES_LIST_HEADERS = ['место', 'ФИО']
    FINAL_PROTOCOL_HEADERS = ['номер пары', 'Тори', 'Уке', 'Судья 1', 'Судья 2', 'Судья 3', 'Судья 4', 'Судья 5', 'Сумма', 'Место']

    @staticmethod
    def normalize_stage(stage: str) -> str:
        return 'final' if str(stage).strip().lower() == 'final' else 'prelim'
    
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
        # Для final используем отдельную подпапку; prelim хранится в корне дисциплины
        final_path = os.path.join(disc_path, 'final')
        os.makedirs(final_path, exist_ok=True)
        os.makedirs(os.path.join(final_path, 'protocols'), exist_ok=True)
        CSVManager.ensure_csv_exists(
            os.path.join(final_path, 'participants_list.csv'),
            CompetitionCSVManager.PAIRS_HEADERS
        )
        CSVManager.ensure_csv_exists(
            os.path.join(final_path, 'final_protocol.csv'),
            CompetitionCSVManager.FINAL_PROTOCOL_HEADERS
        )

    @staticmethod
    def get_stage_path(comp_base_path: str, discipline_key: str, stage: str) -> str:
        disc_path = CompetitionCSVManager.get_discipline_path(comp_base_path, discipline_key)
        st = CompetitionCSVManager.normalize_stage(stage)
        return os.path.join(disc_path, 'final') if st == 'final' else disc_path

    @staticmethod
    def get_stage_participants_path(comp_base_path: str, discipline_key: str, stage: str) -> str:
        return os.path.join(
            CompetitionCSVManager.get_stage_path(comp_base_path, discipline_key, stage),
            'participants_list.csv'
        )

    @staticmethod
    def get_stage_final_protocol_path(comp_base_path: str, discipline_key: str, stage: str) -> str:
        return os.path.join(
            CompetitionCSVManager.get_stage_path(comp_base_path, discipline_key, stage),
            'final_protocol.csv'
        )
    
    @staticmethod
    def get_protocol_path(comp_base_path: str, discipline_key: str, judge_name: str, judge_position: int, tori: str, uke: str) -> str:
        """Возвращает путь до файла протокола судьи"""
        disc_path = CompetitionCSVManager.get_discipline_path(comp_base_path, discipline_key)
        protocols_path = os.path.join(disc_path, 'protocols')
        jn = normalize_protocol_token(judge_name)
        tt = normalize_protocol_token(tori)
        uu = normalize_protocol_token(uke)
        return os.path.join(protocols_path, f'{jn}_{judge_position}_{tt}-{uu}.csv')

    @staticmethod
    def resolve_protocol_path(
        comp_base_path: str, discipline_key: str, judge_name: str, judge_position: int, tori: str, uke: str,
    ) -> str:
        """Чтение: нормализованное имя файла или legacy с сырыми ФИО."""
        primary = CompetitionCSVManager.get_protocol_path(
            comp_base_path, discipline_key, judge_name, judge_position, tori, uke
        )
        if os.path.isfile(primary):
            return primary
        disc_path = CompetitionCSVManager.get_discipline_path(comp_base_path, discipline_key)
        protocols_path = os.path.join(disc_path, 'protocols')
        legacy = os.path.join(protocols_path, f'{judge_name}_{judge_position}_{tori}-{uke}.csv')
        if os.path.isfile(legacy):
            return legacy
        return primary

    @staticmethod
    def get_stage_protocol_path(comp_base_path: str, discipline_key: str, stage: str, judge_name: str, judge_position: int, tori: str, uke: str) -> str:
        stage_path = CompetitionCSVManager.get_stage_path(comp_base_path, discipline_key, stage)
        protocols_path = os.path.join(stage_path, 'protocols')
        jn = normalize_protocol_token(judge_name)
        tt = normalize_protocol_token(tori)
        uu = normalize_protocol_token(uke)
        return os.path.join(protocols_path, f'{jn}_{judge_position}_{tt}-{uu}.csv')

    @staticmethod
    def resolve_stage_protocol_path(
        comp_base_path: str, discipline_key: str, stage: str,
        judge_name: str, judge_position: int, tori: str, uke: str,
    ) -> str:
        """Путь к CSV протокола: предпочитает нормализованное имя; если файла нет — legacy с сырыми ФИО."""
        primary = CompetitionCSVManager.get_stage_protocol_path(
            comp_base_path, discipline_key, stage, judge_name, judge_position, tori, uke
        )
        if os.path.isfile(primary):
            return primary
        stage_path = CompetitionCSVManager.get_stage_path(comp_base_path, discipline_key, stage)
        protocols_path = os.path.join(stage_path, 'protocols')
        legacy = os.path.join(protocols_path, f'{judge_name}_{judge_position}_{tori}-{uke}.csv')
        if os.path.isfile(legacy):
            return legacy
        return primary

    @staticmethod
    def parse_protocol_filename(stem: str) -> Optional[Tuple[str, str, str]]:
        """
        Разбор имени без .csv: ..._<1-5>_<tori-uke slug>.
        Возвращает (judge_name, position_str, pair_slug_raw).
        """
        m = re.search(r'_([1-5])_(.+)$', stem)
        if not m:
            return None
        pos = m.group(1)
        slug = m.group(2)
        judge = stem[: m.start()].rstrip("_")
        return (judge, pos, slug)

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
        protocol_path = CompetitionCSVManager.resolve_protocol_path(
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
