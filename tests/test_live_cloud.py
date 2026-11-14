"""P15: the settings the thin Azure slice needs, without changing the local stack.

- Kafka security: none by default (Redpanda in docker compose); SASL_SSL with PLAIN when a
  password is set, which is how Azure Event Hubs' Kafka endpoint takes a connection string.
- Audit log: Postgres by default; "stdout" writes one JSON line per entry (the slice has no
  Postgres; Container Apps sends the lines to Log Analytics).
- The Bicep files build, when the Azure CLI is installed (not in CI).
No network and no Azure account needed."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from live.adapters import StdoutAudit, audit_kind, kafka_security
from live.ports import DecisionTaken

INFRA = Path(__file__).resolve().parents[1] / "infra"


def test_kafka_security_is_off_by_default() -> None:
    assert kafka_security({}) == {}
    assert kafka_security({"PITCHSIDE_KAFKA_SASL_PASSWORD": ""}) == {}


def test_kafka_security_for_event_hubs() -> None:
    opts = kafka_security({"PITCHSIDE_KAFKA_SASL_PASSWORD": "Endpoint=sb://x/;SharedAccessKey=k"})
    assert opts["security_protocol"] == "SASL_SSL"
    assert opts["sasl_mechanism"] == "PLAIN"
    assert opts["sasl_plain_username"] == "$ConnectionString"
    assert opts["sasl_plain_password"] == "Endpoint=sb://x/;SharedAccessKey=k"
    assert isinstance(opts["ssl_context"], ssl.SSLContext)


def test_kafka_security_username_can_be_set() -> None:
    env = {"PITCHSIDE_KAFKA_SASL_PASSWORD": "p", "PITCHSIDE_KAFKA_SASL_USERNAME": "u"}
    assert kafka_security(env)["sasl_plain_username"] == "u"


def test_audit_kind() -> None:
    assert audit_kind({}) == "postgres"
    assert audit_kind({"PITCHSIDE_AUDIT": "stdout"}) == "stdout"
    with pytest.raises(ValueError):
        audit_kind({"PITCHSIDE_AUDIT": "cosmos"})


def test_stdout_audit_writes_one_json_line_per_entry() -> None:
    out = io.StringIO()
    audit = StdoutAudit(out)

    async def run() -> None:
        assert await audit.append("m", "fact_ready", "f1", {"x": 1}) == 1
        assert await audit.append("m", "fact_ready", "f1", {"x": 2}) == 1  # first row kept
        await audit.append("m", "decision", "f1", {"action": "approve"})
        with pytest.raises(DecisionTaken):
            await audit.append("m", "decision", "f1", {"action": "reject"})

    asyncio.run(run())
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [(x["kind"], x["fact_id"], x["seq"]) for x in lines] == [
        ("fact_ready", "f1", 1),
        ("decision", "f1", 2),
    ]
    assert lines[0]["audit"] is True and lines[0]["payload"] == {"x": 1}


def _az() -> str | None:
    return shutil.which("az") or shutil.which("az.cmd")


@pytest.mark.skipif(_az() is None, reason="Azure CLI not installed (CI does not have it)")
@pytest.mark.parametrize("name", sorted(p.name for p in INFRA.glob("**/*.bicep")))
def test_bicep_builds(name: str, tmp_path: Path) -> None:
    src = next(INFRA.glob(f"**/{name}"))
    out = tmp_path / "out.json"
    done = subprocess.run(  # noqa: S603 - fixed arguments, local files
        [str(_az()), "bicep", "build", "--file", str(src), "--outfile", str(out)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    assert "warning" not in done.stderr.lower(), done.stderr


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_az() is None or _powershell() is None, reason="needs Azure CLI and PowerShell")
def test_deploy_script_only_builds_by_default() -> None:
    script = INFRA / "deploy.ps1"
    done = subprocess.run(  # noqa: S603 - fixed arguments, local files
        [str(_powershell()), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Nothing deployed" in done.stdout
