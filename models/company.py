from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Company(BaseModel):
    krs: str
    nip: Optional[str] = None
    regon: Optional[str] = None
    name: str
    legal_form: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    voivodeship: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    registered_at: Optional[str] = None
    pkd_section: Optional[str] = None        # litera sekcji PKD, np. "M"
    pkd_main_activity: Optional[str] = None  # opis przeważającej działalności
    source: str = "KRS"                      # "KRS" lub "CEIDG"
    hubspot_company_id: Optional[str] = None
    hubspot_contact_id: Optional[str] = None
    hubspot_synced_at: Optional[datetime] = None
    email_sent: bool = False
    email_sent_at: Optional[datetime] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        data = self.model_dump()
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Company":
        return cls(**data)


class EmailTemplate(BaseModel):
    id: str
    name: str
    subject: str
    body: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def render(self, company: Company) -> tuple[str, str]:
        replacements = {
            "{{nazwa_firmy}}": company.name or "",
            "{{adres}}": company.address or "",
            "{{nip}}": company.nip or "",
            "{{krs}}": company.krs or "",
            "{{data_rejestracji}}": company.registered_at or "",
            "{{strona_www}}": company.website or "",
        }
        subject = self.subject
        body = self.body
        for placeholder, value in replacements.items():
            subject = subject.replace(placeholder, value)
            body = body.replace(placeholder, value)
        return subject, body

    def to_dict(self) -> dict:
        data = self.model_dump()
        data["updated_at"] = data["updated_at"].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EmailTemplate":
        return cls(**data)


class EmailLog(BaseModel):
    company_krs: str
    template_id: str
    to_email: str
    status: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        data = self.model_dump()
        data["sent_at"] = data["sent_at"].isoformat()
        return data
