"""
Integracja z HubSpot CRM — push spółek jako Companies + Contacts.
Używa OAuth 2.0 (token przechowywany w Firebase).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import httpx

from config import settings
from models.company import Company

logger = logging.getLogger(__name__)

HUBSPOT_API = "https://api.hubapi.com"
HUBSPOT_TOKEN_URL = f"{HUBSPOT_API}/oauth/v1/token"


class HubSpotNotConnectedError(Exception):
    pass


class HubSpotService:

    async def _get_valid_token(self) -> str:
        from services.firebase_service import get_hubspot_token, save_hubspot_token

        token_data = get_hubspot_token()
        if not token_data or not token_data.get("access_token"):
            raise HubSpotNotConnectedError("HubSpot nie jest połączony. Przejdź do Ustawień → Integracje.")

        # Odśwież jeśli wygasa za mniej niż 60 sekund
        expires_at_str = token_data.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.utcnow() >= expires_at - timedelta(seconds=60):
                token_data = await self._refresh(token_data["refresh_token"])
        except (ValueError, TypeError):
            pass

        return token_data["access_token"]

    async def _refresh(self, refresh_token: str) -> dict:
        from services.firebase_service import get_hubspot_token, save_hubspot_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                HUBSPOT_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.hubspot_client_id,
                    "client_secret": settings.hubspot_client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            new_data = resp.json()

        existing = get_hubspot_token() or {}
        merged = {
            **existing,
            "access_token": new_data["access_token"],
            "refresh_token": new_data.get("refresh_token", existing.get("refresh_token")),
            "expires_at": (datetime.utcnow() + timedelta(seconds=new_data["expires_in"])).isoformat(),
        }
        save_hubspot_token(merged)
        logger.info("HubSpot token odświeżony")
        return merged

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _company_properties(self, company: Company) -> dict:
        """Mapuje Company na właściwości HubSpot."""
        domain = ""
        if company.website:
            try:
                parsed = urlparse(company.website if "://" in company.website else "https://" + company.website)
                domain = parsed.netloc.lstrip("www.") or ""
            except Exception:
                pass

        props: dict = {
            "name": company.name,
            "country": "Poland",
        }
        if domain:
            props["domain"] = domain
        if company.website:
            props["website"] = company.website
        if company.phone:
            props["phone"] = company.phone
        if company.address:
            props["address"] = company.address
        if company.city:
            props["city"] = company.city
        if company.voivodeship:
            props["state"] = company.voivodeship
        if company.legal_form:
            props["description"] = company.legal_form
        if company.nip:
            props["annualrevenue"] = ""  # placeholder — nadpisz custom prop jeśli masz
            # NIP/KRS najlepiej dodać jako Custom Properties w HubSpot UI
            # Tutaj zapisujemy w description jako fallback
            props["description"] = f"{company.legal_form or ''} | NIP: {company.nip} | {company.source}".strip(" |")

        return props

    async def push_company(self, company: Company) -> str:
        """Tworzy lub aktualizuje firmę w HubSpot. Zwraca HubSpot company ID."""
        token = await self._get_valid_token()
        headers = self._headers(token)
        props = self._company_properties(company)

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Jeśli mamy już ID → aktualizuj
            if company.hubspot_company_id:
                resp = await client.patch(
                    f"{HUBSPOT_API}/crm/v3/objects/companies/{company.hubspot_company_id}",
                    headers=headers,
                    json={"properties": props},
                )
                if resp.status_code == 404:
                    company.hubspot_company_id = None  # ID nieaktualne → utwórz nowy
                else:
                    resp.raise_for_status()
                    logger.debug(f"HubSpot: zaktualizowano firmę {company.hubspot_company_id}")
                    return company.hubspot_company_id

            # Sprawdź duplikaty po domenie
            domain = props.get("domain")
            if domain:
                search = await client.post(
                    f"{HUBSPOT_API}/crm/v3/objects/companies/search",
                    headers=headers,
                    json={
                        "filterGroups": [{"filters": [
                            {"propertyName": "domain", "operator": "EQ", "value": domain}
                        ]}],
                        "limit": 1,
                    },
                )
                if search.status_code == 200:
                    results = search.json().get("results", [])
                    if results:
                        existing_id = results[0]["id"]
                        # Aktualizuj istniejący
                        await client.patch(
                            f"{HUBSPOT_API}/crm/v3/objects/companies/{existing_id}",
                            headers=headers,
                            json={"properties": props},
                        )
                        logger.debug(f"HubSpot: znaleziono duplikat firmy, zaktualizowano {existing_id}")
                        return existing_id

            # Utwórz nowy rekord
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/companies",
                headers=headers,
                json={"properties": props},
            )
            resp.raise_for_status()
            new_id = resp.json()["id"]
            logger.debug(f"HubSpot: utworzono firmę {new_id} ({company.name})")
            return new_id

    async def push_contact(self, company: Company) -> Optional[str]:
        """Tworzy lub aktualizuje kontakt (email) w HubSpot. Zwraca contact ID."""
        if not company.email:
            return None

        token = await self._get_valid_token()
        headers = self._headers(token)
        props: dict = {"email": company.email}
        if company.phone:
            props["phone"] = company.phone
        if company.name:
            props["company"] = company.name

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Sprawdź czy kontakt już istnieje
            if company.hubspot_contact_id:
                resp = await client.patch(
                    f"{HUBSPOT_API}/crm/v3/objects/contacts/{company.hubspot_contact_id}",
                    headers=headers,
                    json={"properties": props},
                )
                if resp.status_code != 404:
                    resp.raise_for_status()
                    return company.hubspot_contact_id

            # Utwórz nowy (HubSpot deduplikuje po email automatycznie → zwróci 409 z ID)
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/contacts",
                headers=headers,
                json={"properties": props},
            )
            if resp.status_code == 409:
                # Kontakt już istnieje — wyciągnij ID z odpowiedzi
                existing_id = resp.json().get("message", "").split(":")[-1].strip()
                if existing_id.isdigit():
                    return existing_id
                return None

            resp.raise_for_status()
            contact_id = resp.json()["id"]
            logger.debug(f"HubSpot: utworzono kontakt {contact_id} ({company.email})")
            return contact_id

    async def associate_contact_to_company(self, contact_id: str, company_id: str) -> None:
        """Tworzy powiązanie Contact → Company w HubSpot."""
        token = await self._get_valid_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(
                f"{HUBSPOT_API}/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}",
                headers=self._headers(token),
                json=[],
            )
            if resp.status_code not in (200, 201, 204):
                logger.warning(f"HubSpot: błąd asocjacji contact {contact_id} → company {company_id}: {resp.status_code}")

    async def sync_company(self, company: Company) -> dict:
        """Pełny sync: push Company + Contact + asocjacja. Zwraca dict z ID."""
        company_id = await self.push_company(company)
        contact_id = await self.push_contact(company)
        if contact_id and company_id:
            await self.associate_contact_to_company(contact_id, company_id)

        return {
            "hubspot_company_id": company_id,
            "hubspot_contact_id": contact_id,
            "hubspot_url": f"https://app.hubspot.com/contacts/{await self._get_portal_id()}/company/{company_id}",
        }

    async def _get_portal_id(self) -> str:
        from services.firebase_service import get_hubspot_token
        token_data = get_hubspot_token() or {}
        return str(token_data.get("hub_id", ""))

    async def get_connection_status(self) -> Optional[dict]:
        """Zwraca info o połączeniu lub None jeśli nie połączono."""
        from services.firebase_service import get_hubspot_token
        token_data = get_hubspot_token()
        if not token_data:
            return None
        return {
            "connected": True,
            "hub_id": token_data.get("hub_id"),
            "hub_domain": token_data.get("hub_domain"),
            "user": token_data.get("user"),
            "connected_at": token_data.get("connected_at"),
            "hubspot_url": f"https://app.hubspot.com/contacts/{token_data.get('hub_id')}",
        }
