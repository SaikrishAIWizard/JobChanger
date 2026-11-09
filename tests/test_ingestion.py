from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.ingestion import parse_referral_file, parse_resume_pdf


def test_parse_referral_csv_validates_headers_and_deduplicates() -> None:
    data = b"friend_name,phone_number,company_name\nAlex,555-0100,Amazon\nAlex,555-0100,Amazon\n,555-0200,Google\n"
    contacts = parse_referral_file("network.csv", data)
    assert len(contacts) == 1
    assert contacts[0].friend_name == "Alex"
    assert contacts[0].company_name == "Amazon"


def test_parse_referral_csv_requires_columns() -> None:
    with pytest.raises(ValueError, match="company_name"):
        parse_referral_file("network.csv", b"friend_name\nAlex\n")


def test_parse_referral_xlsx_reads_active_sheet() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["friend_name", "phone_number", "company_name"])
    sheet.append(["Sam", "555-0101", "Google"])
    output = io.BytesIO()
    workbook.save(output)
    contacts = parse_referral_file("network.xlsx", output.getvalue())
    assert [(contact.friend_name, contact.company_name) for contact in contacts] == [("Sam", "Google")]


def test_parse_resume_rejects_non_pdf() -> None:
    with pytest.raises(ValueError, match="Could not read the PDF"):
        parse_resume_pdf(b"not a pdf")
