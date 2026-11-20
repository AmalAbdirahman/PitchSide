"""Ask PitchSide live mode (R6): request limits, the one-at-a-time gate and the local server,
with a mocked Analyst. No network beyond 127.0.0.1."""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator

import pytest

from app.ask_live import (
    Busy,
    Gate,
    LiveError,
    LiveService,
    TimedOut,
    make_server,
    parse_request,
)
from app.ask_schema import REFUSAL
from core.config import ROOT
from tests.test_ask import FACTS, MATCH, SHOTS, Mock, Q, S, refuse, shots_answer

LIVE = S.ask.live
END = MATCH.meta.clock.end_t
ORIGIN = LIVE.allowed_origins[0]
ASK_JSON = ROOT / "docs" / "data" / "ask.json"


def _body(question: str = Q.text, at_t: float = Q.at_t) -> bytes:
    return json.dumps({"question": question, "at_t": at_t}).encode()


# ---- request limits ----------------------------------------------------------------------


def test_a_valid_request_is_trimmed_and_kept() -> None:
    req = parse_request(_body("  " + Q.text + "  "), LIVE, END)
    assert (req.question, req.at_t) == (Q.text, Q.at_t)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"question": "x"}).encode(),
        json.dumps({"question": 3, "at_t": 10}).encode(),
        json.dumps({"question": "x", "at_t": "ten"}).encode(),
        json.dumps({"question": "x", "at_t": True}).encode(),
        _body("   "),
        _body("x" * (LIVE.max_chars + 1)),
        _body(at_t=-1),
        _body(at_t=END + 1),
        _body(at_t=float("nan")),
    ],
)
def test_bad_requests_are_refused_with_400(body: bytes) -> None:
    with pytest.raises(LiveError) as e:
        parse_request(body, LIVE, END)
    assert e.value.status == 400


def test_the_limit_is_200_characters() -> None:
    assert LIVE.max_chars == 200 and LIVE.timeout_s == 30
    assert parse_request(_body("x" * 200), LIVE, END).question == "x" * 200


# ---- answers: the same pipeline and checks as R5 -------------------------------------------


def _service(mock: Mock, timeout_s: float = LIVE.timeout_s) -> LiveService:
    return LiveService(MATCH, FACTS, S, mock, Gate(timeout_s))


def test_a_correct_live_answer_is_verified() -> None:
    answer = _service(Mock(shots_answer(SHOTS))).answer(parse_request(_body(), LIVE, END))
    assert answer.status == "verified" and answer.question_id == "live"
    assert answer.at_minute == MATCH.meta.clock.minute(Q.at_t)
    assert all(n.verified for n in answer.numbers)


def test_a_wrong_live_number_is_retried_and_then_refused() -> None:
    mock = Mock(shots_answer(SHOTS + 1))
    answer = _service(mock).answer(parse_request(_body(), LIVE, END))
    assert [a.outcome for a in answer.attempts] == ["failed", "failed"]
    assert "numbers_match" in mock.requests[1]["feedback"]
    assert answer.status == "refused" and answer.text == REFUSAL


def test_an_unanswerable_live_question_yields_the_refusal() -> None:
    answer = _service(Mock(refuse)).answer(parse_request(_body(), LIVE, END))
    assert answer.status == "refused" and answer.text == REFUSAL


def test_live_answers_are_not_cached_between_questions() -> None:
    mock = Mock(shots_answer(SHOTS))
    svc = _service(mock)
    for _ in range(2):
        svc.answer(parse_request(_body(), LIVE, END))
    assert len(mock.requests) == 2


# ---- the gate: one question at a time, with a time limit -----------------------------------


def test_the_gate_refuses_a_second_question_while_one_runs() -> None:
    gate, started, release = Gate(5), threading.Event(), threading.Event()

    def slow() -> int:
        started.set()
        release.wait(5)
        return 1

    t = threading.Thread(target=gate.run, args=(slow,))
    t.start()
    started.wait(5)
    with pytest.raises(Busy):
        gate.run(lambda: 2)
    release.set()
    t.join(5)
    assert gate.run(lambda: 3) == 3


