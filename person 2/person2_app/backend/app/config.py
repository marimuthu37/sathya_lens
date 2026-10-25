"""
app/config.py
─────────────
Central configuration module.

All values are read from environment variables (or .env file at startup).
Import `settings` from this module anywhere you need a config value.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_env: str = "development"
    debug: bool = True

    # ── Server ────────────────────────────────────────────────────────────────
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a Python list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./satyalens.db"
    # SQL statement logging. Separate from `debug` and off by default: echoed
    # statements would otherwise put case descriptions and extracted payment
    # values into the server log. Bound parameters are never logged.
    sql_echo: bool = False

    # ── File storage ──────────────────────────────────────────────────────────
    upload_dir: str = "uploads"

    # Maximum upload size in bytes (default 10 MiB).
    max_upload_bytes: int = 10 * 1024 * 1024

    # Comma-separated list of allowed MIME types for evidence uploads.
    allowed_mime_types: str = (
        "image/jpeg,image/png,image/gif,image/webp,"
        "application/pdf,"
        "audio/mpeg,audio/wav,audio/ogg,audio/mp4,audio/webm,"
        "video/mp4,video/webm,"
        "text/plain"
    )

    @property
    def allowed_mime_types_list(self) -> list[str]:
        return [m.strip() for m in self.allowed_mime_types.split(",") if m.strip()]

    # ── Analysis module ────────────────────────────────────────────────────────
    # Set to true only when Person 1's analysis_person1.py adapter is available.
    analysis_module_enabled: bool = False


# Singleton — import this object throughout the application.
settings = Settings()
