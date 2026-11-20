"""Ask PitchSide live mode (R6): one typed question at a time, answered on this machine.

The question goes through exactly the R5 pipeline (app/ask.answer_question): the Analyst's
read-only tools are capped at the replay time the page sends, code checks every number, name,
window and evidence id, a failure is retried once, and anything still unchecked becomes the
refusal. The answer has the same JSON shape as an entry of docs/data/ask.json.

The server binds to a loopback address only, answers only the origins in config.yaml
(ask.live.allowed_origins), takes at most ask.live.max_chars characters, runs one question at a
time and gives up waiting after ask.live.timeout_s. Live answers are never written to
data/ask_cache.json, so the offline build stays byte-identical. scripts/ask_server.py starts it.
"""

from __future__ import annotations

import ipaddress
import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TypeVar

from app.ask import Analyst, AnalystCaller, answer_question
from app.ask_schema import AskAnswer, Question
from core.config import AskLive, Settings
from core.events import Match
from core.facts import Fact

T = TypeVar("T")
MAX_BODY = 4096  # bytes; far above 200 characters of JSON-escaped text, far below anything costly


class LiveError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class Busy(LiveError):
    def __init__(self) -> None:
        super().__init__(429, "Another question is still running. Try again when it finishes.")


class TimedOut(LiveError):
    def __init__(self, seconds: float) -> None:
        super().__init__(504, f"No checked answer within {seconds:g} s.")


@dataclass(frozen=True)
class LiveRequest:
    question: str
    at_t: float  # the page's replay clock; the Analyst's tools never look past it


def parse_request(body: bytes, live: AskLive, end_t: float) -> LiveRequest:
    """The question and replay time from a POST body, or LiveError(400)."""
    try:
        doc = json.loads(body)
    except ValueError:
        raise LiveError(400, "The body must be JSON.") from None
    if not isinstance(doc, dict):
        raise LiveError(400, "The body must be a JSON object.")
    question, at_t = doc.get("question"), doc.get("at_t")
    if not isinstance(question, str):
        raise LiveError(400, "question must be text.")
    if isinstance(at_t, bool) or not isinstance(at_t, int | float) or not math.isfinite(at_t):
        raise LiveError(400, "at_t must be a number of seconds.")
    if not 0 <= at_t <= end_t:
        raise LiveError(400, f"at_t must lie between 0 and {end_t:g}.")
    text = question.strip()
    if not 0 < len(text) <= live.max_chars:
        raise LiveError(400, f"Ask a question of 1 to {live.max_chars} characters.")
    return LiveRequest(text, float(at_t))


class Gate:
    """One call at a time. A caller waits at most `timeout_s`; a call that outlives that keeps
    the gate shut until it ends, so two model conversations never overlap."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self._lock = threading.Lock()

    def run(self, fn: Callable[[], T]) -> T:
        if not self._lock.acquire(blocking=False):
            raise Busy()
        box: list[tuple[bool, Any]] = []
        done = threading.Event()

        def work() -> None:
            try:
                box.append((True, fn()))
            except BaseException as e:  # noqa: BLE001 - handed back to the waiting caller below
                box.append((False, e))
            finally:
                done.set()
                self._lock.release()

        threading.Thread(target=work, daemon=True).start()
        if not done.wait(self.timeout_s):
            raise TimedOut(self.timeout_s)
        ok, value = box[0]
        if not ok:
            raise value
        return value  # type: ignore[no-any-return]


@dataclass
class LiveService:
    """What the server needs: the match, its verified facts (for the refusal's closest fact),
    settings, the Analyst caller (FoundryAnalyst, or a mock in tests) and the gate."""

    match: Match
    facts: list[Fact]
    s: Settings
    caller: AnalystCaller
    gate: Gate

    def answer(self, req: LiveRequest) -> AskAnswer:
        q = Question(id="live", text=req.question, at_t=req.at_t)
        analyst = Analyst(self.caller, {}, self.s.ask.max_calls)  # fresh: nothing is cached
        return answer_question(q, self.match, self.facts, analyst, self.s)

    def handle(self, req: LiveRequest) -> AskAnswer:
        return self.gate.run(lambda: self.answer(req))


# ---- HTTP --------------------------------------------------------------------------------


def make_server(service: LiveService, live: AskLive, host: str, port: int) -> ThreadingHTTPServer:
    """The live server on a loopback address; any other host is a ValueError."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"live mode binds to a loopback IPv4 address, not {host!r}") from None
    if ip.version != 4 or not ip.is_loopback:
        raise ValueError(f"live mode binds to a loopback IPv4 address, not {host!r}")
    handler = type("LiveHandler", (_Handler,), {"service": service, "live": live})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


class _Handler(BaseHTTPRequestHandler):
    service: LiveService
    live: AskLive
    server_version = "PitchSideLive"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        print(f"  {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}")

    def _origin(self) -> str | None:
        """The allowed Origin to echo, '' when the request has none (curl), None if refused."""
        origin = self.headers.get("Origin")
        if origin is None:
            return ""
        return origin if origin in self.live.allowed_origins else None

    def _send(self, status: int, doc: object | None, origin: str, extra: dict[str, str]) -> None:
        body = b"" if doc is None else json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        if doc is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, origin: str = "") -> None:
        self._send(status, {"error": message}, origin, {})

    def _route(self, method: str) -> str | None:
        """The allowed origin if the path and origin are fine; else answers and returns None."""
        path = self.path.split("?")[0]
        if path not in ("/health", "/ask"):
            self._error(404, "Not found.")
            return None
        origin = self._origin()
        if origin is None:
            self._error(403, "This origin may not use the local server.")
            return None
        if method != "OPTIONS" and (method == "POST") != (path == "/ask"):
            self._error(405, "Method not allowed.", origin)
            return None
        return origin

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        origin = self._route("OPTIONS")
        if origin is None:
            return
        allow = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
        }
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            allow["Access-Control-Allow-Private-Network"] = "true"
        self._send(204, None, origin, allow)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        origin = self._route("GET")
        if origin is None:
            return
        live = self.live
        doc = {"ok": True, "max_chars": live.max_chars, "timeout_s": live.timeout_s}
        self._send(200, doc, origin, {})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        origin = self._route("POST")
        if origin is None:
            return
        if self.headers.get_content_type() != "application/json":
            return self._error(415, "Send the question as application/json.", origin)
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            return self._error(413, "The request is too large.", origin)
        try:
            svc = self.service
            req = parse_request(self.rfile.read(size), self.live, svc.match.meta.clock.end_t)
            answer = svc.handle(req)
        except LiveError as e:
            return self._error(e.status, e.message, origin)
        except Exception as e:  # noqa: BLE001 - a bug or Foundry outage: say so; the server goes on
            print(f"  live question failed: {type(e).__name__}")
            return self._error(500, "The question could not be answered.", origin)
        self._send(200, answer.model_dump(mode="json"), origin, {})
