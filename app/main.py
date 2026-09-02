"""HTTP wiring.

One endpoint, GET /tools/convert. Every parameter arrives as a string and is
parsed by `service`, rather than by FastAPI's coercion, so that every failure
comes back in the {"error", "message"} shape the brief specifies instead of
FastAPI's own 422 body.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, service
from .errors import ToolError, codes
from .upstream import Upstream

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("fx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.UPSTREAM_TIMEOUT_SECONDS),
        headers={"user-agent": "fx-convert-tool/1.0"},
    )
    app.state.upstream = Upstream(client)
    logger.info("upstream base is %s%s", config.UPSTREAM_BASE, config.UPSTREAM_PREFIX)
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(
    title="fx-convert",
    version="1.0",
    summary="Convert an amount between currencies at an ECB reference rate.",
    lifespan=lifespan,
)


def get_upstream(request: Request) -> Upstream:
    return request.app.state.upstream


@app.get("/tools/convert")
async def convert(
    amount: str | None = Query(
        None, description="How much to convert. Must be greater than zero."
    ),
    from_: str | None = Query(None, alias="from", description="Currency to convert from, e.g. EUR."),
    to: str | None = Query(None, description="Currency to convert to, e.g. TRY."),
    date_: str | None = Query(
        None,
        alias="date",
        description="The day to price it on, YYYY-MM-DD. Defaults to today (UTC).",
    ),
    upstream: Upstream = Depends(get_upstream),
):
    return await service.convert(
        raw_amount=amount,
        raw_from=from_,
        raw_to=to,
        raw_date=date_,
        upstream=upstream,
    )


@app.exception_handler(ToolError)
async def handle_tool_error(request: Request, exc: ToolError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.as_body())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # A safety net: parsing lives in `service`, so reaching here means a
    # parameter was malformed in a way FastAPI rejected first.
    return JSONResponse(
        status_code=400,
        content={
            "error": codes.BAD_REQUEST,
            "message": "The request parameters could not be read. Expected "
            "amount, from, to and an optional date=YYYY-MM-DD.",
        },
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mapping = {404: codes.NOT_FOUND, 405: codes.METHOD_NOT_ALLOWED}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": mapping.get(exc.status_code, codes.BAD_REQUEST),
            "message": f"{exc.detail}. The only endpoint is GET /tools/convert.",
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error serving %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": codes.INTERNAL_ERROR,
            "message": "This service failed in a way it does not recognise, so it "
            "returned no rate rather than an unverified one.",
        },
    )
