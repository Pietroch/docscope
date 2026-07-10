# api/src/docscope/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from docscope.routers import extract

app = FastAPI(title="docscope")
app.include_router(extract.router)

# Client is a separate static app in dev - CORS handled here, not client-side
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
