#!/usr/bin/env python
"""Запуск веб-интерфейса sqlite-web для редактирования базы данных SQLite.

Используется готовое решение sqlite-web (https://github.com/coleifer/sqlite-web).

Запуск:
    python run_sqlite_web.py [путь_к_базе.db]

Интерфейс доступен по адресу: http://localhost:5001
"""

import os
import secrets
import sqlite3

from sqlite_web import sqlite_web as sw


def _prepare_database(db_path: str) -> None:
    """Создаёт файл базы данных, если он отсутствует."""
    if os.path.exists(db_path):
        return
    print(f"База данных {db_path} не найдена. Создаём...")
    conn = sqlite3.connect(db_path)
    conn.close()
    print(f"База данных создана: {db_path}")


def create_sqlite_web_app(db_path: str):
    """Инициализирует приложение sqlite-web для указанной базы данных."""
    app = sw.app
    app.config["SECRET_KEY"] = os.environ.get(
        "SQLITE_WEB_SECRET_KEY", secrets.token_hex(32)
    )
    app.config["ROWS_PER_PAGE"] = 25
    app.config["QUERY_ROWS_PER_PAGE"] = 100
    # Инициализация датасета через официальный API sqlite-web.
    sw.initialize_app([db_path])
    return app


def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.environ.get("DATABASE_PATH", os.path.join(base_dir, "data.db"))
    port = int(os.environ.get("SQLITE_WEB_PORT", "5001"))

    _prepare_database(db_path)
    app = create_sqlite_web_app(db_path)

    print("=" * 60)
    print("Веб-интерфейс для редактирования SQLite базы данных")
    print("=" * 60)
    print(f"База данных: {db_path}")
    print(f"Адрес: http://localhost:{port}")
    print("=" * 60)
    print("Возможности:")
    print("  - Просмотр всех таблиц")
    print("  - Добавление, редактирование, удаление записей")
    print("  - Выполнение SQL-запросов")
    print("  - Экспорт/Импорт данных")
    print("  - Создание/удаление таблиц и индексов")
    print("=" * 60)
    print("Нажмите Ctrl+C для остановки")
    print("=" * 60)

    # Порт 5001, чтобы не конфликтовать с основным приложением (порт 5000).
    # Локальное приложение без внешнего доступа — Werkzeug допустим.
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
