from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger

from .db import PipelineDB
from .graphiti_client import GraphitiClient
from .immich_client import ImmichClient
from .ingestion import IngestionStats, run_ingestion

# ── Config from environment ──────────────────────────────────────────────────

IMMICH_BASE_URL = os.getenv("IMMICH_BASE_URL", "http://localhost:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
CONCURRENCY = int(os.getenv("PIPELINE_CONCURRENCY", "1"))


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="DEBUG",
    )
    logger.add(
        "data/pipeline.log",
        rotation="50 MB",
        retention="7 days",
        level="INFO",
    )


async def _run() -> None:
    _configure_logging()

    if not IMMICH_API_KEY:
        logger.error("IMMICH_API_KEY not set — export it or add to .env")
        sys.exit(1)

    immich = ImmichClient(IMMICH_BASE_URL, IMMICH_API_KEY)
    if not await immich.ping():
        logger.error(f"Cannot reach Immich at {IMMICH_BASE_URL}")
        sys.exit(1)
    logger.info(f"Immich reachable at {IMMICH_BASE_URL}")

    db = PipelineDB()
    await db.init()

    graphiti = GraphitiClient(
        falkordb_host=FALKORDB_HOST,
        falkordb_port=FALKORDB_PORT,
        ollama_base=OLLAMA_BASE_URL,
    )
    await graphiti.init()

    def _on_progress(stats: IngestionStats) -> None:
        done = stats.processed + stats.skipped + stats.failed
        if stats.total > 0 and done % 50 == 0:
            pct = done / stats.total * 100
            logger.info(f"Progress: {done}/{stats.total} ({pct:.1f}%)")

    stats = IngestionStats()
    stats.on_progress(_on_progress)

    await run_ingestion(
        immich=immich,
        db=db,
        graphiti=graphiti,
        concurrency=CONCURRENCY,
        stats=stats,
    )

    await graphiti.close()

    db_stats = await db.get_stats()
    logger.success(
        f"Pipeline finished — DB: {db_stats['processed']} processed, "
        f"{db_stats['pending_retries']} pending retries, "
        f"{db_stats['pending_retroactive']} retroactive updates pending"
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
