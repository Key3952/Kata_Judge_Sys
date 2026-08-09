# utils.py
"""
Общие утилиты для приложения судейства дзюдо ката.
Включает функции логирования, безопасности, валидации и кеширования.
"""

import os
import re
import csv
import json
import logging
import threading
from datetime import datetime
from functools import wraps
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path

from flask import request, jsonify, session, redirect, url_for, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from cachelib import SimpleCache

# ==================== ЛОГИРОВАНИЕ ====================

def setup_logging(app=None, log_level: str = 'INFO', log_file: str = 'app.log'):
    """
    Настраивает систему логирования для приложения.
    
    Args:
        app: Flask приложение (опционально)
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Путь к файлу логов
    
    Returns:
        Logger объект
    """
    # Создаем logger
    logger = logging.getLogger('judo_kata')
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Очищаем существующие handlers
    if logger.handlers:
        logger.handlers.clear()
    
    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (если указан файл)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Не удалось создать файл логов {log_file}: {e}")
    
    # Если передано приложение, привязываем logger
    if app:
        app.logger = logger
    
    return logger


# Глобальный logger
logger = setup_logging()


# ==================== БЕЗОПАСНОСТЬ ====================

class SecurityUtils:
    """Утилиты безопасности"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Хеширует пароль"""
        return generate_password_hash(password, method='pbkdf2:sha256')
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Проверяет пароль против хеша"""
        return check_password_hash(password_hash, password)
    
    @staticmethod
    def sanitize_path(path: str, base_dir: str) -> Optional[str]:
        """
        Проверяет и нормализует путь, предотвращая directory traversal атаки.
        
        Args:
            path: Исходный путь
            base_dir: Базовая директория
        
        Returns:
            Нормализованный путь или None если путь невалиден
        """
        if not path or not base_dir:
            return None
        
        # Нормализуем пути
        abs_base = os.path.abspath(base_dir)
        abs_path = os.path.abspath(os.path.join(base_dir, path))
        
        # Проверяем что путь внутри базовой директории
        if not abs_path.startswith(abs_base + os.sep) and abs_path != abs_base:
            return None
        
        return abs_path
    
    @staticmethod
    def validate_filename(filename: str, allowed_extensions: List[str] = None) -> bool:
        """
        Проверяет безопасность имени файла.
        
        Args:
            filename: Имя файла
            allowed_extensions: Список разрешенных расширений
        
        Returns:
            True если имя безопасно
        """
        if not filename:
            return False
        
        # Запрещенные символы
        forbidden_chars = ['..', '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\x00']
        for char in forbidden_chars:
            if char in filename:
                return False
        
        # Проверка расширения
        if allowed_extensions:
            ext = os.path.splitext(filename)[1].lower().lstrip('.')
            if ext not in [e.lower().lstrip('.') for e in allowed_extensions]:
                return False
        
        return True
    
    @staticmethod
    def rate_limit_key() -> str:
        """Генерирует ключ для rate limiting на основе IP"""
        if request:
            return request.remote_addr or 'unknown'
        return 'unknown'


# ==================== ВАЛИДАЦИЯ ====================

class ValidationError(Exception):
    """Исключение валидации"""
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(self.message)


class Validator:
    """Класс для валидации входных данных"""
    
    # Паттерны для валидации
    PATTERNS = {
        'name': re.compile(r'^[\w\s\-\']+$', re.UNICODE),
        'email': re.compile(r'^[\w\.-]+@[\w\.-]+\.\w+$'),
        'numeric': re.compile(r'^\d+$'),
        'alphanumeric': re.compile(r'^[\w\-]+$'),
        'cyrillic': re.compile(r'^[\u0400-\u04FF\s\-\']+$', re.UNICODE),
    }
    
    @staticmethod
    def is_not_empty(value: Any, field_name: str = 'Поле') -> None:
        """Проверяет что значение не пустое"""
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValidationError(f"{field_name} не может быть пустым", field_name)
    
    @staticmethod
    def min_length(value: str, min_len: int, field_name: str = 'Поле') -> None:
        """Проверяет минимальную длину строки"""
        if not isinstance(value, str) or len(value) < min_len:
            raise ValidationError(f"{field_name} должно содержать минимум {min_len} символов", field_name)
    
    @staticmethod
    def max_length(value: str, max_len: int, field_name: str = 'Поле') -> None:
        """Проверяет максимальную длину строки"""
        if isinstance(value, str) and len(value) > max_len:
            raise ValidationError(f"{field_name} не может превышать {max_len} символов", field_name)
    
    @staticmethod
    def is_numeric(value: Any, field_name: str = 'Поле', allow_float: bool = False) -> None:
        """Проверяет что значение числовое"""
        try:
            if allow_float:
                float(value)
            else:
                int(value)
        except (ValueError, TypeError):
            raise ValidationError(f"{field_name} должно быть числом", field_name)
    
    @staticmethod
    def in_range(value: Any, min_val: Any, max_val: Any, field_name: str = 'Поле') -> None:
        """Проверяет что значение в диапазоне"""
        try:
            val = float(value) if isinstance(value, (int, float, str)) else value
            if val < min_val or val > max_val:
                raise ValidationError(f"{field_name} должно быть от {min_val} до {max_val}", field_name)
        except (ValueError, TypeError):
            raise ValidationError(f"{field_name} должно быть числом в диапазоне", field_name)
    
    @staticmethod
    def matches_pattern(value: str, pattern_name: str, field_name: str = 'Поле') -> None:
        """Проверяет соответствие паттерну"""
        if pattern_name not in Validator.PATTERNS:
            raise ValidationError(f"Неизвестный паттерн: {pattern_name}")
        
        if not Validator.PATTERNS[pattern_name].match(str(value)):
            raise ValidationError(f"{field_name} имеет неверный формат", field_name)
    
    @staticmethod
    def is_valid_judge_position(value: Any) -> None:
        """Проверяет позицию судьи (1-5)"""
        Validator.is_numeric(value, 'Позиция судьи')
        Validator.in_range(value, 1, 5, 'Позиция судьи')
    
    @staticmethod
    def is_valid_score(value: Any) -> None:
        """Проверяет оценку (0-170)"""
        Validator.is_numeric(value, 'Оценка', allow_float=True)
        Validator.in_range(value, 0, 170, 'Оценка')
    
    @staticmethod
    def clean_string(value: str, max_len: int = 255) -> str:
        """Очищает строку от опасных символов"""
        if not isinstance(value, str):
            return ''
        
        # Удаляем null bytes
        value = value.replace('\x00', '')
        # Обрезаем пробелы
        value = value.strip()
        # Ограничиваем длину
        return value[:max_len]


# ==================== КЕШИРОВАНИЕ ====================

class CacheManager:
    """Менеджер кеширования"""
    
    _cache = SimpleCache(default_timeout=300)
    _locks = {}
    
    @classmethod
    def get(cls, key: str) -> Any:
        """Получает значение из кеша"""
        return cls._cache.get(key)
    
    @classmethod
    def set(cls, key: str, value: Any, timeout: int = 300) -> bool:
        """Сохраняет значение в кеш"""
        return cls._cache.set(key, value, timeout=timeout)
    
    @classmethod
    def delete(cls, key: str) -> bool:
        """Удаляет значение из кеша"""
        return cls._cache.delete(key)
    
    @classmethod
    def clear(cls) -> None:
        """Очищает весь кеш"""
        cls._cache.clear()
    
    @classmethod
    def get_or_set(cls, key: str, factory: Callable, timeout: int = 300) -> Any:
        """
        Получает значение из кеша или создает новое.
        
        Args:
            key: Ключ кеша
            factory: Функция для создания значения
            timeout: Время жизни кеша в секундах
        
        Returns:
            Значение из кеша
        """
        value = cls.get(key)
        if value is not None:
            return value
        
        # Блокировка для предотвращения race condition
        lock_key = f"lock:{key}"
        if lock_key not in cls._locks:
            cls._locks[lock_key] = threading.Lock()
        
        with cls._locks[lock_key]:
            # Повторная проверка после получения блокировки
            value = cls.get(key)
            if value is not None:
                return value
            
            value = factory()
            cls.set(key, value, timeout)
            return value
    
    @classmethod
    def invalidate_pattern(cls, pattern: str) -> int:
        """
        Инвалидирует ключи по паттерну.
        Примечание: SimpleCache не поддерживает поиск по паттерну,
        поэтому этот метод требует реализации через Redis/Memcached
        """
        logger.warning("invalidate_pattern не поддерживается для SimpleCache")
        return 0


# Декоратор для кеширования результатов функции
def cached(timeout: int = 300, key_prefix: str = 'view'):
    """
    Декоратор для кеширования результатов функции.
    
    Args:
        timeout: Время жизни кеша в секундах
        key_prefix: Префикс для ключа кеша
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Генерируем ключ на основе имени функции и аргументов
            key_parts = [key_prefix, f.__name__]
            
            # Добавляем аргументы в ключ
            for arg in args:
                key_parts.append(str(arg))
            for k, v in sorted(kwargs.items()):
                key_parts.append(f"{k}={v}")
            
            cache_key = ':'.join(key_parts)
            
            # Пробуем получить из кеша
            cached_value = CacheManager.get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache hit: {cache_key}")
                return cached_value
            
            # Вызываем функцию
            result = f(*args, **kwargs)
            
            # Сохраняем в кеш
            CacheManager.set(cache_key, result, timeout=timeout)
            logger.debug(f"Cache miss, stored: {cache_key}")
            
            return result
        
        return decorated_function
    return decorator


