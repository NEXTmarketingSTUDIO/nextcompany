"""
Klient CEIDG Hurtowni Danych v3.
Dokumentacja: https://dane.biznes.gov.pl/api/ceidg/v3/firmy
Auth: Bearer token — klucz API z https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00

Uwaga: endpoint /firmy (lista) nie zwraca email/telefon — trzeba pobrać /firma/{id}.
"""
import asyncio
import logging
import time
from datetime import date
from typing import Optional

import httpx

from models.company import Company

logger = logging.getLogger(__name__)

CEIDG_BASE = "https://dane.biznes.gov.pl/api/ceidg/v3"
PAGE_LIMIT = 25  # API v3: max 25 na stronę
# Limit API: ~50 żądań / 3 min — zostawiamy zapas
DETAIL_RATE_LIMIT = 45
DETAIL_RATE_PERIOD = 180.0


class _RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self._max_calls = max_calls
        self._period = period
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < self._period]
            if len(self._timestamps) >= self._max_calls:
                wait = self._period - (now - self._timestamps[0])
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self._period]
            self._timestamps.append(time.monotonic())


class CEIDGClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._detail_limiter = _RateLimiter(DETAIL_RATE_LIMIT, DETAIL_RATE_PERIOD)

    async def __aenter__(self) -> "CEIDGClient":
        self._client = httpx.AsyncClient(
            base_url=CEIDG_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=60.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def search_by_date(self, target_date: date, limit: int | None = None) -> list[Company]:
        """Zwraca JDG z datą rozpoczęcia działalności w danym dniu (opcjonalny limit)."""
        date_str = target_date.isoformat()
        companies: list[Company] = []
        page = 0
        total_in_registry: Optional[int] = None

        if limit is not None:
            logger.info("CEIDG: limit %d — pobieram tylko tyle firm (bez pełnej paginacji)", limit)

        while True:
            remaining = None if limit is None else limit - len(companies)
            if limit is not None and remaining <= 0:
                break

            page_size = PAGE_LIMIT if remaining is None else min(PAGE_LIMIT, remaining)
            params: dict = {
                "dataod": date_str,
                "datado": date_str,
                "limit": page_size,
                "page": page,
            }

            try:
                resp = await self._client.get("/firmy", params=params)
            except httpx.HTTPError as e:
                logger.error("CEIDG request error: %s", e)
                break

            if resp.status_code == 401:
                logger.error("CEIDG: Nieprawidłowy klucz API (401). Sprawdź CEIDG_API_KEY w .env.")
                break
            if resp.status_code == 429:
                logger.warning("CEIDG: Przekroczono limit zapytań (429).")
                break
            if resp.status_code != 200:
                logger.error("CEIDG: HTTP %s — %s", resp.status_code, resp.text[:200])
                break

            data = resp.json()
            if total_in_registry is None:
                raw_count = data.get("count")
                if isinstance(raw_count, int):
                    total_in_registry = raw_count
                    if limit is not None:
                        logger.info(
                            "CEIDG: w rejestrze %d firm na %s, plan pobrania: %d",
                            total_in_registry, date_str, min(limit, total_in_registry),
                        )
                    else:
                        logger.info(
                            "CEIDG: w rejestrze %d firm na %s, pobieram wszystkie",
                            total_in_registry, date_str,
                        )

            items = data.get("firmy") or data.get("firma") or []
            for item in items:
                if limit is not None and len(companies) >= limit:
                    break
                enriched = await self._fetch_detail(item)
                company = self._parse(enriched)
                if company:
                    companies.append(company)

            if limit is not None and len(companies) >= limit:
                break

            links = data.get("links") or {}
            next_url = links.get("next")
            self_url = links.get("self")
            if not items or not next_url or next_url == self_url:
                break

            page += 1

        with_contact = sum(1 for c in companies if c.email or c.phone)
        logger.info(
            "CEIDG: pobrano %d firm dla %s (%d z danymi kontaktowymi)",
            len(companies), date_str, with_contact,
        )
        return companies

    async def _fetch_detail(self, item: dict) -> dict:
        """Uzupełnia wpis z listy o email, telefon, PKD z endpointu szczegółów."""
        link = _str(item.get("link"))
        if not link:
            return item

        await self._detail_limiter.acquire()
        try:
            resp = await self._client.get(link)
        except httpx.HTTPError as e:
            logger.debug("CEIDG detail error for %s: %s", link, e)
            return item

        if resp.status_code == 429:
            logger.warning("CEIDG: limit szczegółów (429), pomijam kontakt dla %s", link)
            return item
        if resp.status_code != 200:
            logger.debug("CEIDG detail HTTP %s for %s", resp.status_code, link)
            return item

        payload = resp.json()
        details = payload.get("firma") or []
        if not details:
            return item

        detail = details[0] if isinstance(details, list) else details
        if not isinstance(detail, dict):
            return item

        return {**item, **detail}

    def _parse(self, item: dict) -> Optional[Company]:
        try:
            owner = item.get("wlasciciel") or {}
            nip = _str(owner.get("nip") or item.get("nip"))
            regon = _str(owner.get("regon") or item.get("regon"))

            first = _str(owner.get("imie") or item.get("imie", ""))
            last = _str(owner.get("nazwisko") or item.get("nazwisko", ""))
            firm_name = _str(item.get("nazwa") or item.get("firma") or "")
            if firm_name:
                name = firm_name
            elif first or last:
                name = f"{first} {last}".strip()
            else:
                return None

            addr = item.get("adresDzialalnosci") or item.get("adres") or {}
            street = _str(addr.get("ulica") or addr.get("ulicaNazwa", ""))
            building = _str(
                addr.get("budynek")
                or addr.get("numerBudynku")
                or addr.get("numerDomu", "")
            )
            flat = _str(addr.get("lokal") or addr.get("numerLokalu", ""))
            postal = _str(addr.get("kod") or addr.get("kodPocztowy", ""))
            street_line = (street + " " + building + (f"/{flat}" if flat else "")).strip()
            city_raw = _str(addr.get("miasto") or addr.get("miejscowosc") or addr.get("gmina", ""))
            address_parts = [
                p for p in [street_line, postal + " " + city_raw if postal else city_raw]
                if p.strip()
            ]
            address = ", ".join(address_parts) or None
            city = city_raw or None
            voivodeship = _str(addr.get("wojewodztwo", "")) or None

            kontakt = item.get("kontakt") or {}
            email = (
                _str(kontakt.get("email") or item.get("email") or item.get("adresEmail", ""))
                or None
            )
            phone = (
                _str(kontakt.get("telefon") or item.get("telefon") or item.get("numerTelefonu", ""))
                or None
            )
            website = _str(kontakt.get("www") or item.get("www") or item.get("adresStrony", "")) or None

            pkd_section, pkd_activity = _parse_pkd(item)

            registered_at = _str(
                item.get("dataRozpoczecia")
                or item.get("dataWpisu")
                or item.get("dataRejestracji", "")
            ) or None

            entry_id = _str(item.get("id") or item.get("identyfikatorWpisu") or "")
            krs_key = f"CEIDG-{nip or entry_id}"
            if krs_key == "CEIDG-":
                return None

            return Company(
                krs=krs_key,
                nip=nip or None,
                regon=regon or None,
                name=name,
                legal_form="Jednoosobowa działalność gospodarcza",
                address=address,
                city=city,
                voivodeship=voivodeship,
                email=email,
                phone=phone,
                website=website,
                registered_at=registered_at,
                pkd_section=pkd_section,
                pkd_main_activity=pkd_activity,
                source="CEIDG",
            )
        except Exception as e:
            logger.warning("CEIDG parse error: %s | item=%s", e, str(item)[:200])
            return None


def _parse_pkd(item: dict) -> tuple[Optional[str], Optional[str]]:
    from utils.pkd import get_section

    pkd_glowny = item.get("pkdGlowny") or {}
    if isinstance(pkd_glowny, dict) and pkd_glowny.get("kod"):
        kod = _str(pkd_glowny.get("kod"))
        dzial = _pkd_dzial_digits(kod)
        section = get_section(dzial) if dzial else None
        return section, _str(pkd_glowny.get("nazwa")) or None

    pkd_list = item.get("pkd") or []
    for pkd in pkd_list:
        if not isinstance(pkd, dict):
            continue
        if pkd.get("przewazajace") or pkd.get("glowne") or len(pkd_list) == 1:
            kod = _str(pkd.get("kod") or pkd.get("kodDzial") or pkd.get("podklasa", ""))
            dzial = _pkd_dzial_digits(kod)
            section = get_section(dzial) if dzial else None
            return section, _str(pkd.get("nazwa", "")) or None

    return None, None


def _pkd_dzial_digits(kod: str) -> Optional[int]:
    digits = "".join(c for c in kod if c.isdigit())
    if len(digits) >= 2:
        return int(digits[:2])
    return None


def _str(val) -> str:
    return str(val).strip() if val is not None else ""
