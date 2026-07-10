# api/src/docscope/services/extraction.py
#
# Orchestrates PDF text extraction (with OCR fallback for scanned pages)
# and Model 1 field parsing. Kept separate from the router: the router only
# deals with HTTP, this module deals with documents.

import io

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from PIL import Image

from docscope.services.models import model_1


def extract_pdf_text(content: bytes) -> str:
    """Extract text page by page. Falls back to OCR for pages with no
    embedded text layer (scanned PDFs)."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]

    if any(not page.strip() for page in pages):
        pages = _ocr_missing_pages(content, pages)

    return "\n".join(pages)


def _ocr_missing_pages(content: bytes, pages: list[str]) -> list[str]:
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        for i, page_text in enumerate(pages):
            if page_text.strip():
                continue
            pixmap = doc[i].get_pixmap(dpi=200)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            pages[i] = pytesseract.image_to_string(image, lang="fra+eng")
    finally:
        doc.close()
    return pages


def extract_model_1(text: str) -> dict:
    """Run Model 1 field + earnings table extraction and shape the result
    for the API response."""
    fields = [
        {"intitule": label, "valeur": value}
        for label, value in model_1.extract_fields(text)
    ]

    table, summary = model_1.extract_earnings_table(text)
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
