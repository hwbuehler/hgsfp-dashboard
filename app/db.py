"""Database engine and session handling.

SQLite is run in WAL mode so that participants submitting responses concurrently
do not block each other.  Readers never block the single writer, which is what
matters for a survey that a few hundred people fill in at the same time.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Starlette's default threadpool size. Sync endpoints — every dashboard route,
# and the worker half of a submission — run there, so it is also the ceiling on
# how many requests can want a connection simultaneously.
THREADPOOL_LIMIT = 40


def _build_engine() -> Engine:
    settings.database_file.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        settings.database_url,
        # FastAPI runs sync endpoints in a threadpool, so a connection may be
        # handed to a different thread than the one that created it.
        connect_args={"check_same_thread": False},
        # Sized against Starlette's threadpool rather than against a network
        # database: every one of those threads can be inside a request at once,
        # and a SQLite connection is a file handle, not a server session. The
        # default of five made a lecture hall queue on the pool before it ever
        # reached SQLite.
        pool_size=THREADPOOL_LIMIT,
        max_overflow=THREADPOOL_LIMIT,
        # Nothing here holds a connection for seconds, so waiting this long means
        # something is wrong; failing is more useful than hanging for 30s.
        pool_timeout=10,
        future=True,
    )


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # Wait instead of raising "database is locked" when the writer is busy.
    # An outer bound for the pathological case (a checkpoint stalling behind a
    # slow disk), not an expected wait: writers normally queue for microseconds.
    # Raised from 5s because a submission that waits has still been recorded,
    # while one that raises has lost somebody's answers for good. Submissions
    # are handled off the event loop, so a wait costs one worker thread, not the
    # whole server.
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
