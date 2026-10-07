# extensions.py
"""
Модуль расширений Flask.
Централизованная инициализация расширений для приложения.
"""

from flask import Flask
from flask_socketio import SocketIO
from cachelib import SimpleCache
from filelock import FileLock
import threading


# Инициализация расширений
socketio = SocketIO()
cache = SimpleCache()

# Глобальная блокировка для операций с CSV
csv_lock_manager = {}
csv_lock_manager_lock = threading.Lock()


def get_csv_lock(filepath: str) -> FileLock:
    """
    Возвращает блокировку для файла CSV.
    Используется для предотвращения race conditions при записи.
    
    Args:
        filepath: Путь к CSV файлу
    
    Returns:
        FileLock объект для этого файла
    """
    lock_key = f"csv:{filepath}"
    
    with csv_lock_manager_lock:
        if lock_key not in csv_lock_manager:
            lock_file = f"{filepath}.lock"
            csv_lock_manager[lock_key] = FileLock(lock_file, timeout=30)
        
        return csv_lock_manager[lock_key]


def init_extensions(app: Flask) -> None:
    """
    Инициализирует расширения Flask приложения.
    
    Args:
        app: Flask приложение
    """
    from config import settings
    
    # Инициализация SocketIO
    socketio.init_app(
        app,
        cors_allowed_origins=settings.cors_origins_list,
        manage_session=False,
        async_mode=settings.SOCKETIO_ASYNC_MODE,
        logger=False,
        engineio_logger=False,
        ping_timeout=settings.SOCKETIO_PING_TIMEOUT,
        ping_interval=settings.SOCKETIO_PING_INTERVAL
    )
    
    app.logger.info("Расширения инициализированы")


def cleanup_extensions() -> None:
    """Очищает ресурсы расширений"""
    with csv_lock_manager_lock:
        csv_lock_manager.clear()
