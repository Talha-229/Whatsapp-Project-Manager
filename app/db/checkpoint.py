"""Postgres checkpointer pool for LangGraph (Supabase / DATABASE_URL)."""

import logging
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def init_checkpoint_pool(database_url: str) -> PostgresSaver:
    """Create connection pool + PostgresSaver and run setup() once."""
    global _pool, _saver
    if _saver is not None:
        return _saver
    if not database_url.strip():
        raise ValueError("DATABASE_URL is required for Postgres checkpointer")
    _pool = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=10,
        # None = disable prepared statements (required for Supabase pooler / PgBouncer)
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        # Without check, idle connections killed by the pooler are still handed out → OperationalError.
        check=ConnectionPool.check_connection,
        # Recycle before server-side idle close (session pooler / PgBouncer often < 10m).
        max_idle=120.0,
        max_lifetime=900.0,
        open=True,
    )
    _saver = PostgresSaver(_pool)
    _saver.setup()
    logger.info("Postgres checkpointer initialized and tables migrated")
    return _saver


def get_checkpointer() -> PostgresSaver:
    if _saver is None:
        raise RuntimeError("Checkpointer not initialized; call init_checkpoint_pool at startup")
    return _saver


def shutdown_checkpoint_pool() -> None:
    global _pool, _saver
    if _pool is not None:
        try:
            _pool.close()
        except Exception as e:
            logger.warning("Pool close: %s", e)
        _pool = None
    _saver = None
