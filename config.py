# config.py
"""
Модуль конфигурации приложения.
Использует pydantic-settings для валидации и загрузки из .env
"""

import os
from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Настройки приложения с валидацией"""
    
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'
    )
    
    # ==================== БЕЗОПАСНОСТЬ ====================
    SECRET_KEY: str = Field(
        default='dev-secret-key-change-in-production',
        description='Секретный ключ Flask',
        min_length=16
    )
    ADMIN_PASSWORD: str = Field(
        default='admin123',
        description='Пароль администратора'
    )
    SESSION_COOKIE_SECURE: bool = Field(
        default=False,
        description='Secure флаг для cookie'
    )
    SESSION_COOKIE_HTTPONLY: bool = Field(
        default=True,
        description='HttpOnly флаг для cookie'
    )
    SESSION_COOKIE_SAMESITE: str = Field(
        default='Lax',
        description='SameSite атрибут cookie'
    )
    
    # ==================== ПРИЛОЖЕНИЕ ====================
    FLASK_ENV: str = Field(default='development')
    FLASK_DEBUG: bool = Field(default=True)
    MAX_CONTENT_LENGTH: int = Field(
        default=16777216,  # 16MB
        description='Максимальный размер загружаемого контента в байтах'
    )
    
    # ==================== ЛОГИРОВАНИЕ ====================
    LOG_LEVEL: str = Field(default='INFO')
    LOG_FILE: str = Field(default='app.log')
    LOG_MAX_BYTES: int = Field(default=10485760)  # 10MB
    LOG_BACKUP_COUNT: int = Field(default=5)
    
    # ==================== SOCKET.IO ====================
    SOCKETIO_CORS_ORIGINS: str = Field(default='*')
    SOCKETIO_ASYNC_MODE: str = Field(default='threading')
    SOCKETIO_PING_TIMEOUT: int = Field(default=60)
    SOCKETIO_PING_INTERVAL: int = Field(default=25)
    
    # ==================== КЕШИРОВАНИЕ ====================
    CACHE_TYPE: str = Field(default='simple')
    CACHE_DEFAULT_TIMEOUT: int = Field(default=300)
    
    # ==================== RATE LIMITING ====================
    RATELIMIT_ENABLED: bool = Field(default=False)
    RATELIMIT_DEFAULT: str = Field(default='100 per hour')
    RATELIMIT_STORAGE_URL: str = Field(default='memory://')
    
    # ==================== ПУТИ ====================
    DATA_DIR: str = Field(default='')
    COMPETITIONS_DIR: str = Field(default='')
    
    @field_validator('SECRET_KEY')
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError('SECRET_KEY должен быть минимум 16 символов')
        return v
    
    @field_validator('SESSION_COOKIE_SAMESITE')
    @classmethod
    def validate_samesite(cls, v: str) -> str:
        allowed = ['Strict', 'Lax', 'None']
        if v not in allowed:
            raise ValueError(f'SAME_SITE должен быть одним из: {allowed}')
        return v
    
    @field_validator('LOG_LEVEL')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in allowed:
            raise ValueError(f'LOG_LEVEL должен быть одним из: {allowed}')
        return v.upper()
    
    @property
    def is_production(self) -> bool:
        """Проверяет, запущено ли приложение в production режиме"""
        return self.FLASK_ENV == 'production'
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Возвращает список CORS origin"""
        if self.SOCKETIO_CORS_ORIGINS == '*':
            return ['*']
        return [origin.strip() for origin in self.SOCKETIO_CORS_ORIGINS.split(',')]


# Глобальный экземпляр настроек
settings = Settings()


def get_settings() -> Settings:
    """Возвращает экземпляр настроек (для совместимости)"""
    return settings
