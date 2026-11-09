from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import PurePath

from openpyxl import load_workbook
from pypdf import PdfReader

from app.schemas import ReferralContact

logger = logging.getLogger(__name__)

REQUIRED_REFERRAL_COLUMNS = {"friend_name", "company_name"}
OPTIONAL_REFERRAL_COLUMNS = {"phone_number", "years_of_experience"}
MAX_RESUME_CHARS = 60_000


def parse_resume_pdf(content: bytes) -> str:
    if not content:
        raise ValueError("The uploaded resume is empty.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        logger.info("Resume PDF parsing failed: %s", exc)
        raise ValueError("Could not read the PDF. Please upload a valid, text-based resume.") from exc

    normalized = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not normalized:
        raise ValueError("No selectable text was found in the PDF; scanned-image resumes are not supported.")
    return normalized[:MAX_RESUME_CHARS]


def parse_referral_file(filename: str, content: bytes) -> list[ReferralContact]:
    extension = PurePath(filename).suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        raise ValueError("Referral network file must be a .csv or .xlsx file.")
    if not content:
        raise ValueError("The uploaded referral file is empty.")

    try:
        rows = _read_csv(content) if extension == ".csv" else _read_xlsx(content)
    except (UnicodeDecodeError, csv.Error, OSError, ValueError) as exc:
        raise ValueError(f"Could not read the referral file: {exc}") from exc

    if not rows:
        raise ValueError("The referral file has no data rows.")

    headers = {str(key).strip().lower() for key in rows[0]}
    missing = REQUIRED_REFERRAL_COLUMNS - headers
    if missing:
        raise ValueError(f"Referral file is missing required columns: {', '.join(sorted(missing))}.")

    contacts: list[ReferralContact] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        normalized = {str(key).strip().lower(): value for key, value in row.items()}
        friend = _clean(normalized.get("friend_name"))
        company = _clean(normalized.get("company_name"))
        if not friend or not company:
            continue
        phone = _clean(normalized.get("phone_number"))
        key = (friend.casefold(), company.casefold(), phone)
        if key in seen:
            continue
        seen.add(key)
        contacts.append(
            ReferralContact(
                friend_name=friend,
                phone_number=phone,
                company_name=company,
            )
        )
    if not contacts:
        raise ValueError("No rows with both friend_name and company_name were found.")
    return contacts


def _read_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _read_xlsx(content: bytes) -> list[dict[str, object]]:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        iterator = worksheet.iter_rows(values_only=True)
        headers = next(iterator, None)
        if not headers:
            return []
        keys = [str(value or "").strip() for value in headers]
        result: list[dict[str, object]] = []
        for values in iterator:
            if not any(value is not None for value in values):
                continue
            result.append({key: value for key, value in zip(keys, values) if key})
        return result
    finally:
        workbook.close()


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""
