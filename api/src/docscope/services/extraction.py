# api/src/docscope/services/extraction.py
#
# Orchestrates PDF text extraction (with OCR fallback for scanned pages)
# and per-model field parsing. Kept separate from the router: the router
# only deals with HTTP, this module deals with documents.

import io

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from PIL import Image

from docscope.services.models import apside, delvaux, mosica, ucm


def extract_pdf_text(content: bytes) -> str:
    """Extract text page by page. Falls back to OCR for pages with no
    embedded text layer (scanned PDFs), and for the whole document when
    pdfplumber can't even read its page count - some scanners emit a
    malformed xref table that pdfplumber gives up on (0 pages) even
    though the PDF renders fine; PyMuPDF (used below for OCR) is more
    forgiving and repairs it, so it - not pdfplumber - is the source of
    truth for how many pages actually exist."""
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]

        if len(pages) != doc.page_count:
            pages = [""] * doc.page_count

        if any(not page.strip() for page in pages):
            pages = _ocr_missing_pages(doc, pages)
    finally:
        doc.close()

    return "\n".join(pages)


def _ocr_missing_pages(doc, pages: list[str]) -> list[str]:
    for i, page_text in enumerate(pages):
        if page_text.strip():
            continue

        pixmap = doc[i].get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        pages[i] = pytesseract.image_to_string(image, lang="fra+eng")
    return pages


def extract_ucm(text: str) -> dict:
    """Run UCM field + earnings table extraction and shape the result
    for the API response."""
    fields = [
        {"intitule": label, "valeur": value}
        for label, value in ucm.extract_fields(text)
    ]

    table, summary = ucm.extract_earnings_table(text)
    table_json = [
        {"ligne": code, "colonne": column, "valeur": value}
        for code, column, value in table
    ]
    summary_json = [
        {"intitule": label, "valeur": value} for _, label, value in summary
    ]

    return {
        "text": text,
        "champs": fields,
        "tableau": table_json,
        "synthese": summary_json,
    }


def extract_apside(text: str) -> dict:
    """Run Apside field + earnings table extraction and shape the result
    for the API response."""
    fields = [
        {"intitule": label, "valeur": value}
        for label, value in apside.extract_fields(text)
    ]

    table, summary = apside.extract_earnings_table(text)
    table_json = [
        {"ligne": code, "colonne": column, "valeur": value}
        for code, column, value in table
    ]
    summary_json = [
        {"intitule": label, "valeur": value} for _, label, value in summary
    ]

    return {
        "text": text,
        "champs": fields,
        "tableau": table_json,
        "synthese": summary_json,
    }


def extract_mosica(text: str) -> dict:
    """Run Mosica field + earnings table extraction and shape the result
    for the API response."""
    fields = [
        {"intitule": label, "valeur": value}
        for label, value in mosica.extract_fields(text)
    ]

    table, summary = mosica.extract_earnings_table(text)
    table_json = [
        {"ligne": code, "colonne": column, "valeur": value}
        for code, column, value in table
    ]
    summary_json = [
        {"intitule": label, "valeur": value} for _, label, value in summary
    ]

    return {
        "text": text,
        "champs": fields,
        "tableau": table_json,
        "synthese": summary_json,
    }


def extract_delvaux(content: bytes) -> dict:
    """Run Delvaux field + rubriques table extraction (word-coordinate
    based, not plain text) and shape the result for the API response.
    No OCR fallback: the model's own PDFs always carry a text layer."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        fields = [
            {"intitule": label, "valeur": value}
            for label, value in delvaux.extract_fields(pdf.pages[0])
        ]
        table = delvaux.extract_earnings_table(pdf)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    table_json = [
        {"ligne": code, "colonne": column, "valeur": value}
        for code, column, value in table
    ]

    return {
        "text": text,
        "champs": fields,
        "tableau": table_json,
        "synthese": [],
    }
