import asyncio
import logging
from datetime import date
from typing import Optional

import httpx

from models.company import Company

logger = logging.getLogger(__name__)

BASE_URL = "https://api-krs.ms.gov.pl/api/krs"

# Kalibracja potwierdzona empirycznie (endpoint /scrape/calibrate):
# KRS 1_200_000 → 15.10.2025,  ~185 nowych spółek dziennie
CALIBRATION_KRS = 1_200_000
CALIBRATION_DATE = date(2025, 10, 15)
ESTIMATED_DAILY = 185
# Okno ±50 000 = ±270 dni — wystarczający bufor na błąd szacowania
SEARCH_WINDOW = 50_000


def _parse_krs_date(date_str: str) -> Optional[date]:
    """Parsuje datę z formatu KRS: DD.MM.YYYY"""
    try:
        d, m, y = date_str.strip().split(".")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _estimate_krs(target: date) -> int:
    days = (target - CALIBRATION_DATE).days
    return max(1, CALIBRATION_KRS + days * ESTIMATED_DAILY)


class KRSClient:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _fetch(self, krs_num: int) -> tuple[Optional[date], Optional[dict]]:
        """Pobiera OdpisAktualny. Zwraca (data_rejestracji, dane) lub (None, None)."""
        krs_str = str(krs_num).zfill(10)
        for rejestr in ("P", "S"):
            try:
                resp = await self._client.get(
                    f"{BASE_URL}/OdpisAktualny/{krs_str}",
                    params={"rejestr": rejestr, "format": "json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    date_str = data.get("odpis", {}).get("naglowekA", {}).get("dataRejestracjiWKRS", "")
                    return _parse_krs_date(date_str), data
                if resp.status_code != 404:
                    logger.warning(f"KRS {krs_str} rejestr={rejestr}: HTTP {resp.status_code}")
            except Exception as e:
                logger.debug(f"Blad KRS {krs_str}: {e}")
        return None, None

    async def _fetch_nearest(self, krs_num: int, radius: int = 30) -> tuple[Optional[int], Optional[date], Optional[dict]]:
        """Szuka działającego numeru KRS w pobliżu krs_num (obsługa luk w numeracji)."""
        offsets = [0] + [v for i in range(1, radius + 1) for v in (i, -i)]
        for off in offsets:
            num = krs_num + off
            if num <= 0:
                continue
            reg_date, data = await self._fetch(num)
            if reg_date is not None:
                return num, reg_date, data
            await asyncio.sleep(0.05)
        return None, None, None

    async def search_by_date(self, target_date: date, limit: int | None = None) -> list[Company]:
        estimate = _estimate_krs(target_date)
        lo = max(1, estimate - SEARCH_WINDOW)
        hi = estimate + SEARCH_WINDOW
        logger.info(f"Szukam spolkach z {target_date} | szac. KRS ~{estimate} | zakres [{lo}, {hi}]")

        # 1. Binary search — znajdź dowolny KRS z target_date
        pivot = await self._binary_search(target_date, lo, hi)
        if pivot is None:
            logger.warning(f"Brak wynikow dla {target_date} w zakresie [{lo}, {hi}]")
            return []
        logger.info(f"Pivot: KRS {pivot}")

        # 2. Pełny skan wstecz tylko bez limitu — przy limicie zaczynamy od pivota
        if limit is not None:
            first = pivot
            logger.info(f"Limit {limit}: zbieram max {limit} spolek od KRS {first} (pomijam skan wstecz)")
        else:
            first = await self._scan_to_boundary(pivot, target_date, direction=-1)
            logger.info(f"Pierwsza spolka z {target_date}: KRS {first}")

        # 3. Zbierz spółki z target_date
        companies = await self._collect(first, target_date, limit=limit)
        logger.info(f"Lacznie {len(companies)} spolkach z {target_date}")
        return companies

    async def _binary_search(self, target: date, lo: int, hi: int) -> Optional[int]:
        """Binary search — zwraca numer KRS z datą == target, lub None."""
        best_below: Optional[int] = None
        iterations = 0

        while lo <= hi and iterations < 30:
            iterations += 1
            mid = (lo + hi) // 2
            num, reg_date, _ = await self._fetch_nearest(mid)
            await asyncio.sleep(0.3)

            if num is None or reg_date is None:
                hi = mid - 200
                continue

            logger.debug(f"  BS iter {iterations}: KRS {num} → {reg_date}")

            if reg_date == target:
                return num
            elif reg_date < target:
                best_below = num
                lo = num + 1
            else:
                hi = num - 1

        # Nie trafiliśmy dokładnie — sprawdź okolicę best_below
        if best_below is not None:
            for offset in range(1, 300):
                num, reg_date, _ = await self._fetch_nearest(best_below + offset)
                await asyncio.sleep(0.15)
                if reg_date is None:
                    continue
                if reg_date == target:
                    return num
                if reg_date > target:
                    break

        return None

    async def _scan_to_boundary(self, pivot: int, target: date, direction: int) -> int:
        """Skanuje w kierunku direction (-1 lub +1) dopóki data == target."""
        current = pivot
        consecutive_gaps = 0

        while True:
            nxt = current + direction
            if nxt <= 0:
                break
            reg_date, _ = await self._fetch(nxt)
            await asyncio.sleep(0.2)

            if reg_date is None:
                consecutive_gaps += 1
                if consecutive_gaps >= 15:
                    break
                current += direction
                continue

            consecutive_gaps = 0
            if reg_date == target:
                current = nxt
            else:
                break

        return current

    async def _collect(self, start: int, target: date, limit: int | None = None) -> list[Company]:
        """Zbiera spółki skanując od start w przód dopóki data == target."""
        companies: list[Company] = []
        current = start
        consecutive_other = 0

        while consecutive_other < 20:
            if limit is not None and len(companies) >= limit:
                break
            reg_date, data = await self._fetch(current)
            await asyncio.sleep(0.3)

            if reg_date is None:
                current += 1
                consecutive_other += 1
                continue

            if reg_date == target:
                consecutive_other = 0
                company = self._parse_company(str(current).zfill(10), data)
                if company:
                    companies.append(company)
                    logger.info(f"  ✓ KRS {current}: {company.name} | email={company.email} | www={company.website}")
                    if limit is not None and len(companies) >= limit:
                        break
            elif reg_date > target:
                break
            else:
                consecutive_other += 1

            current += 1

        return companies

    def _parse_company(self, krs: str, data: dict) -> Optional[Company]:
        try:
            from utils.pkd import get_section

            odpis = data.get("odpis", {})
            naglowek = odpis.get("naglowekA", {})
            dane = odpis.get("dane", {})
            dzial1 = dane.get("dzial1", {})
            dane_podmiotu = dzial1.get("danePodmiotu", {})
            identyfikatory = dane_podmiotu.get("identyfikatory", {})
            siedziba_i_adres = dzial1.get("siedzibaIAdres", {})
            adres = siedziba_i_adres.get("adres", {})
            siedziba = siedziba_i_adres.get("siedziba", {})

            nazwa = dane_podmiotu.get("nazwa") or f"Spolka KRS {krs}"
            forma = dane_podmiotu.get("formaPrawna")
            nip = identyfikatory.get("nip")
            regon = identyfikatory.get("regon")
            data_wpisu = naglowek.get("dataRejestracjiWKRS")

            ulica = str(adres.get("ulica", "") or "").strip()
            nr_domu = str(adres.get("nrDomu", "") or "").strip()
            nr_lokalu = str(adres.get("nrLokalu", "") or "").strip()
            kod = str(adres.get("kodPocztowy", "") or "").strip()
            miejscowosc_adres = str(adres.get("miejscowosc", "") or "").strip()

            street_line = ulica
            if nr_domu:
                street_line += " " + nr_domu
            if nr_lokalu:
                street_line += "/" + nr_lokalu
            city_line = ((kod + " ") if kod else "") + miejscowosc_adres

            adres_parts = [p for p in [street_line, city_line] if p.strip()]
            adres_str = ", ".join(adres_parts) if adres_parts else None

            city = siedziba.get("miejscowosc") or adres.get("miejscowosc")
            voivodeship = siedziba.get("wojewodztwo")

            www = siedziba_i_adres.get("adresStronyInternetowej")
            if www:
                www = www.strip()
                if www and not www.lower().startswith(("http://", "https://")):
                    www = "https://" + www.lower()

            # Email z oficjalnych danych KRS (Dane kontaktowe)
            email_krs = (
                siedziba_i_adres.get("email")
                or siedziba_i_adres.get("adresEmail")
                or siedziba_i_adres.get("adresPocztyElektronicznej")
                or dane_podmiotu.get("email")
                or dane_podmiotu.get("adresEmail")
            )
            email_krs = email_krs.strip() if isinstance(email_krs, str) else None
            email_krs = email_krs if email_krs else None

            # PKD — przeważająca działalność
            pkd_list = (
                dane.get("dzial3", {})
                .get("przedmiotDzialalnosci", {})
                .get("przedmiotPrzewazajacejDzialalnosci", [])
            )
            pkd_section = None
            pkd_main_activity = None
            if pkd_list:
                first = pkd_list[0]
                pkd_section = get_section(first.get("kodDzial"))
                pkd_main_activity = first.get("opis")

            return Company(
                krs=krs,
                nip=str(nip) if nip else None,
                regon=str(regon) if regon else None,
                name=str(nazwa),
                legal_form=str(forma) if forma else None,
                address=adres_str,
                city=city,
                voivodeship=voivodeship,
                email=email_krs,
                website=www,
                registered_at=str(data_wpisu) if data_wpisu else None,
                pkd_section=pkd_section,
                pkd_main_activity=pkd_main_activity,
            )
        except Exception as e:
            logger.error(f"Blad parsowania KRS {krs}: {e}", exc_info=True)
            return None
