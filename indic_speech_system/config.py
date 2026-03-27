# config.py
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv()
except ImportError:
    pass  # dotenv not installed; rely on environment variables directly


class Config:
    # Telegram Bot (BotFather)
    TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")

    # Evolution API for WhatsApp
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
    EVOLUTION_API_KEY: str | None = os.getenv("EVOLUTION_API_KEY")
    EVOLUTION_INSTANCE_NAME: str = os.getenv("EVOLUTION_INSTANCE_NAME", "indic_speech_bot")
    
    # Redact personal numbers, load from environment instead
    BOT_PHONE_NUMBER: str = os.getenv("BOT_PHONE_NUMBER", "919876543210@s.whatsapp.net")
    OWNER_PHONE_NUMBER: str = os.getenv("OWNER_PHONE_NUMBER", "919876543211@s.whatsapp.net")  # Owner JID

    # Derived: phone number without JID suffix
    OWNER_ID: str = OWNER_PHONE_NUMBER.split("@")[0] if "@" in OWNER_PHONE_NUMBER else OWNER_PHONE_NUMBER

    # Server settings
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "https://your-domain.com")
    WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "3000"))

    # Database settings
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "evolution")
    DB_USER: str = os.getenv("DB_USER", "evolution")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "evolutionpass123")

    # Supported languages
    SUPPORTED_LANGUAGES: list[str] = [
        'hindi', 'tamil', 'telugu', 'bengali', 'marathi',
        'gujarati', 'kannada', 'malayalam', 'punjabi', 'english'
    ]

    # File paths
    OUTPUT_DIR: str = "outputs"
    TEMP_DIR: str = "temp"

    # Model settings
    DEFAULT_SRC_LANG: str = "hindi"
    DEFAULT_TGT_LANG: str = "english"

    # AI Model Configuration
    AI_MODEL: str = os.getenv("AI_MODEL", "llama3.1:8b")
    AI_MODEL_FAST: str = os.getenv("AI_MODEL_FAST", "llama3.1:8b-instruct-q4_K_M")

    # Circuit Breaker Settings
    CIRCUIT_BREAKER_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))
    CIRCUIT_BREAKER_RESET_SEC: int = int(os.getenv("CIRCUIT_BREAKER_RESET_SEC", "60"))

    # Cloud LLM API Keys (optional, for Business Analyst fallback chain)
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
