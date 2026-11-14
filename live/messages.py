"""Messages on the live topics, as Pydantic models.

events topic (replayer -> worker): Start (match metadata and league priors), then one EventMsg per
event in time order, then End. facts topic (worker -> API): the Start again, Tick (clock and score
after each event), FactMsg (a verified fact ready to route, or a rejected one), and End. Every message carries the
match id, which is also its Kafka key, so one match stays in one partition and in order.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from core.events import Event, Meta


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    match_id: str
    sent_at: float  # wall-clock seconds (time.time()) when the message was sent


class Start(_Msg):
    kind: Literal["start"] = "start"
    meta: Meta
    league: dict[str, Any]  # core.sources.League as a dict


class EventMsg(_Msg):
    kind: Literal["event"] = "event"
    seq: int
    event: Event


class End(_Msg):
    kind: Literal["end"] = "end"
    t: float  # match time of the final whistle


class Tick(_Msg):
    kind: Literal["tick"] = "tick"
    t: float
    minute: int
    score: dict[str, int]


class FactMsg(_Msg):
    kind: Literal["fact"] = "fact"
    status: Literal["ready", "rejected"]
    fact: dict[str, Any]  # core.facts.Fact.to_dict()
    trigger_seq: int  # the events-topic message whose arrival made the moment due (-1: End)
    latency_ms: float  # wall clock from that message's sent_at to this fact being ready


Message = Annotated[Start | EventMsg | End | Tick | FactMsg, Field(discriminator="kind")]
_ADAPTER: TypeAdapter[Start | EventMsg | End | Tick | FactMsg] = TypeAdapter(Message)


def parse(data: dict[str, Any]) -> Start | EventMsg | End | Tick | FactMsg:
    return _ADAPTER.validate_python(data)


def dump(msg: _Msg) -> dict[str, Any]:
    return msg.model_dump(mode="json")
