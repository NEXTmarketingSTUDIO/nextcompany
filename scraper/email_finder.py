import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
IGNORED_DOMAINS = {"example.com", "sentry.io", "google.com", "facebook.com", "w3.org"}
IGNORED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}

# Polish mobile: +48 XXX XXX XXX or 48 XXX XXX XXX
_PHONE_MOBILE = re.compile(r"(?:\+?48[\s\-]?)?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}")
# Polish landline: XX XXX XX XX or XX-XXX-XX-XX
_PHONE_LANDLINE = re.compile(r"\d{2}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}")
# tel: href
_PHONE_TEL = re.compile(r"tel:(\+?[\d\s\-]+)")


def _is_valid_email(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    if domain in IGNORED_DOMAINS:
        return False
    local = email.split("@")[0].lower()
    if any(local.endswith(ext.lstrip(".")) for ext in IGNORED_EXTENSIONS):
        return False
    if any(ext in local for ext in IGNORED_EXTENSIONS):
        return False
    return True


def _normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def find_phone(html: str) -> Optional[str]:
    """Extract first plausible Polish phone number from raw HTML text."""
    soup = BeautifulSoup(html, "html.parser")

    # tel: links are most reliable
    for a in soup.find_all("a", href=True):
        m = _PHONE_TEL.match(a["href"])
        if m:
            digits = re.sub(r"[\s\-]", "", m.group(1))
            if len(digits) >= 9:
                return m.group(1).strip()

    text = soup.get_text(separator=" ")

    for pattern in (_PHONE_MOBILE, _PHONE_LANDLINE):
        for m in pattern.finditer(text):
            raw = m.group(0)
            digits = re.sub(r"[\s\-+]", "", raw)
            # Skip NIP (10 digits), REGON (9/14 digits that look like phone),
            # postcodes (5 digits), and sequences shorter than 9 digits
            if len(digits) < 9 or len(digits) > 12:
                continue
            # Reject if surrounded by more digits (part of a longer number)
            start, end = m.start(), m.end()
            if (start > 0 and text[start - 1].isdigit()) or (
                end < len(text) and text[end].isdigit()
            ):
                continue
            return raw.strip()

    return None


async def find_contact(url: str) -> dict[str, Optional[str]]:
    """Return {"email": ..., "phone": ...} for a company website."""
    result: dict[str, Optional[str]] = {"email": None, "phone": None}
    if not url:
        return result

    url = _normalize_url(url)

    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NEXTcompany-bot/1.0)"},
    ) as client:
        pages_html: list[str] = []

        main_html = await _fetch_html(client, url)
        if main_html:
            pages_html.append(main_html)

        if not result["email"] or not result["phone"]:
            contact_paths = ["/kontakt", "/contact", "/o-nas", "/about"]
            for path in contact_paths:
                html = await _fetch_html(client, urljoin(url, path))
                if html:
                    pages_html.append(html)
                    break

        for html in pages_html:
            if not result["email"]:
                emails = _extract_emails(html)
                if emails:
                    result["email"] = emails[0]
            if not result["phone"]:
                result["phone"] = find_phone(html)
            if result["email"] and result["phone"]:
                break

    return result


async def find_email(url: str) -> Optional[str]:
    contact = await find_contact(url)
    return contact["email"]


async def _fetch_html(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        return resp.text
    except (httpx.TimeoutException, httpx.ConnectError, httpx.TooManyRedirects):
        logger.debug(f"Nie udalo sie polaczyc z {url}")
        return None
    except Exception as e:
        logger.debug(f"Blad scrapingu {url}: {e}")
        return None


def _extract_emails(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    mailto_emails = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            if EMAIL_REGEX.match(email) and _is_valid_email(email):
                mailto_emails.append(email)

    if mailto_emails:
        return mailto_emails

    text = soup.get_text(separator=" ")
    found = EMAIL_REGEX.findall(text)
    return [e for e in found if _is_valid_email(e)]
