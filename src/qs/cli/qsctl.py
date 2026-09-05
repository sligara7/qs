"""``qsctl``: drive a running qs over HTTP from a terminal or a script.

The verbs bluesky-queueserver's ``qserver`` gave operators, over the only transport qs has.
Connection: ``--url`` / ``QS_URL`` (default http://localhost:60610) and ``--api-key`` / ``QS_API_KEY``.
Every command prints the server's JSON reply (``--json``) or a short human summary, and exits 1
when the server answers ``success: false`` or cannot be reached.

    qsctl status
    qsctl queue add count '[["det"]]' --kwargs '{"num": 3}'
    qsctl queue start | stop | stop-cancel | autostart on|off | clear | get
    qsctl queue remove <uid> | move <uid> front|back|<index>
    qsctl re pause [--immediate] | resume | abort | stop | halt
    qsctl history [--clear]
    qsctl plans | devices | experiment
    qsctl watch [--every 1.0]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:60610"


class QsClient:
    def __init__(self, url: str, api_key: str | None, timeout: float = 30.0) -> None:
        headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=url.rstrip("/") + "/api", headers=headers, timeout=timeout)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._client.get(path, params=params or None).json()

    def post(self, path: str, **body: Any) -> dict[str, Any]:
        return self._client.post(path, json=body).json()

    def close(self) -> None:
        self._client.close()


# ---- output -----------------------------------------------------------------------------


def _summarise(command: str, reply: dict[str, Any]) -> str:
    if not reply.get("success", True):
        return f"error: {reply.get('msg') or reply.get('detail') or reply}"
    if command == "status":
        qs = reply.get("qs", {})
        lines = [
            f"manager: {reply['manager_state']}   engine: {reply['re_state']}   "
            f"queue: {reply['items_in_queue']} item(s)   history: {reply['items_in_history']}",
            f"running: {reply.get('running_item_uid') or '-'}   autostart: {reply['queue_autostart_enabled']}"
            f"   stop pending: {reply['queue_stop_pending']}",
        ]
        if qs.get("experiment"):
            e = qs["experiment"]
            who = e.get("username", "-")
            lines.append(f"experiment: {e.get('data_session', '-')} cycle {e.get('cycle', '-')} ({who})")
        if qs.get("last_error"):
            lines.append(f"last error: {str(qs['last_error']).splitlines()[-1]}")
        if qs.get("database_ok") is False:
            lines.append(
                f"DATABASE UNAVAILABLE: {qs.get('database_error')} ({qs.get('pending_history')} pending)"
            )
        return "\n".join(lines)
    if command == "queue":
        items = reply.get("items", [])
        if not items:
            return "(queue empty)"
        return "\n".join(f"{i['item_uid'][:8]}  {i['name']}({_args(i)})" for i in items)
    if command == "history":
        items = reply.get("items", [])
        if not items:
            return "(history empty)"
        out = []
        for i in items:
            r = i["result"]
            msg = (r.get("msg") or "").splitlines()[-1] if r.get("msg") else ""
            runs = len(r.get("run_uids", []))
            out.append(f"{r['exit_status']:8s} {i['name']}({_args(i)})  runs={runs}  {msg}")
        return "\n".join(out)
    if command == "plans":
        return "\n".join(sorted(reply.get("plans_allowed", {})))
    if command == "devices":
        d = reply.get("devices_allowed", {})
        return "\n".join(
            f"{name:24s} {v.get('classname', '')}{' movable' if v.get('is_movable') else ''}"
            f"{' readable' if v.get('is_readable') else ''}"
            for name, v in sorted(d.items())
        )
    if command == "experiment":
        e = reply.get("qs", {}).get("experiment", {})
        return json.dumps(e, indent=1) if e else "(no experiment synced)"
    if "item" in reply and isinstance(reply["item"], dict):
        return f"ok  {reply['item'].get('item_uid', '')}  queue size {reply.get('qsize', '?')}"
    return "ok" + (f"  {reply['msg']}" if reply.get("msg") else "")


def _args(item: dict[str, Any]) -> str:
    parts = [json.dumps(a) for a in item.get("args", [])]
    parts += [f"{k}={json.dumps(v)}" for k, v in (item.get("kwargs") or {}).items()]
    return ", ".join(parts)


# ---- commands ---------------------------------------------------------------------------


def _queue(client: QsClient, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    sub = args.queue_cmd
    if sub == "get":
        return "queue", client.get("/queue/get")
    if sub == "add":
        item = {
            "item_type": "plan",
            "name": args.plan,
            "args": json.loads(args.args) if args.args else [],
            "kwargs": json.loads(args.kwargs) if args.kwargs else {},
        }
        body: dict[str, Any] = {"item": item}
        if args.pos is not None:
            body["pos"] = int(args.pos) if args.pos.lstrip("-").isdigit() else args.pos
        return "add", client.post("/queue/item/add", **body)
    if sub == "remove":
        return "remove", client.post("/queue/item/remove", uid=args.uid)
    if sub == "move":
        pos = args.pos_dest
        body: dict[str, Any] = {"uid": args.uid}
        body["pos_dest"] = int(pos) if pos.lstrip("-").isdigit() else pos
        return "move", client.post("/queue/item/move", **body)
    if sub == "clear":
        return "clear", client.post("/queue/clear")
    if sub == "start":
        return "start", client.post("/queue/start")
    if sub == "stop":
        return "stop", client.post("/queue/stop")
    if sub == "stop-cancel":
        return "stop-cancel", client.post("/queue/stop/cancel")
    if sub == "autostart":
        return "autostart", client.post("/queue/autostart", enable=args.enable == "on")
    raise SystemExit(f"unknown queue command {sub}")


def _re(client: QsClient, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    sub = args.re_cmd
    if sub == "pause":
        return "pause", client.post("/re/pause", option="immediate" if args.immediate else "deferred")
    return sub, client.post(f"/re/{sub}")


def _watch(client: QsClient, every: float) -> int:
    last = None
    try:
        while True:
            s = client.get("/status")
            line = _summarise("status", s)
            if line != last:
                print(time.strftime("%H:%M:%S"), line.replace("\n", "\n         "), flush=True)
                last = line
            time.sleep(every)
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qsctl", description="Control a running qs over HTTP.")
    p.add_argument("--url", default=os.environ.get("QS_URL", DEFAULT_URL), help="qs base URL (QS_URL)")
    p.add_argument("--api-key", default=os.environ.get("QS_API_KEY"), help="API key (QS_API_KEY)")
    p.add_argument("--json", action="store_true", help="print the server's JSON reply")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("plans")
    sub.add_parser("devices")
    sub.add_parser("experiment", help="show the synced experiment (from the profile's RE.md)")
    h = sub.add_parser("history")
    h.add_argument("--clear", action="store_true")
    w = sub.add_parser("watch", help="print status whenever it changes")
    w.add_argument("--every", type=float, default=1.0)
    q = sub.add_parser("queue").add_subparsers(dest="queue_cmd", required=True)
    q.add_parser("get")
    a = q.add_parser("add")
    a.add_argument("plan")
    a.add_argument("args", nargs="?", help="JSON list of positional arguments, e.g. '[[\"det\"]]'")
    a.add_argument("--kwargs", help="JSON object of keyword arguments")
    a.add_argument("--pos", help="front | back | index")
    r = q.add_parser("remove")
    r.add_argument("uid")
    m = q.add_parser("move")
    m.add_argument("uid")
    m.add_argument("pos_dest", help="front | back | index")
    for name in ("clear", "start", "stop", "stop-cancel"):
        q.add_parser(name)
    au = q.add_parser("autostart")
    au.add_argument("enable", choices=["on", "off"])
    re_ = sub.add_parser("re").add_subparsers(dest="re_cmd", required=True)
    pa = re_.add_parser("pause")
    pa.add_argument("--immediate", action="store_true", help="pause now instead of at the next checkpoint")
    for name in ("resume", "abort", "stop", "halt"):
        re_.add_parser(name)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = QsClient(args.url, args.api_key)
    try:
        if args.command == "watch":
            return _watch(client, args.every)
        if args.command == "status":
            kind, reply = "status", client.get("/status")
        elif args.command == "plans":
            kind, reply = "plans", client.get("/plans/allowed")
        elif args.command == "devices":
            kind, reply = "devices", client.get("/devices/allowed")
        elif args.command == "experiment":
            kind, reply = "experiment", client.get("/status")
        elif args.command == "history":
            if args.clear:
                kind, reply = "clear", client.post("/history/clear")
            else:
                kind, reply = "history", client.get("/history/get")
        elif args.command == "queue":
            kind, reply = _queue(client, args)
        elif args.command == "re":
            kind, reply = _re(client, args)
        else:  # pragma: no cover
            raise SystemExit(2)
    except httpx.HTTPError as exc:
        print(f"error: cannot reach qs at {args.url}: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    print(json.dumps(reply, indent=1) if args.json else _summarise(kind, reply))
    return 0 if reply.get("success", True) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
