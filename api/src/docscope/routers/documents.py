# api/src/docscope/routers/documents.py
#
# HTTP layer only: validate input, delegate to the documents service.

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from docscope.services.documents import (
    DocumentLocked,
    InvalidDocumentName,
    NoPayslipView,
    create_documents,
    create_field,
    get_document,
    get_payslip,
    list_documents,
    set_document_validated,
    update_field,
)

router = APIRouter()

DOCUMENT_LOCKED_DETAIL = "Document validé : décochez la validation pour le modifier."


class FieldIn(BaseModel):
    label: str
    value: str | None = None


class FieldUpdateIn(BaseModel):
    label: str
    value: str | None = None


class ValidatedIn(BaseModel):
    validated: bool


class DocumentIn(BaseModel):
    name: str
    fields: list[FieldIn] = []


class DocumentsIn(BaseModel):
    template: Literal["ucm", "delvaux", "apside", "mosica", "ricoh", "sitti"]
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


@router.get("/documents/{document_id}/payslip")
def get_document_payslip(document_id: int) -> dict:
    try:
        payslip = get_payslip(document_id)
    except NoPayslipView as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Aucune vue graphique pour ce modèle ({exc.template}).",
        )
    if payslip is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return payslip


@router.patch("/documents/{document_id}/fields/{field_id}")
def update_document_field(document_id: int, field_id: int, payload: FieldUpdateIn) -> dict:
    try:
        field = update_field(document_id, field_id, payload.label, payload.value)
    except DocumentLocked:
        raise HTTPException(status_code=409, detail=DOCUMENT_LOCKED_DETAIL)
    if field is None:
        raise HTTPException(status_code=404, detail="Champ introuvable.")
    return field


@router.post("/documents/{document_id}/fields")
def add_document_field(document_id: int, payload: FieldIn) -> dict:
    try:
        field = create_field(document_id, payload.label, payload.value)
    except DocumentLocked:
        raise HTTPException(status_code=409, detail=DOCUMENT_LOCKED_DETAIL)
    if field is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return field


@router.patch("/documents/{document_id}/validated")
def update_document_validated(document_id: int, payload: ValidatedIn) -> dict:
    document = set_document_validated(document_id, payload.validated)
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return document