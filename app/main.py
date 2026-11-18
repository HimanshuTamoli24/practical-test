"""FastAPI entrypoint for the AI-powered website link analysis API."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.schemas import ErrorResponse, LinkAnalysisRequest, LinkAnalysisResponse
from app.services.analysis_service import analyze_page
from app.services.llm_service import LLMError
from app.services.website_extractor import WebsiteFetchError, fetch_page
from app.utils.validators import InvalidURLError, validate_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("link-analysis")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="AI-Powered Website Link Analysis",
    description=(
        "Analyzes a public website with Groq and returns competitor, search query, "
        "SEO, service and target-audience insights."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []) if part != "body")
        messages.append(f"{location or 'body'}: {error.get('msg', 'invalid value')}")
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": "Invalid request body. " + "; ".join(messages)},
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the browser UI."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "groq_configured": settings.groq_configured,
        "model": settings.groq_model,
        "fallback_model": settings.groq_fallback_model,
    }


@app.post(
    "/link-analysis",
    response_model=LinkAnalysisResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or non-public URL"},
        422: {"model": ErrorResponse, "description": "Malformed request body"},
        502: {"model": ErrorResponse, "description": "Website fetch or Groq failure"},
        503: {"model": ErrorResponse, "description": "Groq API key not configured"},
    },
    tags=["analysis"],
    summary="Analyze a website with Groq",
)
async def link_analysis(payload: LinkAnalysisRequest) -> LinkAnalysisResponse:
    # 1. Validate URL syntax and make sure the host is public (SSRF protection).
    try:
        url = validate_url(payload.url)
    except InvalidURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 2. Fetch and extract the page.
    try:
        page = await fetch_page(url)
    except InvalidURLError as exc:  # redirect to an internal host
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except WebsiteFetchError as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 3. Ask Groq for structured analysis.
    try:
        result = await analyze_page(page)
    except LLMError as exc:
        message = str(exc)
        status = 503 if "not configured" in message else 502
        logger.error("LLM analysis failed for %s: %s", url, message)
        raise HTTPException(status_code=status, detail=message) from exc

    logger.info(
        "Analysed %s with %s (%s in / %s out tokens, %s ms)",
        url,
        result.meta.model,
        result.meta.input_tokens,
        result.meta.output_tokens,
        result.meta.response_ms,
    )
    return result


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=4000, reload=True)
