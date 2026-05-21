"""
Klient CEIDG Hurtowni Danych v2.
Dokumentacja: https://dane.biznes.gov.pl/api/ceidg/v2/firmy
Auth: Bearer token — klucz API z https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00
"""
import logging
from datetime import date
from typing import Optional

import httpx

from models.company import Company

logger = logging.getLogger(__name__)

CEIDG_BASE = "https://dane.biznes.gov.pl/api/ceidg/v2"


class CEIDGClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "CEIDGClient":
        self._client = httpx.AsyncClient(
            base_url=CEIDG_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def search_by_date(self, target_date: date) -> list[Company]:
        """Zwraca wszystkie JDG zarejestrowane w danym dniu."""
        date_str = target_date.isoformat()
        companies: list[Company] = []
        url = "/firmy"
        params: dict = {
            "dataod": date_str,
            "datado": date_str,
            "limit": 100,
        }

        while url:
            try:
                resp = await self._client.get(url, params=params)
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
            items = data.get("firma", [])
            for item in items:
                company = self._parse(item)
                if company:
                    companies.append(company)

            # Paginacja
            links = data.get("links", {})
            next_url = links.get("next")
            if next_url and next_url != url:
                url = next_url
                params = {}  # next_url zawiera już parametry
            else:
                break

        logger.info("CEIDG: pobrano %d firm dla %s", len(companies), date_str)
        return companies

    def _parse(self, item: dict) -> Optional[Company]:
        try:
            nip = _str(item.get("nip"))
            regon = _str(item.get("regon"))

            # Imię + nazwisko + nazwa firmy
            first = _str(item.get("imie", ""))
            last = _str(item.get("nazwisko", ""))
            firm_name = _str(item.get("firma") or item.get("nazwa") or "")
            if firm_name:
                name = firm_name
            elif first or last:
                name = f"{first} {last}".strip()
            else:
                return None

            # Adres
            addr = item.get("adresDzialalnosci") or item.get("adres") or {}
            street = _str(addr.get("ulica") or addr.get("ulicaNazwa", ""))
            building = _str(addr.get("numerBudynku") or addr.get("numerDomu", ""))
            flat = _str(addr.get("lokal") or addr.get("numerLokalu", ""))
            postal = _str(addr.get("kodPocztowy", ""))
            street_line = (street + " " + building + (f"/{flat}" if flat else "")).strip()
            city_raw = _str(addr.get("miejscowosc") or addr.get("gmina") or addr.get("miasto", ""))
            address_parts = [p for p in [street_line, postal + " " + city_raw if postal else city_raw] if p.strip()]
            address = ", ".join(address_parts) or None
            city = _str(
                addr.get("miejscowosc")
                or addr.get("gmina")
                or addr.get("miasto", "")
            ) or None
            voivodeship = _str(addr.get("wojewodztwo", "")) or None

            # Kontakt — może być na poziomie głównym lub w sub-obiekcie
            kontakt = item.get("kontakt") or {}
            email = _str(kontakt.get("email") or item.get("email", "")) or None
            phone = _str(kontakt.get("telefon") or item.get("telefon", "")) or None
            website = _str(kontakt.get("www") or item.get("www", "")) or None

            # PKD
            pkd_list = item.get("pkd") or []
            pkd_section: Optional[str] = None
            pkd_activity: Optional[str] = None
            for pkd in pkd_list:
                if pkd.get("przewazajace") or pkd.get("glowne"):
                    from utils.pkd import get_section
                    kod = _str(pkd.get("kodDzial") or pkd.get("podklasa", "")[:2])
                    try:
                        pkd_section = get_section(int(kod)) if kod.isdigit() else None
                    except Exception:
                        pkd_section = None
                    pkd_activity = _str(pkd.get("nazwa", "")) or None
                    break

            # Data rejestracji
            registered_at = _str(
                item.get("dataRozpoczecia")
                or item.get("dataWpisu")
                or item.get("dataRejestracji", "")
            ) or None

            # ID — CEIDG nie używa KRS, używamy NIP jako klucz główny (z prefiksem)
            entry_id = _str(item.get("id") or item.get("identyfikatorWpisu") or "")
            krs_key = f"CEIDG-{nip or entry_id}"
            if krs_key == "CEIDG-":
                return None

            return Company(
                krs=krs_key,
                nip=nip,
                regon=regon,
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


def _str(val) -> str:
    return str(val).strip() if val is not None else ""
