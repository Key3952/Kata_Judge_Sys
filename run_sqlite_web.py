#!/usr/bin/env python
"""
Запуск веб-интерфейса sqlite-web для редактирования базы данных SQLite.
Используется готовое решение sqlite-web (https://github.com/coleifer/sqlite-web).

Запуск:
    python run_sqlite_web.py

Интерфейс будет доступен по адресу: http://localhost:5001
"""

import os
import sys
from sqlite_web import sqlite_web

# Путь к базе данных проекта
GLOBAL_DATA_DIR = os.path.dirname(__file__)
DATABASE_PATH = os.path.join(GLOBAL_DATA_DIR, 'data.db')

# Создаем базу данных если она не существует
if not os.path.exists(DATABASE_PATH):
    print(f"База данных {DATABASE_PATH} не найдена. Создаем...")
    # Просто создаем пустой файл БД, sqlite-web сам разберется со структурой
    import sqlite3
    conn = sqlite3.connect(DATABASE_PATH)
    conn.close()
    print(f"База данных создана: {DATABASE_PATH}")

# Настраиваем sqlite_web
sqlite_web.DATABASE = DATABASE_PATH
sqlite_web.ROWS_PER_PAGE = 25
sqlite_web.QUERY_ROWS_PER_PAGE = 100
sqlite_web.TRUNCATE_VALUES = True

# Отключаем пароль для локального использования
sqlite_web.app.config['SECRET_KEY'] = 'sqlite-local-key-change-in-production'

# Загружаем базу данных в datasets
from playhouse.dataset import DataSet
datasets['sqlite'] = DataSet(f'sqlite:///{DATABASE_PATH}')
dataset_config['sqlite'] = {'database': DATABASE_PATH}

if __name__ == '__main__':
    print("=" * 60)
    print("Веб-интерфейс для редактирования SQLite базы данных")
    print("=" * 60)
    print(f"База данных: {DATABASE_PATH}")
    print(f"Адрес: http://localhost:5001")
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
    
    # Запускаем на порту 5001 чтобы не конфликтовать с основным приложением (порт 5000)
    sqlite_web.app.run(host='0.0.0.0', port=5001, debug=False)