# ==================== ОБРАБОТКА ОШИБОК ====================

class APIError(Exception):
    """Базовое исключение для API ошибок"""
    def __init__(self, message: str, status_code: int = 400, field: str = None):
        self.message = message
        self.status_code = status_code
        self.field = field
        super().__init__(self.message)
    
    def to_dict(self) -> Dict:
        result = {'error': self.message}
        if self.field:
            result['field'] = self.field
        return result


class NotFoundError(APIError):
    """Ошибка 404"""
    def __init__(self, message: str = 'Ресурс не найден'):
        super().__init__(message, status_code=404)


class UnauthorizedError(APIError):
    """Ошибка 401"""
    def __init__(self, message: str = 'Требуется авторизация'):
        super().__init__(message, status_code=401)


class ForbiddenError(APIError):
    """Ошибка 403"""
    def __init__(self, message: str = 'Доступ запрещен'):
        super().__init__(message, status_code=403)


class ValidationError(APIError):
    """Ошибка валидации 400"""
    def __init__(self, message: str, field: str = None):
        super().__init__(message, status_code=400, field=field)


def handle_api_error(error: APIError):
    """Обработчик API ошибок"""
    logger.warning(f"API Error: {error.message} (status={error.status_code})")
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


def api_response(data: Any = None, message: str = None, status_code: int = 200):
    """
    Создает стандартный ответ API.
    
    Args:
        data: Данные ответа
        message: Сообщение
        status_code: HTTP статус код
    
    Returns:
        Flask Response объект
    """
    response_data = {}
    
    if message:
        response_data['message'] = message
    
    if data is not None:
        response_data['data'] = data
    
    response = jsonify(response_data)
    response.status_code = status_code
    return response


