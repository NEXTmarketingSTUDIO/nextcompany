import base64
import json
import logging
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Katalog z config.py — zawsze folder backendu (niezależnie od cwd przy starcie uvicorn).
BACKEND_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"

    firebase_credentials_path: str = ""
    # Na Renderze: wklej cały JSON konta serwisowego (jedna linia) lub użyj _B64.
    firebase_credentials_json: str = ""
    firebase_credentials_json_b64: str = ""
    firebase_project_id: str = ""

    ceidg_api_key: str = ""

    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    hubspot_redirect_uri: str = "http://localhost:8000/auth/hubspot/callback"
    frontend_url: str = "http://localhost:5173"
    # Dodatkowe originy CORS (np. https://twoj-projekt.web.app), po przecinku.
    cors_origins: str = ""

    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""
    sendgrid_from_name: str = "NEXTcompany"

    scrape_hour: int = 8
    scrape_minute: int = 0

    port: int = 8000

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def firebase_credentials_file(self) -> Path:
        """Ścieżka do JSON konta serwisowego — względna wobec folderu backendu, jeśli nie jest absolutna."""
        if not self.firebase_credentials_path:
            return Path("")
        p = Path(self.firebase_credentials_path)
        if p.is_absolute():
            return p.resolve()
        resolved = (BACKEND_ROOT / p).resolve()
        if resolved.is_file():
            return resolved
        # Częsty błąd: ścieżka z roota mono-repo (backend/plik.json), a plik leży obok config.py.
        if p.parts and p.parts[0] == "backend" and len(p.parts) > 1:
            alt = (BACKEND_ROOT / Path(*p.parts[1:])).resolve()
            if alt.is_file():
                return alt
        return resolved

    @property
    def firebase_credentials_dict(self) -> dict[str, Any] | None:
        """Dane konta serwisowego z env (Render) lub None — wtedy używany jest plik."""
        raw = self.firebase_credentials_json.strip()
        if not raw and self.firebase_credentials_json_b64.strip():
            try:
                raw = base64.b64decode(self.firebase_credentials_json_b64.strip()).decode("utf-8")
            except Exception as e:
                logger.error("Nieprawidlowy FIREBASE_CREDENTIALS_JSON_B64: %s", e)
                return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError as e:
            logger.error("Nieprawidlowy FIREBASE_CREDENTIALS_JSON: %s", e)
            return None

    @property
    def firebase_configured(self) -> bool:
        if not self.firebase_project_id:
            return False
        if self.firebase_credentials_dict is not None:
            return True
        if not self.firebase_credentials_path:
            return False
        return self.firebase_credentials_file.is_file()

    @property
    def cors_origins_list(self) -> list[str]:
        origins: list[str] = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
        frontend = self.frontend_url.strip().rstrip("/")
        # Poprawka literówki w .env (hhttps://...)
        if frontend.startswith("hhttps://"):
            frontend = frontend[1:]
        if frontend:
            origins.append(frontend)
            # Firebase Hosting: web.app ↔ firebaseapp.com
            if frontend.endswith(".web.app"):
                origins.append(frontend.replace(".web.app", ".firebaseapp.com"))
            elif frontend.endswith(".firebaseapp.com"):
                origins.append(frontend.replace(".firebaseapp.com", ".web.app"))
        if self.cors_origins:
            origins.extend(o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip())
        return list(dict.fromkeys(origins))

    @property
    def ceidg_configured(self) -> bool:
        return bool(self.ceidg_api_key)

    @property
    def hubspot_configured(self) -> bool:
        return bool(self.hubspot_client_id and self.hubspot_client_secret)

    @property
    def sendgrid_configured(self) -> bool:
        return bool(self.sendgrid_api_key and self.sendgrid_from_email)


settings = Settings()

if not settings.firebase_configured:
    logger.warning(
        "Firebase nie jest skonfigurowany. Skopiuj .env.example do .env i uzupelnij dane Firebase."
    )

if not settings.sendgrid_configured:
    logger.warning(
        "SendGrid nie jest skonfigurowany. Skopiuj .env.example do .env i uzupelnij SENDGRID_API_KEY."
    )
