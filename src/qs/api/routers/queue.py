"""Queue and history groups, with bluesky-queueserver's request and response shapes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from qs.api.auth import Principal
from qs.api.deps import Services, get_principal, get_services, require_scope
from qs.api.payload import read_payload
from qs.api.responses import fail, ok, unsupported
from qs.queue.models import QueueItem
from qs.queue.service import QueueError
from qs.sequencer import SequencerError

router = APIRouter(tags=["queue"])


def _item_from_payload(payload: dict[str, Any], principal: Principal) -> QueueItem:
    raw = payload.get("item")
    if not isinstance(raw, dict) or "name" not in raw:
        raise QueueError("Payload must contain 'item' with at least a 'name'")
    return QueueItem.from_dict(raw, user=principal.name, user_group=principal.user_group)


def _position_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "pos": payload.get("pos"),
        "before_uid": payload.get("before_uid"),
        "after_uid": payload.get("after_uid"),
    }


@router.get("/queue/get", dependencies=[Depends(require_scope("read:queue"))])
async def queue_get(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        items=[i.to_dict() for i in services.queue.items()],
        running_item=services.status.running_item(),
        plan_queue_uid=services.status.snapshot()["plan_queue_uid"],
    )


@router.get("/queue/item/get", dependencies=[Depends(require_scope("read:queue"))])
async def item_get(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        if payload.get("uid"):
            item = services.queue.get(str(payload["uid"]))
        else:
            items = services.queue.items()
            pos = payload.get("pos", "back")
            if not items:
                return fail("Queue is empty", item={})
            index = {"front": 0, "back": len(items) - 1}.get(pos, pos)
            item = items[int(index)]
        return ok("", item=item.to_dict())
    except (QueueError, IndexError, ValueError, TypeError) as exc:
        return fail(str(exc), item={})


@router.get("/queue/item/{item_uid}", dependencies=[Depends(require_scope("read:queue"))])
async def item_get_by_path(item_uid: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    """Path-style alias used by finch's hand-rolled client (``GET /queue/item/<uid>``); not in
    bluesky-httpserver's spec but cheap to honour (``req:finch-client-compat``)."""
    try:
        return ok("", item=services.queue.get(item_uid).to_dict())
    except QueueError as exc:
        return fail(str(exc), item={})


