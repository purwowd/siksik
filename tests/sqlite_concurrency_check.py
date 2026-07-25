from __future__ import annotations

import asyncio
import sys
import tempfile
from contextlib import suppress
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.db import Database


async def run_check() -> None:
    with tempfile.TemporaryDirectory(prefix="siksik-db-check-") as directory:
        database = Database(Path(directory) / "concurrency.db")
        await database.connect()
        try:
            await database.execute(
                "CREATE TABLE concurrency_probe (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
            )

            async def transaction_writer(index: int) -> None:
                async with database.transaction() as connection:
                    await connection.execute(
                        "INSERT INTO concurrency_probe (id, value) VALUES (?, ?)",
                        (index, index),
                    )
                    await asyncio.sleep(0)

            async def committed_writer(index: int) -> None:
                await database.execute(
                    "INSERT INTO concurrency_probe (id, value) VALUES (?, ?)",
                    (index, index),
                )

            async def observer() -> None:
                for _ in range(20):
                    await database.fetchone("SELECT COUNT(*) AS total FROM concurrency_probe")
                    await asyncio.sleep(0)

            await asyncio.gather(
                *(transaction_writer(index) for index in range(1, 21)),
                *(committed_writer(index) for index in range(21, 41)),
                observer(),
            )
            transaction_started = asyncio.Event()

            async def cancelled_writer() -> None:
                async with database.transaction() as connection:
                    await connection.execute(
                        "INSERT INTO concurrency_probe (id, value) VALUES (?, ?)",
                        (99, 99),
                    )
                    transaction_started.set()
                    await asyncio.Event().wait()

            cancelled_task = asyncio.create_task(cancelled_writer())
            await transaction_started.wait()
            cancelled_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancelled_task
            await database.execute(
                "INSERT INTO concurrency_probe (id, value) VALUES (?, ?)",
                (41, 41),
            )
            row = await database.fetchone(
                "SELECT COUNT(*) AS total, SUM(value) AS checksum FROM concurrency_probe"
            )
            rolled_back = await database.fetchone(
                "SELECT COUNT(*) AS total FROM concurrency_probe WHERE id = 99"
            )
            if row is None or int(row["total"]) != 41 or int(row["checksum"]) != 861:
                raise RuntimeError("SQLite concurrency result is inconsistent")
            if rolled_back is None or int(rolled_back["total"]) != 0:
                raise RuntimeError("cancelled SQLite transaction was not rolled back")
        finally:
            await database.close()


if __name__ == "__main__":
    asyncio.run(run_check())
    print("sqlite_concurrency_check: ok")
