from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.company import EmailTemplate
from services.firebase_service import (
    FirebaseNotConfiguredError,
    delete_template,
    get_all_templates,
    get_template,
    save_template,
)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    id: str
    name: str
    subject: str
    body: str


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    body: str | None = None


@router.get("", response_model=list[dict])
async def list_templates():
    try:
        templates = get_all_templates()
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return [t.to_dict() for t in templates]


@router.get("/{template_id}", response_model=dict)
async def get_template_by_id(template_id: str):
    try:
        template = get_template(template_id)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not template:
        raise HTTPException(status_code=404, detail=f"Szablon {template_id} nie znaleziony")
    return template.to_dict()


@router.post("", response_model=dict, status_code=201)
async def create_template(payload: TemplateCreate):
    try:
        existing = get_template(payload.id)
        if existing:
            raise HTTPException(status_code=409, detail=f"Szablon o id '{payload.id}' juz istnieje")
        template = EmailTemplate(**payload.model_dump())
        save_template(template)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return template.to_dict()


@router.put("/{template_id}", response_model=dict)
async def update_template(template_id: str, payload: TemplateUpdate):
    try:
        template = get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail=f"Szablon {template_id} nie znaleziony")
        update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        updated = template.model_copy(update=update_data)
        save_template(updated)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return updated.to_dict()


@router.delete("/{template_id}", status_code=204)
async def remove_template(template_id: str):
    try:
        deleted = delete_template(template_id)
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Szablon {template_id} nie znaleziony")
