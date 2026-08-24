"""SATRIA / SIKSIK API v1 aggregator.

Handlers live in ``app.api.routes`` (union of main crawl routes + SATRIA extras).
This module is the stable include target so callers import ``app.api.v1.router``.
"""

from app.api.routes import router

__all__ = ["router"]
