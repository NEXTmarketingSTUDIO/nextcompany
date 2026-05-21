import logging
from typing import Optional

from config import settings
from models.company import Company, EmailTemplate

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(Exception):
    pass


def _get_sg_client():
    if not settings.sendgrid_configured:
        raise EmailNotConfiguredError(
            "SendGrid nie jest skonfigurowany. Uzupelnij SENDGRID_API_KEY i SENDGRID_FROM_EMAIL w .env"
        )
    from sendgrid import SendGridAPIClient

    return SendGridAPIClient(settings.sendgrid_api_key)


def send_email(
    to_email: str, template: EmailTemplate, company: Company
) -> tuple[bool, Optional[str]]:
    try:
        sg = _get_sg_client()
    except EmailNotConfiguredError as e:
        return False, str(e)

    subject, body = template.render(company)

    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=(settings.sendgrid_from_email, settings.sendgrid_from_name),
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )

    try:
        response = sg.send(message)
        if response.status_code in (200, 202):
            logger.info(f"Mail wyslany do {to_email} (KRS {company.krs})")
            return True, None
        else:
            error_msg = f"SendGrid status {response.status_code}: {response.body}"
            logger.warning(error_msg)
            return False, error_msg
    except Exception as e:
        logger.error(f"Blad wysylki maila do {to_email}: {e}")
        return False, str(e)
