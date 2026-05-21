"""
OAuth 2.0 dla HubSpot + endpoint synchronizacji spółek.
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from config import settings
from services.hubspot_service import HubSpotNotConnectedError, HubSpotService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hubspot"])

HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_SCOPES = "crm.objects.companies.write crm.objects.contacts.write crm.objects.companies.read crm.objects.contacts.read"


# ── OAuth ────────────────────────────────────────────────────────────────────

@router.get("/auth/hubspot")
async def hubspot_auth():
    """Przekierowanie na stronę autoryzacji HubSpot."""
    if not settings.hubspot_configured:
        raise HTTPException(
            status_code=503,
            detail="Brak HUBSPOT_CLIENT_ID / HUBSPOT_CLIENT_SECRET w .env"
        )
    params = {
        "client_id": settings.hubspot_client_id,
        "redirect_uri": settings.hubspot_redirect_uri,
        "scope": HUBSPOT_SCOPES,
    }
    return RedirectResponse(f"{HUBSPOT_AUTH_URL}?{urlencode(params)}")


@router.get("/auth/hubspot/callback")
async def hubspot_callback(code: str = Query(...)):
    """HubSpot przekierowuje tutaj po autoryzacji."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Wymień code na tokeny
        resp = await client.post(
            HUBSPOT_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.hubspot_client_id,
                "client_secret": settings.hubspot_client_secret,
                "redirect_uri": settings.hubspot_redirect_uri,
                "code": code,
            },
        )
        if not resp.is_success:
            logger.error("HubSpot token exchange failed: %s", resp.text)
            return RedirectResponse(f"{settings.frontend_url}/settings?hubspot=error")

        token_data = resp.json()

        # Pobierz info o portalu
        info_resp = await client.get(
            f"https://api.hubapi.com/oauth/v1/access-tokens/{token_data['access_token']}"
        )
        portal_info = info_resp.json() if info_resp.is_success else {}

    from services.firebase_service import save_hubspot_token

    save_hubspot_token({
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": (datetime.utcnow() + timedelta(seconds=token_data["expires_in"])).isoformat(),
        "hub_id": portal_info.get("hub_id"),
        "hub_domain": portal_info.get("hub_domain"),
        "user": portal_info.get("user"),
        "connected_at": datetime.utcnow().isoformat(),
    })

    logger.info("HubSpot połączony: portal %s (%s)", portal_info.get("hub_id"), portal_info.get("hub_domain"))
    return RedirectResponse(f"{settings.frontend_url}/settings?hubspot=connected")


@router.get("/auth/hubspot/status")
async def hubspot_status():
    """Zwraca status połączenia z HubSpot."""
    hs = HubSpotService()
    status = await hs.get_connection_status()
    if not status:
        return {"connected": False}
    return status


@router.delete("/auth/hubspot")
async def hubspot_disconnect():
    """Rozłącza HubSpot (usuwa token z Firebase)."""
    from services.firebase_service import delete_hubspot_token
    delete_hubspot_token()
    return {"message": "HubSpot rozłączony"}


# ── Synchronizacja spółek ─────────────────────────────────────────────────────

@router.post("/companies/{krs}/hubspot-sync")
async def sync_one(krs: str):
    """Synchronizuje jedną spółkę z HubSpot."""
    from services.firebase_service import FirebaseNotConfiguredError, get_company, save_company

    try:
        company = get_company(krs)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not company:
        raise HTTPException(status_code=404, detail=f"Spółka KRS {krs} nie znaleziona")

    try:
        hs = HubSpotService()
        result = await hs.sync_company(company)
    except HubSpotNotConnectedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("HubSpot sync error for %s: %s", krs, e)
        raise HTTPException(status_code=502, detail=f"Błąd HubSpot: {e}")

    # Zapisz HubSpot ID w Firebase
    company.hubspot_company_id = result["hubspot_company_id"]
    company.hubspot_contact_id = result.get("hubspot_contact_id")
    company.hubspot_synced_at = datetime.utcnow()
    try:
        save_company(company)
    except FirebaseNotConfiguredError:
        pass

    return result


class BulkSyncResult(BaseModel):
    synced: int
    failed: int
    total: int


class BulkHubSpotSyncRequest(BaseModel):
    krs_list: list[str]


@router.post("/companies/hubspot-sync-bulk", response_model=BulkSyncResult)
async def sync_bulk(body: BulkHubSpotSyncRequest):
    """Synchronizuje zaznaczone spółki (lista KRS) z HubSpot."""
    from services.firebase_service import FirebaseNotConfiguredError, get_company, save_company

    if not body.krs_list:
        raise HTTPException(status_code=400, detail="Lista KRS nie może być pusta")

    hs = HubSpotService()
    synced = failed = 0

    for krs in body.krs_list:
        try:
            company = get_company(krs)
        except FirebaseNotConfiguredError as e:
            raise HTTPException(status_code=503, detail=str(e))

        if not company:
            failed += 1
            continue

        try:
            result = await hs.sync_company(company)
            company.hubspot_company_id = result["hubspot_company_id"]
            company.hubspot_contact_id = result.get("hubspot_contact_id")
            company.hubspot_synced_at = datetime.utcnow()
            save_company(company)
            synced += 1
        except HubSpotNotConnectedError:
            raise HTTPException(status_code=503, detail="HubSpot nie jest połączony")
        except Exception as e:
            logger.warning("HubSpot sync failed for %s: %s", krs, e)
            failed += 1

    return BulkSyncResult(synced=synced, failed=failed, total=len(body.krs_list))


@router.post("/companies/hubspot-sync-all", response_model=BulkSyncResult)
async def sync_all(limit: int = Query(500, ge=1, le=2000)):
    """Synchronizuje wszystkie spółki z Firebase do HubSpot."""
    from services.firebase_service import FirebaseNotConfiguredError, get_companies, save_company

    try:
        companies, total = get_companies(limit=limit, offset=0)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    hs = HubSpotService()
    synced = failed = 0

    for company in companies:
        try:
            result = await hs.sync_company(company)
            company.hubspot_company_id = result["hubspot_company_id"]
            company.hubspot_contact_id = result.get("hubspot_contact_id")
            company.hubspot_synced_at = datetime.utcnow()
            save_company(company)
            synced += 1
        except HubSpotNotConnectedError:
            raise HTTPException(status_code=503, detail="HubSpot nie jest połączony")
        except Exception as e:
            logger.warning("HubSpot sync failed for %s: %s", company.krs, e)
            failed += 1

    return BulkSyncResult(synced=synced, failed=failed, total=total)
