import logging
from datetime import date, datetime, timedelta
from typing import Optional

from config import settings
from models.company import Company, EmailLog, EmailTemplate

logger = logging.getLogger(__name__)

_db = None
_initialized = False


class FirebaseNotConfiguredError(Exception):
    pass


def _get_db():
    global _db, _initialized
    if _initialized:
        return _db

    _initialized = True

    if not settings.firebase_configured:
        logger.warning("Firebase nie jest skonfigurowany — operacje DB beda pomijane")
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred_dict = settings.firebase_credentials_dict
            if cred_dict is not None:
                cred = credentials.Certificate(cred_dict)
            else:
                cred = credentials.Certificate(settings.firebase_credentials_file)
            firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})

        _db = firestore.client()
        logger.info("Firebase Firestore polaczony pomyslnie")
        return _db
    except Exception as e:
        logger.error(f"Blad inicjalizacji Firebase: {e}")
        return None


def _require_db():
    db = _get_db()
    if db is None:
        raise FirebaseNotConfiguredError(
            "Firebase nie jest skonfigurowany. Uzupelnij .env i dodaj firebase-credentials.json"
        )
    return db


def save_company(company: Company) -> None:
    db = _require_db()
    doc_ref = db.collection("companies").document(company.krs)
    existing = doc_ref.get()
    if existing.exists:
        data = existing.to_dict()
        update_data = company.to_dict()
        # Zachowaj pola które nie powinny być nadpisywane przy ponownym scrapingu
        for field in ("email_sent", "email_sent_at", "hubspot_company_id", "hubspot_contact_id", "hubspot_synced_at"):
            if data.get(field):
                update_data[field] = data[field]
        doc_ref.set(update_data)
    else:
        doc_ref.set(company.to_dict())
    logger.debug(f"Zapisano spolke KRS {company.krs}")


def mark_email_sent(krs: str, sent_at: datetime) -> None:
    db = _require_db()
    db.collection("companies").document(krs).update(
        {"email_sent": True, "email_sent_at": sent_at.isoformat()}
    )


def get_companies(
    limit: int = 50,
    offset: int = 0,
    date_filter: Optional[str] = None,
    email_sent: Optional[bool] = None,
    has_email: Optional[bool] = None,
) -> tuple[list[Company], int]:
    db = _require_db()
    query = db.collection("companies")

    if date_filter:
        query = query.where("registered_at", ">=", date_filter).where(
            "registered_at", "<=", date_filter + "T23:59:59"
        )

    if email_sent is not None:
        query = query.where("email_sent", "==", email_sent)

    query = query.order_by("scraped_at", direction="DESCENDING")

    all_docs = list(query.stream())

    # has_email filtrujemy lokalnie — Firestore nie ma wygodnego
    # "is null / is not null", a mieszanie != z order_by wymagaloby
    # dodatkowego indeksu zlozonego.
    if has_email is not None:
        all_docs = [
            d for d in all_docs
            if bool((d.to_dict() or {}).get("email")) == has_email
        ]

    total = len(all_docs)
    page_docs = all_docs[offset : offset + limit]

    companies = []
    for doc in page_docs:
        try:
            companies.append(Company.from_dict(doc.to_dict()))
        except Exception as e:
            logger.warning(f"Blad parsowania dokumentu {doc.id}: {e}")

    return companies, total


def get_company(krs: str) -> Optional[Company]:
    db = _require_db()
    doc = db.collection("companies").document(krs).get()
    if not doc.exists:
        return None
    return Company.from_dict(doc.to_dict())


def save_template(template: EmailTemplate) -> None:
    db = _require_db()
    db.collection("email_templates").document(template.id).set(template.to_dict())
    logger.debug(f"Zapisano szablon {template.id}")


def get_template(template_id: str) -> Optional[EmailTemplate]:
    db = _require_db()
    doc = db.collection("email_templates").document(template_id).get()
    if not doc.exists:
        return None
    return EmailTemplate.from_dict(doc.to_dict())


def get_all_templates() -> list[EmailTemplate]:
    db = _require_db()
    docs = db.collection("email_templates").stream()
    templates = []
    for doc in docs:
        try:
            templates.append(EmailTemplate.from_dict(doc.to_dict()))
        except Exception as e:
            logger.warning(f"Blad parsowania szablonu {doc.id}: {e}")
    return templates


def delete_template(template_id: str) -> bool:
    db = _require_db()
    ref = db.collection("email_templates").document(template_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


def delete_company(krs: str) -> bool:
    db = _require_db()
    ref = db.collection("companies").document(krs)
    if not ref.get().exists:
        return False
    ref.delete()
    logger.debug(f"Usunieto spolke KRS {krs}")
    return True


def delete_companies(krs_list: list[str]) -> int:
    db = _require_db()
    count = 0
    for krs in krs_list:
        ref = db.collection("companies").document(krs)
        if ref.get().exists:
            ref.delete()
            count += 1
    logger.debug(f"Usunieto {count} spolek")
    return count


def _has_contact_data(data: dict) -> bool:
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    return bool(email or phone)


def count_companies_without_contact() -> int:
    db = _require_db()
    count = 0
    for doc in db.collection("companies").stream():
        if not _has_contact_data(doc.to_dict() or {}):
            count += 1
    return count


def delete_companies_without_contact() -> int:
    """Usuwa firmy bez emaila i bez telefonu."""
    db = _require_db()
    to_delete: list[str] = []
    for doc in db.collection("companies").stream():
        if not _has_contact_data(doc.to_dict() or {}):
            to_delete.append(doc.id)
    return delete_companies(to_delete)


def log_email(log: EmailLog) -> None:
    db = _require_db()
    db.collection("email_logs").add(log.to_dict())
    if log.status == "sent":
        mark_email_sent(log.company_krs, log.sent_at)


def save_hubspot_token(token_data: dict) -> None:
    db = _require_db()
    db.collection("settings").document("hubspot").set(token_data)
    logger.debug("Zapisano token HubSpot")


def get_hubspot_token() -> Optional[dict]:
    db = _require_db()
    doc = db.collection("settings").document("hubspot").get()
    return doc.to_dict() if doc.exists else None


def delete_hubspot_token() -> None:
    db = _require_db()
    db.collection("settings").document("hubspot").delete()
    logger.debug("Usunieto token HubSpot")


def get_stats() -> dict:
    db = _require_db()
    today = date.today().isoformat()

    companies_ref = db.collection("companies")
    total_companies = len(list(companies_ref.stream()))

    sent_ref = companies_ref.where("email_sent", "==", True)
    total_sent = len(list(sent_ref.stream()))

    today_ref = companies_ref.where("registered_at", ">=", today)
    today_companies = len(list(today_ref.stream()))

    logs_ref = db.collection("email_logs")
    today_sent_logs = logs_ref.where("sent_at", ">=", today).where("status", "==", "sent")
    today_sent = len(list(today_sent_logs.stream()))

    chart_data = []
    for i in range(6, -1, -1):
        day = (date.today() - timedelta(days=i)).isoformat()
        day_companies = len(list(companies_ref.where("registered_at", ">=", day)
                                 .where("registered_at", "<=", day + "T23:59:59").stream()))
        day_sent = len(list(logs_ref.where("sent_at", ">=", day)
                            .where("sent_at", "<=", day + "T23:59:59")
                            .where("status", "==", "sent").stream()))
        chart_data.append({"date": day, "companies": day_companies, "sent": day_sent})

    return {
        "total_companies": total_companies,
        "total_sent": total_sent,
        "today_companies": today_companies,
        "today_sent": today_sent,
        "chart_data": chart_data,
    }
