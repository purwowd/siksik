#!/usr/bin/env python3
"""CLI: hitung ulang rekomendasi semua sesi completed (tiga status)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow `python scripts/recompute_recommendations.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ensure_dirs
from app.core.db import db
from app.services.recommendation import recompute_all_recommendations


async def main() -> None:
    ensure_dirs()
    await db.connect()
    try:
        result = await recompute_all_recommendations()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
