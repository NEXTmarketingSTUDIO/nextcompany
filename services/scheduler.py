import logging
from datetime import date
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from scraper.ceidg_client import CEIDGClient
from scraper.email_finder import find_contact
from scraper.krs_client import KRSClient
from services.firebase_service import FirebaseNotConfiguredError, save_company
from utils.geocoding import is_within_radius
from utils.pkd import matches_branza

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _enrich_contact(company) -> None:
    """Uzupełnij email i telefon ze strony www (tylko gdy ich brak)."""
    if not company.website:
        return
    if company.email and company.phone:
        return
    try:
        contact = await find_contact(company.website)
        if not company.email:
            company.email = contact.get("email")
        if not company.phone:
            company.phone = contact.get("phone")
    except Exception as e:
        logger.debug("find_contact failed for %s: %s", company.website, e)


def _to_row(c) -> dict:
    return {
        "krs": c.krs,
        "nazwa": c.name,
        "forma": c.legal_form,
        "zrodlo": c.source,
        "adres": c.address,
        "miasto": c.city,
        "wojewodztwo": c.voivodeship,
        "nip": c.nip,
        "email": c.email,
        "telefon": c.phone,
        "www": c.website,
        "data_rejestracji": c.registered_at,
        "pkd_sekcja": c.pkd_section,
        "pkd_dzialalnosc": c.pkd_main_activity,
    }


async def run_daily_scrape(
    target_date: date | None = None,
    branza: str | None = None,
    city: str | None = None,
    radius_km: float | None = None,
    limit: int | None = None,
) -> dict:
    if target_date is None:
        target_date = date.today()

    limit_label = (
        f"max {limit} na źródło (KRS + CEIDG osobno)"
        if limit is not None
        else "bez limitu — pełne pobranie dnia"
    )
    logger.info(
        f"=== Scraping {target_date} | branża={branza or 'wszystkie'} | "
        f"miasto={city or 'wszędzie'} | promień={radius_km or '—'} km | {limit_label} ==="
    )

    # ── KRS (spółki) ────────────────────────────────────────────────────────
    async with KRSClient() as krs:
        krs_companies = await krs.search_by_date(target_date, limit=limit)
    logger.info(f"KRS: pobrano {len(krs_companies)} spółek")

    # ── CEIDG (JDG) ─────────────────────────────────────────────────────────
    ceidg_companies = []
    if settings.ceidg_configured:
        async with CEIDGClient(settings.ceidg_api_key) as ceidg:
            ceidg_companies = await ceidg.search_by_date(target_date, limit=limit)
        logger.info(f"CEIDG: pobrano {len(ceidg_companies)} firm")
    else:
        logger.info("CEIDG: pominięto (brak CEIDG_API_KEY w .env)")

    all_companies = krs_companies + ceidg_companies
    logger.info(f"Łącznie: {len(all_companies)} podmiotów przed filtrowaniem")

    # ── Filtrowanie i wzbogacanie ────────────────────────────────────────────
    filtered = []
    for company in all_companies:
        # Filtr branży
        if not matches_branza(company.pkd_section, branza):
            continue

        # Filtr lokalizacji
        if city and radius_km:
            in_range = await is_within_radius(company.city or "", city, radius_km)
            if not in_range:
                continue

        # Uzupełnij kontakt ze strony www (CEIDG ma email/tel wprost, KRS nie)
        await _enrich_contact(company)

        filtered.append(company)
        logger.info(f"[PODMIOT] {_to_row(company)}")

    # ── Zapis do Firebase ────────────────────────────────────────────────────
    saved_count = 0
    if settings.firebase_configured:
        for company in filtered:
            try:
                save_company(company)
                saved_count += 1
            except FirebaseNotConfiguredError:
                logger.warning("Firebase niedostępny — przerywam zapis do bazy")
                break
            except Exception as e:
                logger.warning("Nie udało się zapisać %s: %s", company.krs, e)
    else:
        logger.info("Pomijam zapis do Firestore (Firebase nieskonfigurowany)")

    logger.info(
        f"=== Zakończono: wszystkich={len(all_companies)} (KRS={len(krs_companies)}, "
        f"CEIDG={len(ceidg_companies)}), po filtrach={len(filtered)}, zapisano={saved_count} ==="
    )

    return {
        "stats": {
            "scraped": len(all_companies),
            "krs": len(krs_companies),
            "ceidg": len(ceidg_companies),
            "filtered": len(filtered),
            "saved": saved_count,
        },
        "companies": [_to_row(c) for c in filtered],
    }


def setup_scheduler():
    scheduler.add_job(
        run_daily_scrape,
        trigger=CronTrigger(hour=settings.scrape_hour, minute=settings.scrape_minute),
        id="daily_scrape",
        name="Codzienny scraping KRS + CEIDG",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler uruchomiony — scraping codziennie o "
        f"{settings.scrape_hour:02d}:{settings.scrape_minute:02d}"
    )


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler zatrzymany")
