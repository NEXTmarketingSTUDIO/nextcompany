import asyncio
import logging
import math
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Główne miasta polskie z przybliżonymi koordynatami (lat, lon)
POLISH_CITIES: dict[str, tuple[float, float]] = {
    "WARSZAWA": (52.2297, 21.0122), "KRAKÓW": (50.0647, 19.9450),
    "ŁÓDŹ": (51.7592, 19.4550), "WROCŁAW": (51.1079, 17.0385),
    "POZNAŃ": (52.4064, 16.9252), "GDAŃSK": (54.3520, 18.6466),
    "SZCZECIN": (53.4285, 14.5528), "BYDGOSZCZ": (53.1235, 18.0084),
    "LUBLIN": (51.2465, 22.5684), "KATOWICE": (50.2649, 19.0238),
    "BIAŁYSTOK": (53.1325, 23.1688), "GDYNIA": (54.5189, 18.5305),
    "CZĘSTOCHOWA": (50.8118, 19.1203), "RADOM": (51.4027, 21.1471),
    "SOSNOWIEC": (50.2863, 19.1041), "TORUŃ": (53.0137, 18.5981),
    "KIELCE": (50.8661, 20.6286), "RZESZÓW": (50.0412, 21.9991),
    "GLIWICE": (50.2945, 18.6714), "ZABRZE": (50.3249, 18.7857),
    "OLSZTYN": (53.7784, 20.4801), "BIELSKO-BIAŁA": (49.8225, 19.0469),
    "BYTOM": (50.3480, 18.9160), "ZIELONA GÓRA": (51.9355, 15.5062),
    "RYBNIK": (50.0971, 18.5411), "RUDA ŚLĄSKA": (50.2586, 18.8564),
    "OPOLE": (50.6751, 17.9213), "TYCHY": (50.1292, 18.9986),
    "GORZÓW WIELKOPOLSKI": (52.7325, 15.2369), "DĄBROWA GÓRNICZA": (50.3241, 19.1894),
    "ELBLĄG": (54.1522, 19.4031), "PŁOCK": (52.5463, 19.7064),
    "WAŁBRZYCH": (50.7714, 16.2842), "WŁOCŁAWEK": (52.6483, 19.0677),
    "TARNÓW": (50.0121, 20.9858), "CHORZÓW": (50.2978, 18.9528),
    "KOSZALIN": (54.1944, 16.1716), "KALISZ": (51.7611, 18.0910),
    "LEGNICA": (51.2070, 16.1551), "GRUDZIĄDZ": (53.4864, 18.7536),
    "JAWORZNO": (50.2050, 19.2744), "SŁUPSK": (54.4641, 17.0286),
    "JASTRZĘBIE-ZDRÓJ": (49.9571, 18.6108), "NOWE TYCHY": (50.1292, 18.9986),
    "SIEDLCE": (52.1670, 22.2903), "MYSŁOWICE": (50.2072, 19.1656),
    "KONIN": (52.2229, 18.2511), "PIOTRKÓW TRYBUNALSKI": (51.4052, 19.7031),
    "INOWROCŁAW": (52.7980, 18.2610), "LUBIN": (51.4005, 16.2027),
    "OSTROWIEC ŚWIĘTOKRZYSKI": (50.9286, 21.3860), "SUWAŁKI": (54.1118, 22.9310),
    "GNIEZNO": (52.5349, 17.5974), "STARGARD": (53.3369, 15.0494),
    "PIŁA": (53.1514, 16.7383), "OSTRÓW WIELKOPOLSKI": (51.6521, 17.8183),
    "NOWY SĄCZ": (49.6249, 20.6945), "PRZEMYŚL": (49.7836, 22.7677),
    "TARNOWSKIE GÓRY": (50.4428, 18.8620), "ZAMOŚĆ": (50.7229, 23.2520),
    "ŻORY": (50.0457, 18.7028), "MIELEC": (50.2877, 21.4270),
    "STALOWA WOLA": (50.5784, 22.0498), "TOMASZÓW MAZOWIECKI": (51.5260, 20.0104),
    "KĘDZIERZYN-KOŹLE": (50.3458, 18.2173), "LESZNO": (51.8441, 16.5752),
    "LEGIONOWO": (52.4057, 20.9339), "PRUSZKÓW": (52.1702, 20.8006),
    "WIELICZKA": (49.9881, 20.0658), "OSTROŁĘKA": (53.0833, 21.5744),
    "ŻYRARDÓW": (52.0487, 20.4458), "BĘDZIN": (50.3214, 19.1244),
    "WODZISŁAW ŚLĄSKI": (49.9986, 18.4611), "NOWY TARG": (49.4776, 20.0323),
    "SANOK": (49.5586, 22.1973), "JASŁO": (49.7453, 21.4716),
    "KROSNO": (49.6893, 21.7707), "TCZEW": (54.0896, 18.7786),
    "CHEŁM": (51.1435, 23.4719), "ŚWIDNICA": (50.8440, 16.4844),
    "ZDUŃSKA WOLA": (51.5991, 18.9340), "OŚWIĘCIM": (50.0348, 19.2079),
    "SIEMIANOWICE ŚLĄSKIE": (50.3066, 19.0239), "PABIANICE": (51.6643, 19.3541),
    "SIERADZ": (51.5956, 18.7300), "SKIERNIEWICE": (51.9592, 20.1531),
    "ŚWIDNIK": (51.2226, 22.6930), "ŁOMŻA": (53.1777, 22.0590),
    "BIAŁA PODLASKA": (52.0316, 23.1164), "TARNOBRZEG": (50.5773, 21.6798),
    "ZAWIERCIE": (50.4839, 19.4237), "RACIBÓRZ": (50.0929, 18.2184),
    "NOWY DWÓR MAZOWIECKI": (52.4326, 20.7020), "ŚWIĘTOCHŁOWICE": (50.2952, 18.9146),
    "SOPOT": (54.4418, 18.5601), "JELENIA GÓRA": (50.9044, 15.7312),
    "MYSŁOWICE": (50.2072, 19.1656),
}