def api_success(data: Any = None, message: str = 'OK'):
    """Создает успешный ответ API"""
    return api_response(data=data, message=message, status_code=200)


def api_error(message: str, status_code: int = 400, field: str = None):
    """Создает ответ с ошибкой API"""
    error = APIError(message, status_code, field)
    return handle_api_error(error)


# ==================== ДЕКОРАТОРЫ БЕЗОПАСНОСТИ ====================

def require_admin(f: Callable) -> Callable:
    """
    Декоратор для защиты routes требующих админских прав.
    Перенаправляет на страницу login если пользователь не авторизован.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session or not session.get('admin'):
            logger.warning(f"Попытка доступа без авторизации: {request.path}")
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


def require_admin_json(f: Callable) -> Callable:
    """
    Декоратор для защиты API endpoints требующих админских прав.
    Возвращает JSON ошибку если пользователь не авторизован.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session or not session.get('admin'):
            logger.warning(f"Попытка доступа API без авторизации: {request.path}")
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function


def validate_json_request(required_fields: List[str] = None):
    """
    Декоратор для валидации JSON запросов.
    
    Args:
        required_fields: Список обязательных полей
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Проверяем Content-Type
            if not request.is_json:
                return jsonify({'error': 'Content-Type должен быть application/json'}), 400
            
            data = request.get_json(silent=True)
            if data is None:
                return jsonify({'error': 'Неверный JSON формат'}), 400
            
            # Проверяем обязательные поля
            if required_fields:
                missing = [field for field in required_fields if field not in data]
                if missing:
                    return jsonify({
                        'error': 'Отсутствуют обязательные поля',
                        'missing_fields': missing
                    }), 400
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def normalize_protocol_token(s: str) -> str:
    """
    Единая нормализация ФИО/фрагментов для имён файлов протоколов.
    Пробелы заменяются на подчеркивания.
    """
    if s is None:
        return ""
    t = str(s).strip()
    t = re.sub(r"[\s/\\]+", "_", t)
    t = re.sub(r"_+", "_", t)
    return t.strip("_")


def safe_int(value: Any, default: int = 0) -> int:
    """Безопасное преобразование в int"""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """Безопасное преобразование в float"""
    try:
        return float(str(value).replace(',', '.'))
    except (ValueError, TypeError, AttributeError):
        return default


def sanitize_csv_field(value: Any) -> str:
    """Очищает значение для записи в CSV"""
    if value is None:
        return ''
    s = str(value).strip()
    # Удаляем потенциально опасные символы
    s = s.replace('\x00', '').replace('\r', '').replace('\n', ' ')
    return s[:500]  # Ограничение длины


def format_date_ru(dt: datetime) -> str:
    """Форматирует дату на русском языке"""
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    return f"{dt.day} {months.get(dt.month, '')} {dt.year} г."


# ==================== МЕНЕДЖЕР БЛОКИРОВОК ДЛЯ CSV ====================

class CSVLockManager:
    """
    Менеджер блокировок для безопасной записи в CSV файлы.
    Предотвращает race conditions при одновременной записи.
    """
    
    _locks: Dict[str, threading.Lock] = {}
    _global_lock = threading.Lock()
    
    @classmethod
    def get_lock(cls, filepath: str) -> threading.Lock:
        """Получает блокировку для файла"""
        abs_path = os.path.abspath(filepath)
        
        with cls._global_lock:
            if abs_path not in cls._locks:
                cls._locks[abs_path] = threading.Lock()
            return cls._locks[abs_path]
    
    @classmethod
    def write_with_lock(cls, filepath: str, write_func: Callable) -> Any:
        """
        Выполняет запись в файл с блокировкой.
        
        Args:
            filepath: Путь к файлу
            write_func: Функция записи (принимает filepath как аргумент)
        
        Returns:
            Результат выполнения write_func
        """
        lock = cls.get_lock(filepath)
        with lock:
            return write_func(filepath)