@router.post("/queue/item/add", dependencies=[Depends(require_scope("write:queue"))])
async def item_add(
    request: Request,
    services: Services = Depends(get_services),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        item = _item_from_payload(payload, principal)
        stored, _ = services.queue.add(item, **_position_kwargs(payload))
        return ok("", qsize=len(services.queue), item=stored.to_dict())
    except QueueError as exc:
        return fail(str(exc), qsize=len(services.queue), item=payload.get("item", {}))


@router.post("/queue/item/add/batch", dependencies=[Depends(require_scope("write:queue"))])
async def item_add_batch(
    request: Request,
    services: Services = Depends(get_services),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    payload = await read_payload(request)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return fail("Payload must contain a list 'items'", qsize=len(services.queue), items=[], results=[])
    items: list[QueueItem] = []
    results: list[dict[str, Any]] = []
    all_ok = True
    for raw in raw_items:
        try:
            item = QueueItem.from_dict(raw, user=principal.name, user_group=principal.user_group)
            services.queue.validate(item)
            items.append(item)
            results.append(ok(""))
        except (QueueError, KeyError, TypeError) as exc:
            all_ok = False
            results.append(fail(str(exc)))
    if not all_ok:
        return fail(
            "Some items are invalid; nothing was added",
            qsize=len(services.queue),
            items=raw_items,
            results=results,
        )
    added = services.queue.add_batch(items, **_position_kwargs(payload))
    return ok("", qsize=len(services.queue), items=[i.to_dict() for i, _ in added], results=results)


@router.post("/queue/item/update", dependencies=[Depends(require_scope("write:queue"))])
async def item_update(
    request: Request,
    services: Services = Depends(get_services),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        item = _item_from_payload(payload, principal)
        updated = services.queue.update(item, replace_uid=bool(payload.get("replace", False)))
        return ok("", qsize=len(services.queue), item=updated.to_dict())
    except QueueError as exc:
        return fail(str(exc), qsize=len(services.queue), item=payload.get("item", {}))


@router.post("/queue/item/move", dependencies=[Depends(require_scope("write:queue"))])
async def item_move(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        uid = payload.get("uid")
        if not uid and "pos" in payload:
            items = services.queue.items()
            uid = items[int(payload["pos"])].item_uid
        if not uid:
            raise QueueError("Payload must contain 'uid' (or 'pos') of the item to move")
        kwargs = {
            "pos": payload.get("pos_dest"),
            "before_uid": payload.get("before_uid"),
            "after_uid": payload.get("after_uid"),
        }
        services.queue.move(str(uid), **kwargs)
        return ok("", qsize=len(services.queue), item=services.queue.get(str(uid)).to_dict())
    except (QueueError, IndexError, ValueError, TypeError) as exc:
        return fail(str(exc), qsize=len(services.queue), item={})


@router.post("/queue/item/move/batch", dependencies=[Depends(require_scope("write:queue"))])
async def item_move_batch(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    uids = payload.get("uids")
    if not isinstance(uids, list) or not uids:
        return fail("Payload must contain a non-empty list 'uids'", qsize=len(services.queue), items=[])
    try:
        anchor_kwargs = {
            "pos": payload.get("pos_dest"),
            "before_uid": payload.get("before_uid"),
            "after_uid": payload.get("after_uid"),
        }
        # Move the first to the anchor, then the rest one after another, preserving order.
        services.queue.move(str(uids[0]), **anchor_kwargs)
        for prev, uid in zip(uids, uids[1:], strict=False):
            services.queue.move(str(uid), after_uid=str(prev))
        moved = [services.queue.get(str(u)).to_dict() for u in uids]
        return ok("", qsize=len(services.queue), items=moved)
    except QueueError as exc:
        return fail(str(exc), qsize=len(services.queue), items=[])


@router.post("/queue/item/remove", dependencies=[Depends(require_scope("write:queue"))])
async def item_remove(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        uid = payload.get("uid")
        if not uid:
            items = services.queue.items()
            if not items:
                raise QueueError("Queue is empty")
            pos = payload.get("pos", "back")
            index = {"front": 0, "back": len(items) - 1}.get(pos, pos)
            uid = items[int(index)].item_uid
        removed = services.queue.remove(str(uid))
        return ok("", qsize=len(services.queue), item=removed.to_dict())
    except (QueueError, IndexError, ValueError, TypeError) as exc:
        return fail(str(exc), qsize=len(services.queue), item={})


@router.post("/queue/item/remove/batch", dependencies=[Depends(require_scope("write:queue"))])
async def item_remove_batch(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    uids = payload.get("uids")
    if not isinstance(uids, list):
        return fail("Payload must contain a list 'uids'", qsize=len(services.queue), items=[])
    try:
        removed = services.queue.remove_batch(
            [str(u) for u in uids], ignore_missing=bool(payload.get("ignore_missing", True))
        )
        return ok("", qsize=len(services.queue), items=[i.to_dict() for i in removed])
    except QueueError as exc:
        return fail(str(exc), qsize=len(services.queue), items=[])


@router.post("/queue/item/execute", dependencies=[Depends(require_scope("write:execute"))])
async def item_execute(
    request: Request,
    services: Services = Depends(get_services),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    payload = await read_payload(request)
    try:
        item = _item_from_payload(payload, principal)
        services.queue.validate(item)
        services.sequencer.execute_now(item)
        return ok("", qsize=len(services.queue), item=item.to_dict())
    except (QueueError, SequencerError) as exc:
        return fail(str(exc), qsize=len(services.queue), item=payload.get("item", {}))


@router.post("/queue/clear", dependencies=[Depends(require_scope("write:queue"))])
async def queue_clear(services: Services = Depends(get_services)) -> dict[str, Any]:
    services.queue.clear()
    return ok("Plan queue is now empty.")


@router.post("/queue/start", dependencies=[Depends(require_scope("write:execute"))])
async def queue_start(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        services.sequencer.queue_start()
        return ok("")
    except SequencerError as exc:
        return fail(str(exc))


@router.post("/queue/stop", dependencies=[Depends(require_scope("write:execute"))])
async def queue_stop(services: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        services.sequencer.queue_stop()
        return ok("")
    except SequencerError as exc:
        return fail(str(exc))


@router.post("/queue/stop/cancel", dependencies=[Depends(require_scope("write:execute"))])
async def queue_stop_cancel(services: Services = Depends(get_services)) -> dict[str, Any]:
    services.sequencer.queue_stop_cancel()
    return ok("")


@router.post("/queue/autostart", dependencies=[Depends(require_scope("write:execute"))])
async def queue_autostart(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    if "enable" not in payload:
        return fail("Payload must contain 'enable'")
    services.sequencer.set_autostart(bool(payload["enable"]))
    return ok("")


@router.post("/queue/mode/set", dependencies=[Depends(require_scope("write:queue"))])
async def queue_mode_set(request: Request, services: Services = Depends(get_services)) -> dict[str, Any]:
    payload = await read_payload(request)
    mode = payload.get("mode")
    if not isinstance(mode, dict):
        return fail("Payload must contain a dict 'mode'")
    if mode.get("ignore_failures"):
        return fail(
            "ignore_failures is not supported: this service stops the queue on failure and waits for a human"
        )
    if "loop" in mode:
        services.sequencer.set_loop_mode(bool(mode["loop"]))
    return ok("")


@router.post("/queue/upload/spreadsheet", dependencies=[Depends(require_scope("write:queue"))])
async def queue_upload_spreadsheet() -> dict[str, Any]:
    return unsupported("Spreadsheet upload", "no spreadsheet processing is configured")


# ---- history ----


@router.get("/history/get", dependencies=[Depends(require_scope("read:history"))])
async def history_get(services: Services = Depends(get_services)) -> dict[str, Any]:
    return ok(
        "",
        items=[h.to_dict() for h in services.queue.history()],
        plan_history_uid=services.status.snapshot()["plan_history_uid"],
    )


@router.post("/history/clear", dependencies=[Depends(require_scope("write:history"))])
async def history_clear(services: Services = Depends(get_services)) -> dict[str, Any]:
    services.queue.clear_history()
    return ok("")
