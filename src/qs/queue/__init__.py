"""The Queue: domain model, repository protocol, and the queue service (``cmp:queue``).

Knows nothing about SQL specifically; Persistence implements :class:`QueueRepository`.
"""

from qs.queue.models import HistoryEntry, ItemState, QueueItem, new_item_uid
from qs.queue.repository import QueueRepository
from qs.queue.service import QueueError, QueueService

__all__ = [
    "HistoryEntry",
    "ItemState",
    "QueueError",
    "QueueItem",
    "QueueRepository",
    "QueueService",
    "new_item_uid",
]
