"""SQLAlchemy engine, session factory and ORM models.

Tables: ``queue_items`` (the live queue, ordered by ``position``), ``queue_history``
(executed items with their result), ``device_definitions`` (``cap:device-definition-persistence``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class PlanItemColumns:
    """The columns that describe a plan item, shared by the live queue and the history.

    A history row IS a queue item plus how it ended, so these eight columns must stay
    identical in both tables. They were written out twice until 2026-09-11; a mixin is what
    stops the two definitions drifting apart. Each table declares its own identity columns
    (``item_uid`` / ``position`` / ``id``) because those genuinely differ.
    """

    item_type: Mapped[str] = mapped_column(String(32), nullable=False, default="plan")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    args: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    kwargs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    user: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    user_group: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class QueueItemRow(PlanItemColumns, Base):
    __tablename__ = "queue_items"

    item_uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, index=True, nullable=False)


class HistoryRow(PlanItemColumns, Base):
    __tablename__ = "queue_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_uid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    exit_status: Mapped[str] = mapped_column(String(32), nullable=False)
    run_uids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    time_start: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    time_stop: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    msg: Mapped[str] = mapped_column(Text, nullable=False, default="")
    traceback: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DeviceDefinitionRow(Base):
    __tablename__ = "device_definitions"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    class_path: Mapped[str] = mapped_column(String(512), nullable=False)
    prefix: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    kwargs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class Database:
    """Owns the engine and hands out sessions. ``url`` is any SQLAlchemy URL."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._url = url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine: Engine = create_engine(url, echo=echo, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):

            @event.listens_for(self._engine, "connect")
            def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self._sessions = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)

    @property
    def url(self) -> str:
        return self._url

    @property
    def engine(self) -> Engine:
        return self._engine

    def create_all(self) -> None:
        Base.metadata.create_all(self._engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A transaction: commits on success, rolls back on error."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self._engine.dispose()
