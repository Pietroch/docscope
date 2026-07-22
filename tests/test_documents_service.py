# tests/test_documents_service.py
#
# Covers the ingestion pipeline (file name parsing, get-or-create on
# company/worker, insert) against an isolated in-memory SQLite engine -
# never touches the real DB_PATH file. StaticPool keeps a single connection
# alive so the in-memory schema survives across Session() calls.

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from docscope.services import documents as documents_service
from docscope.services.documents import (
    Base,
    InvalidDocumentName,
    create_documents,
    get_document,
    list_documents,
    parse_document_name,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(documents_service, "engine", engine)


def test_parse_document_name_valid():
    info = parse_document_name("Dupont Jean_ACME_Bulletin_20260105")
    assert info == {
        "last_name": "Dupont",
        "first_name": "Jean",
        "company": "ACME",
        "type": "Bulletin",
        "date": date(2026, 1, 5),
    }


def test_parse_document_name_invalid():
    with pytest.raises(ValueError):
        parse_document_name("not_a_valid_name")


def test_create_documents_creates_company_and_worker():
    ids = create_documents(
        [{"name": "Dupont Jean_ACME_Bulletin_20260105", "fields": [("Net", "1500,00")]}],
        "ucm",
    )
    assert len(ids) == 1

    document = get_document(ids[0])
    assert document["company"] == "ACME"
    assert document["person"] == "Dupont Jean"
    assert document["template"] == "ucm"


def test_create_documents_reuses_existing_company_and_worker():
    create_documents([{"name": "Dupont Jean_ACME_Bulletin_20260105", "fields": []}], "ucm")
    create_documents([{"name": "Dupont Jean_ACME_Bulletin_20260205", "fields": []}], "ucm")

    result = list_documents()
    assert len(result) == 2
    assert result[0]["company"] == result[1]["company"] == "ACME"


def test_create_documents_rejects_invalid_name_without_partial_insert():
    with pytest.raises(InvalidDocumentName):
        create_documents(
            [
                {"name": "Dupont Jean_ACME_Bulletin_20260105", "fields": []},
                {"name": "not_a_valid_name", "fields": []},
            ],
            "ucm",
        )
    assert list_documents() == []


def test_get_document_missing_returns_none():
    assert get_document(999) is None
