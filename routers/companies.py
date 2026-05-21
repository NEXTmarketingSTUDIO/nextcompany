from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models.company import Company
from services.firebase_service import (
    FirebaseNotConfiguredError,
    count_companies_without_contact,
    delete_companies,
    delete_companies_without_contact,
    delete_company,
    get_companies,
    get_company,
)

router = APIRouter(prefix="/companies", tags=["companies"])


class BulkDeleteRequest(BaseModel):
    krs_list: list[str]


@router.get("", response_model=dict)
async def list_companies(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    date_filter: Optional[str] = Query(None, description="Format YYYY-MM-DD"),
    email_sent: Optional[bool] = Query(None),
    has_email: Optional[bool] = Query(None, description="True = tylko z mailem, False = tylko bez maila"),
):
    offset = (page - 1) * limit
    try:
        companies, total = get_companies(
            limit=limit,
            offset=offset,
            date_filter=date_filter,
            email_sent=email_sent,
            has_email=has_email,
        )
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "items": [c.model_dump() for c in companies],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
    }


@router.get("/no-contact/count")
async def no_contact_count():
    """Liczba firm w bazie bez emaila i telefonu."""
    try:
        count = count_companies_without_contact()
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"count": count}


@router.delete("/no-contact", status_code=200)
async def delete_no_contact():
    """Usuwa wszystkie firmy bez danych kontaktowych (brak emaila i telefonu)."""
    try:
        deleted = delete_companies_without_contact()
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"deleted": deleted}


@router.get("/{krs}", response_model=Company)
async def get_company_by_krs(krs: str):
    try:
        company = get_company(krs)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not company:
        raise HTTPException(status_code=404, detail=f"Spolka KRS {krs} nie znaleziona")

    return company


@router.delete("/{krs}", status_code=204)
async def delete_company_by_krs(krs: str):
    try:
        found = delete_company(krs)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not found:
        raise HTTPException(status_code=404, detail=f"Spolka KRS {krs} nie znaleziona")


@router.delete("", status_code=200)
async def bulk_delete_companies(body: BulkDeleteRequest):
    try:
        deleted = delete_companies(body.krs_list)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"deleted": deleted}
