import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings
from routers import auth, companies, stats, templates
from services.scheduler import run_daily_scrape, setup_scheduler, shutdown_scheduler

logging.basicConfig(
    level=logging.INFO if settings.is_production else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="NEXTcompany API",
    description="Scraper nowo zarejestrowanych spółek KRS z automatyczną wysyłką maili",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(templates.router)
app.include_router(stats.router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "firebase_configured": settings.firebase_configured,
        "sendgrid_configured": settings.sendgrid_configured,
        "hubspot_configured": settings.hubspot_configured,
        "ceidg_configured": settings.ceidg_configured,
        "scrape_schedule": f"{settings.scrape_hour:02d}:{settings.scrape_minute:02d}",
    }


class ScrapeRequest(BaseModel):
    date: str | None = None
    branza: str | None = None       # litera PKD np. "M", lub None = wszystkie
    city: str | None = None         # np. "Poznań"
    radius_km: float | None = None  # promień w km, np. 50


@app.post("/scrape/trigger")
async def trigger_scrape(body: ScrapeRequest = ScrapeRequest()):
    target_date = date.today()
    if body.date:
        try:
            target_date = date.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Nieprawidlowy format daty. Uzyj YYYY-MM-DD")

    logger.info(
        f"Scraping: data={target_date}, branza={body.branza}, "
        f"miasto={body.city}, promien={body.radius_km}"
    )
    result = await run_daily_scrape(
        target_date=target_date,
        branza=body.branza or None,
        city=body.city or None,
        radius_km=body.radius_km,
    )
    return {
        "message": "Scraping zakończony",
        "date": str(target_date),
        "stats": result.get("stats", {}),
        "companies": result.get("companies", []),
    }


@app.get("/scrape/pkd-sections")
async def pkd_sections():
    """Zwraca listę sekcji PKD do wyświetlenia w UI."""
    from utils.pkd import PKD_SECTIONS
    return [
        {"letter": letter, "description": desc}
        for letter, (desc, _) in PKD_SECTIONS.items()
    ]


@app.get("/scrape/calibrate")
async def calibrate_krs():
    """
    Sprawdza kilka punktów KRS żeby skalibrować szacowanie numeru po dacie.
    Wywołaj raz po instalacji — zwróci rzeczywiste daty dla sprawdzanych numerów.
    """
    probe_points = [
        950_334,    # znany punkt: 31.01.2022
        1_000_000,
        1_050_000,
        1_100_000,
        1_150_000,
        1_200_000,
        1_250_000,
        1_300_000,
        1_350_000,
        1_400_000,
        1_450_000,
        1_500_000,
    ]

    results = []
    async with httpx.AsyncClient(timeout=15.0, headers={"Accept": "application/json"}, follow_redirects=True) as client:
        for krs_num in probe_points:
            krs_str = str(krs_num).zfill(10)
            found = False
            for rejestr in ("P", "S"):
                try:
                    r = await client.get(
                        f"https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs_str}",
                        params={"rejestr": rejestr, "format": "json"},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        naglowek = data.get("odpis", {}).get("naglowekA", {})
                        results.append({
                            "krs": krs_num,
                            "status": 200,
                            "rejestr": rejestr,
                            "dataRejestracjiWKRS": naglowek.get("dataRejestracjiWKRS"),
                            "nazwa": data.get("odpis", {}).get("dane", {}).get("dzial1", {}).get("danePodmiotu", {}).get("nazwa"),
                        })
                        found = True
                        break
                except Exception:
                    pass
            if not found:
                results.append({"krs": krs_num, "status": 404})
            await asyncio.sleep(0.3)

    # Wylicz szacowany dzienny przyrost między kolejnymi punktami
    valid = [r for r in results if r.get("status") == 200 and r.get("dataRejestracjiWKRS")]
    rate_info = []
    for i in range(1, len(valid)):
        try:
            from scraper.krs_client import _parse_krs_date
            d1 = _parse_krs_date(valid[i-1]["dataRejestracjiWKRS"])
            d2 = _parse_krs_date(valid[i]["dataRejestracjiWKRS"])
            krs_diff = valid[i]["krs"] - valid[i-1]["krs"]
            day_diff = (d2 - d1).days if d1 and d2 and d2 > d1 else None
            rate = round(krs_diff / day_diff, 1) if day_diff else None
            rate_info.append({"from_krs": valid[i-1]["krs"], "to_krs": valid[i]["krs"], "days": day_diff, "krs_per_day": rate})
        except Exception:
            pass

    return {"probe_results": results, "rate_analysis": rate_info}
