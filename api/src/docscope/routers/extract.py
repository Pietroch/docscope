# api/src/docscope/routers/extract.py
#
# HTTP layer only: read the upload, delegate to the extraction service,
# turn failures into proper HTTP errors. Business logic lives in
# docscope.services.extraction.

from fastapi import APIRouter, HTTPException, UploadFile

from docscope.services.extraction import extract_model_1, extract_pdf_text

router = APIRouter()


@router.post("/extract")
async def extract_text(file: UploadFile) -> dict:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF.")

    # In-memory only: no write to disk, no db, no persistence.
    content = await file.read()

    try:
        text = extract_pdf_text(content)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the PDF file.")

    return extract_model_1(text)