def test_the_gate_times_out_and_stays_busy_until_the_call_ends() -> None:
    gate, release = Gate(0.05), threading.Event()
    with pytest.raises(TimedOut):
        gate.run(lambda: release.wait(5))
    with pytest.raises(Busy):  # the timed-out call still runs: no second call meanwhile
        gate.run(lambda: 1)
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            assert gate.run(lambda: 2) == 2
            break
        except Busy:
            time.sleep(0.01)
    else:
        pytest.fail("the gate never freed")


# ---- the local server ----------------------------------------------------------------------


@pytest.fixture()
def server() -> Iterator[int]:
    srv = make_server(_service(Mock(shots_answer(SHOTS))), LIVE, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _req(
    port: int, method: str, path: str, body: bytes | None = None, **headers: str
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        method, path, body=body, headers={k.replace("_", "-"): v for k, v in headers.items()}
    )
    res = conn.getresponse()
    out = res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    conn.close()
    return out


def test_the_server_binds_to_loopback_only() -> None:
    svc = _service(Mock(refuse))
    for host in ("0.0.0.0", "", "192.168.1.10", "localhost.example"):
        with pytest.raises(ValueError):
            make_server(svc, LIVE, host, 0)


def test_health_answers_without_calling_the_model(server: int) -> None:
    status, headers, body = _req(server, "GET", "/health", Origin=ORIGIN)
    assert status == 200 and headers["access-control-allow-origin"] == ORIGIN
    doc = json.loads(body)
    assert doc == {"ok": True, "max_chars": LIVE.max_chars, "timeout_s": LIVE.timeout_s}


def test_ask_returns_the_same_shape_as_ask_json(server: int) -> None:
    status, headers, body = _req(
        server, "POST", "/ask", _body(), Origin=ORIGIN, Content_Type="application/json"
    )
    assert status == 200 and headers["access-control-allow-origin"] == ORIGIN
    built = json.loads(ASK_JSON.read_text("utf-8"))["answers"][0]
    assert set(json.loads(body)) == set(built)
    assert json.loads(body)["status"] == "verified"


def test_a_preflight_allows_the_page_and_the_private_network(server: int) -> None:
    status, headers, _ = _req(
        server,
        "OPTIONS",
        "/ask",
        Origin=ORIGIN,
        Access_Control_Request_Method="POST",
        Access_Control_Request_Headers="content-type",
        Access_Control_Request_Private_Network="true",
    )
    assert status == 204
    assert headers["access-control-allow-origin"] == ORIGIN
    assert headers["access-control-allow-private-network"] == "true"
    assert "content-type" in headers["access-control-allow-headers"].lower()


def test_other_origins_are_refused(server: int) -> None:
    for method, path in (("GET", "/health"), ("OPTIONS", "/ask")):
        status, headers, _ = _req(server, method, path, Origin="https://evil.example")
        assert status == 403 and "access-control-allow-origin" not in headers
    status, _, _ = _req(
        server, "POST", "/ask", _body(), Origin="https://evil.example", Content_Type="text/plain"
    )
    assert status == 403


def test_ask_needs_json_and_a_valid_body(server: int) -> None:
    status, _, _ = _req(server, "POST", "/ask", _body(), Origin=ORIGIN, Content_Type="text/plain")
    assert status == 415
    status, _, body = _req(
        server, "POST", "/ask", _body("   "), Origin=ORIGIN, Content_Type="application/json"
    )
    assert status == 400 and "error" in json.loads(body)
    status, _, _ = _req(
        server,
        "POST",
        "/ask",
        b"x" * 10_000,
        Origin=ORIGIN,
        Content_Type="application/json",
    )
    assert status == 413


def test_unknown_paths_are_404(server: int) -> None:
    assert _req(server, "GET", "/", Origin=ORIGIN)[0] == 404
    assert _req(server, "GET", "/../config.yaml", Origin=ORIGIN)[0] == 404
