# api/src/docscope/routers/extract.py
#
# HTTP layer only: read the upload, delegate to the extraction service,
# turn failures into proper HTTP errors. Business logic lives in
# docscope.services.extraction.

from fastapi import APIRouter, HTTPException, UploadFile

from docscope.core.logging import get_logger
from docscope.services.extraction import (
    extract_apside,
    extract_delvaux,
    extract_mosica,
    extract_pdf_text,
    extract_ucm,
)

router = APIRouter()
logger = get_logger(__name__)

# UCM and Apside need flat text first (with OCR fallback); Delvaux reads
# the PDF itself (word coordinates), so it takes the raw bytes directly.
EXTRACTORS = {
    "ucm": lambda content: extract_ucm(extract_pdf_text(content)),
    "delvaux": extract_delvaux,
    "apside": lambda content: extract_apside(extract_pdf_text(content)),
    "mosica": lambda content: extract_mosica(extract_pdf_text(content)),
}


@router.post("/extract")
async def extract_text(file: UploadFile, model: str = "ucm") -> dict:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Le fichier doit être un PDF.")
    if model not in EXTRACTORS:
        raise HTTPException(status_code=400, detail="Modèle inconnu.")

    # In-memory only: no write to disk, no db, no persistence.
    content = await file.read()

    try:
        return EXTRACTORS[model](content)
    except Exception:
        logger.exception("Extraction failed - model=%s file=%s", model, file.filename)
        raise HTTPException(status_code=400, detail="Impossible de lire le fichier PDF.")
