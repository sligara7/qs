"""SQLAlchemy implementation of :class:`qs.queue.repository.QueueRepository`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select, update

from qs.persistence.database import Database, HistoryRow, QueueItemRow
from qs.queue.models import HistoryEntry, ItemState, QueueItem


def _row_to_item(row: QueueItemRow | HistoryRow) -> QueueItem:
    return QueueItem(
        name=row.name,
        item_type=row.item_type,
        args=list(row.args or []),
        kwargs=dict(row.kwargs or {}),
        meta=dict(row.meta or {}),
        item_uid=row.item_uid,
        user=row.user,
        user_group=row.user_group,
        created_at=row.created_at,
    )


class SqlQueueRepository:
    """Ordered queue and history in SQL. Positions are dense integers, renumbered on change."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- queue ----

    def list_items(self) -> Sequence[QueueItem]:
        with self._db.session() as s:
            rows = s.scalars(select(QueueItemRow).order_by(QueueItemRow.position)).all()
            return [_row_to_item(r) for r in rows]

    def get_item(self, item_uid: str) -> QueueItem | None:
        with self._db.session() as s:
            row = s.get(QueueItemRow, item_uid)
            return None if row is None else _row_to_item(row)

    def insert(self, item: QueueItem, index: int | None = None) -> None:
        with self._db.session() as s:
            if s.get(QueueItemRow, item.item_uid) is not None:
                raise ValueError(f"Duplicate item uid {item.item_uid!r}")
            count = s.scalar(select(func.count()).select_from(QueueItemRow)) or 0
            pos = count if index is None else max(0, min(index, count))
            s.execute(
                update(QueueItemRow)
                .where(QueueItemRow.position >= pos)
                .values(position=QueueItemRow.position + 1)
            )
            s.add(
                QueueItemRow(
                    item_uid=item.item_uid,
                    position=pos,
                    item_type=item.item_type,
                    name=item.name,
                    args=list(item.args),
                    kwargs=dict(item.kwargs),
                    meta=dict(item.meta),
                    user=item.user,
                    user_group=item.user_group,
                    created_at=item.created_at,
                )
            )

    def replace(self, item: QueueItem) -> None:
        with self._db.session() as s:
            row = s.get(QueueItemRow, item.item_uid)
            if row is None:
                raise KeyError(item.item_uid)
            row.item_type = item.item_type
            row.name = item.name
            row.args = list(item.args)
            row.kwargs = dict(item.kwargs)
            row.meta = dict(item.meta)
            row.user = item.user
            row.user_group = item.user_group

    def remove(self, item_uid: str) -> QueueItem:
        with self._db.session() as s:
            row = s.get(QueueItemRow, item_uid)
            if row is None:
                raise KeyError(item_uid)
            item = _row_to_item(row)
            pos = row.position
            s.delete(row)
            s.flush()
            s.execute(
                update(QueueItemRow)
                .where(QueueItemRow.position > pos)
                .values(position=QueueItemRow.position - 1)
            )
            return item

    def move(self, item_uid: str, index: int) -> None:
        item = self.remove(item_uid)
        self.insert(item, index)

    def clear(self) -> int:
        with self._db.session() as s:
            result = s.execute(delete(QueueItemRow))
            return int(result.rowcount or 0)

    def pop_front(self) -> QueueItem | None:
        with self._db.session() as s:
            row = s.scalars(select(QueueItemRow).order_by(QueueItemRow.position).limit(1)).first()
            if row is None:
                return None
            item = _row_to_item(row)
            s.delete(row)
            s.flush()
            s.execute(update(QueueItemRow).values(position=QueueItemRow.position - 1))
            return item

    # ---- history ----

    def append_history(self, entry: HistoryEntry) -> None:
        it = entry.item
        with self._db.session() as s:
            s.add(
                HistoryRow(
                    item_uid=it.item_uid,
                    item_type=it.item_type,
                    name=it.name,
                    args=list(it.args),
                    kwargs=dict(it.kwargs),
                    meta=dict(it.meta),
                    user=it.user,
                    user_group=it.user_group,
                    created_at=it.created_at,
                    state=str(entry.state),
                    exit_status=entry.exit_status,
                    run_uids=list(entry.run_uids),
                    time_start=entry.time_start,
                    time_stop=entry.time_stop,
                    msg=entry.msg,
                    traceback=entry.traceback,
                )
            )

    def list_history(self) -> Sequence[HistoryEntry]:
        with self._db.session() as s:
            rows = s.scalars(select(HistoryRow).order_by(HistoryRow.id)).all()
            return [
                HistoryEntry(
                    item=_row_to_item(r),
                    state=ItemState(r.state),
                    exit_status=r.exit_status,
                    run_uids=tuple(r.run_uids or []),
                    time_start=r.time_start,
                    time_stop=r.time_stop,
                    msg=r.msg,
                    traceback=r.traceback,
                )
                for r in rows
            ]

    def clear_history(self) -> int:
        with self._db.session() as s:
            result = s.execute(delete(HistoryRow))
            return int(result.rowcount or 0)
