"""
backend/logging_config.py
Structured logging — v2.3

Levert:
    setup_logging()              Configureer app-wide logging
    RequestLoggingMiddleware     Log elke request + duration
    get_logger(name)             Geeft geconfigureerde logger terug

Log events (gestructureerd, zoekopdrachten mogelijk):
    REQUEST  method path status duration_ms
    CACHE    action ticker age_seconds confidence
    SCORE    ticker decision momentum_score skip_score
    FALLBACK ticker reason confidence
    ERROR    ticker error

Niveaus:
    DEBUG    Cache hits, interne details (standaard uit)
    INFO     Requests, scores, fallbacks
    WARNING  Rate limits, fallbacks, stale data
    ERROR    Onverwachte fouten

Configureerbaar via:
    LOG_LEVEL env var (INFO standaard)
    LOG_FORMAT env var: "text" (standaard) of "json"
"""

import logging
import time
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── CONFIGURATIE ──────────────────────────────────────────────────────────────

_LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO").upper()
_LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

_TEXT_FORMAT = (
    "%(asctime)s  %(levelname)-7s  %(name)-20s  %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging() -> None:
    """
    Configureert root logger eenmalig.
    Veilig om meerdere keren aan te roepen.
    """
    global _configured
    if _configured:
        return

    level = getattr(logging, _LOG_LEVEL, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt=_TEXT_FORMAT,
        datefmt=_DATE_FORMAT,
    ))

    root = logging.getLogger()
    root.setLevel(level)

    # Verwijder bestaande handlers om dubbele logs te voorkomen
    root.handlers.clear()
    root.addHandler(handler)

    # Beperk noisy 3rd-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Geeft geconfigureerde logger terug voor een module."""
    setup_logging()
    return logging.getLogger(name)


# ── REQUEST LOGGING MIDDLEWARE ────────────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logt elke HTTP request met method, path, status en duration.
    Bevat geen PII — alleen endpoint structuur.
    """

    def __init__(self, app, logger_name: str = "api.requests"):
        super().__init__(app)
        self._logger = get_logger(logger_name)

    async def dispatch(self, request: Request, call_next) -> Response:
        start    = time.monotonic()
        path     = request.url.path
        query    = f"?{request.url.query}" if request.url.query else ""
        method   = request.method

        try:
            response = await call_next(request)
            duration = (time.monotonic() - start) * 1000
            status   = response.status_code

            level = logging.WARNING if status >= 400 else logging.INFO
            self._logger.log(
                level,
                f"{method} {path}{query} → {status} ({duration:.1f}ms)",
            )
            return response

        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            self._logger.error(
                f"{method} {path}{query} → 500 ({duration:.1f}ms) — {exc}"
            )
            raise


# ── STRUCTURED LOG HELPERS ────────────────────────────────────────────────────

def log_cache_event(
    logger:     logging.Logger,
    action:     str,           # "hit", "miss", "store", "fallback", "invalidate"
    ticker:     str,
    age_seconds: Optional[float] = None,
    confidence:  Optional[str]   = None,
    ttl:         Optional[int]   = None,
) -> None:
    """Log een cache event met gestructureerde velden."""
    parts = [f"CACHE:{action.upper()} ticker={ticker}"]
    if age_seconds is not None:
        parts.append(f"age={age_seconds:.0f}s")
    if confidence:
        parts.append(f"conf={confidence}")
    if ttl is not None:
        parts.append(f"ttl={ttl}s")
    logger.debug("  ".join(parts))


def log_score_event(
    logger:         logging.Logger,
    ticker:         str,
    decision:       str,
    momentum_score: float,
    skip_score:     int,
    confidence:     str,
    cache_hit:      bool,
) -> None:
    """Log een scoring event."""
    logger.info(
        f"SCORE  ticker={ticker}  decision={decision}  "
        f"momentum={momentum_score:.1f}  skip={skip_score}  "
        f"conf={confidence}  cache={'hit' if cache_hit else 'miss'}"
    )


def log_fallback_event(
    logger:     logging.Logger,
    ticker:     str,
    reason:     str,
    confidence: str,
) -> None:
    """Log wanneer cache fallback gebruikt wordt."""
    logger.warning(
        f"FALLBACK  ticker={ticker}  reason={reason}  conf={confidence}"
    )