_geocode_cache: dict[str, Optional[tuple[float, float]]] = {}


def _normalize(city: str) -> str:
    return city.strip().upper()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_city_coords_static(city: str) -> Optional[tuple[float, float]]:
    return POLISH_CITIES.get(_normalize(city))


async def geocode_city(city: str) -> Optional[tuple[float, float]]:
    """Zwraca (lat, lon) dla podanej miejscowości. Najpierw statyczny dict, potem Nominatim."""
    key = _normalize(city)
    if key in _geocode_cache:
        return _geocode_cache[key]

    static = POLISH_CITIES.get(key)
    if static:
        _geocode_cache[key] = static
        return static

    # Fallback: Nominatim (OpenStreetMap) — bezpłatny, bez klucza
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": f"{city}, Polska", "format": "json", "limit": 1, "countrycodes": "pl"},
                headers={"User-Agent": "NEXTcompany-scraper/1.0"},
            )
            results = r.json()
            if results:
                coords = (float(results[0]["lat"]), float(results[0]["lon"]))
                _geocode_cache[key] = coords
                logger.debug(f"Nominatim: {city} → {coords}")
                return coords
    except Exception as e:
        logger.warning(f"Geocoding {city} failed: {e}")

    _geocode_cache[key] = None
    return None


async def is_within_radius(company_city: str, center_city: str, radius_km: float) -> bool:
    """Sprawdza czy company_city leży w promieniu radius_km od center_city."""
    if not company_city or not center_city:
        return False

    # Identyczna nazwa → zawsze OK
    if _normalize(company_city) == _normalize(center_city):
        return True

    center = await geocode_city(center_city)
    if center is None:
        logger.warning(f"Nie znaleziono koordynatów dla centrum: {center_city}")
        return False

    company = await geocode_city(company_city)
    if company is None:
        return False

    dist = haversine_km(center[0], center[1], company[0], company[1])
    return dist <= radius_km
