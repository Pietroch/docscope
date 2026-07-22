# api/src/docscope/routers/documents.py
#
# HTTP layer only: validate input, delegate to the documents service.

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from docscope.services.documents import (
    InvalidDocumentName,
    create_documents,
    get_document,
    list_documents,
    update_field,
)

router = APIRouter()


class FieldIn(BaseModel):
    label: str
    value: str | None = None


class FieldUpdateIn(BaseModel):
    label: str
    value: str | None = None


class DocumentIn(BaseModel):
    name: str
    fields: list[FieldIn] = []


class DocumentsIn(BaseModel):
    template: Literal["ucm", "delvaux", "apside"]
    documents: list[DocumentIn]


@router.post("/documents")
def add_documents(payload: DocumentsIn) -> dict:
    # `type` isn't sent by the client: create_documents derives it from
    # each name (see parse_document_name).
    documents = [
        {"name": doc.name, "fields": [(f.label, f.value) for f in doc.fields]}
        for doc in payload.documents
    ]
    try:
        ids = create_documents(documents, payload.template)
    except InvalidDocumentName as exc:
        raise HTTPException(status_code=400, detail=f"Nom de fichier invalide : {', '.join(exc.names)}")
    return {"ids": ids}


@router.get("/documents")
def get_documents() -> dict:
    return {"documents": list_documents()}


@router.get("/documents/{document_id}")
def get_document_by_id(document_id: int) -> dict:
    document = get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return document


@router.patch("/documents/{document_id}/fields/{field_id}")
def update_document_field(document_id: int, field_id: int, payload: FieldUpdateIn) -> dict:
    field = update_field(document_id, field_id, payload.label, payload.value)
    if field is None:
        raise HTTPException(status_code=404, detail="Champ introuvable.")
    return field
