"""FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import agent, core
from app.core.config import get_settings
from app.core.errors import FleetPilotError
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("fleetpilot.api")

app = FastAPI(title="FleetPilot API", version="1.0.0",
              description="Agentic fleet operations copilot — grounded Q&A, insights and human-approved actions.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(FleetPilotError)
async def domain_error(_: Request, exc: FleetPilotError):
    return JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.message}})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):  # pragma: no cover - safety net
    logger.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": {"code": "internal_error", "message": "Internal error"}})


app.include_router(core.router)
app.include_router(agent.router)
