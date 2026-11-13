"""Settings are typed, validated, frozen, and passed in rather than read through a global."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from core.config import CONFIG_PATH, ROOT, Pitch, Settings, Xg, load
from core.events import Match
from core.moments import overload_moments
from core.shares import lane_of
from core.xg import xg
from tests.conftest import LEAGUE


def _raw() -> dict[str, Any]:
    return load(CONFIG_PATH).model_dump()


def test_config_yaml_loads_with_committed_values(settings: Settings) -> None:
    assert settings.seed == 7
    assert settings.pitch == Pitch(length=105, width=68, own_third_x=35, final_third_x=70)
    assert settings.verifier.n_min == 6
    assert settings.router.t0_metrics == ("goals", "substitutions", "cards")
    assert settings.verifier.early_markers == ("early sign", "so far")


def test_unknown_key_is_rejected() -> None:
    raw = _raw()
    raw["verifier"]["n_mni"] = 6
    with pytest.raises(ValidationError, match="n_mni"):
        Settings.model_validate(raw)


def test_wrong_type_is_rejected() -> None:
    raw = _raw()
    raw["pitch"]["length"] = "wide"
    with pytest.raises(ValidationError, match="length"):
        Settings.model_validate(raw)


def test_missing_section_is_rejected() -> None:
    raw = _raw()
    del raw["router"]
    with pytest.raises(ValidationError, match="router"):
        Settings.model_validate(raw)


def test_settings_are_frozen(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        settings.seed = 8  # type: ignore[misc]
    with pytest.raises(ValidationError):
        settings.pitch.length = 100  # type: ignore[misc]


def test_pitch_ints_stay_ints(settings: Settings) -> None:
    # hero.json carries the pitch section; a float would change the published bytes.
    assert all(isinstance(v, int) for v in settings.pitch.model_dump().values())


def test_xg_uses_the_coefficients_it_is_given(settings: Settings) -> None:
    zero = dict.fromkeys(
        ("intercept", "dist", "angle", "header", "other_body", "free_kick", "under_pressure"), 0.0
    )
    flat = Xg(**zero, penalty=0.7, goal_width=7.32)
    assert xg(94.0, 34.0, settings.pitch, flat) == pytest.approx(0.5)
    assert xg(94.0, 34.0, settings.pitch, settings.xg) != pytest.approx(0.5)


def test_lane_uses_the_pitch_it_is_given() -> None:
    narrow = Pitch(length=105, width=30, own_third_x=35, final_third_x=70)
    wide = Pitch(length=105, width=90, own_third_x=35, final_third_x=70)
    assert lane_of(15.0, narrow) == "centre"
    assert lane_of(15.0, wide) == "left"


def test_overload_moments_use_the_threshold_they_are_given(
    match: Match, settings: Settings
) -> None:
    teams = (match.meta.home, match.meta.away)
    assert overload_moments(match.events, teams, LEAGUE, settings, match.meta.clock)
    impossible = settings.model_copy(
        update={"moments": settings.moments.model_copy(update={"overload_min_share": 1.01})}
    )
    assert overload_moments(match.events, teams, LEAGUE, impossible, match.meta.clock) == []


def test_importing_the_pipeline_reads_no_project_file() -> None:
    # Config, schema and data are read when an entry point asks, never at import time.
    # Libraries may read their own package files; only files of this repo count.
    code = (
        "import builtins, io, pathlib, sys\n"
        "seen = []\n"
        "real = io.open\n"
        "def spy(file, *a, **k):\n"
        "    seen.append(pathlib.Path(file).resolve())\n"
        "    return real(file, *a, **k)\n"
        "builtins.open = io.open = spy\n"
        f"{_IMPORTS}\n"
        "root = pathlib.Path.cwd().resolve()\n"
        "venv = pathlib.Path(sys.prefix).resolve()\n"
        "ours = [p for p in seen if p.is_relative_to(root) and not p.is_relative_to(venv)]\n"
        "print([p.relative_to(root).as_posix() for p in ours if '.egg-info' not in str(p)])\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert run.stdout.strip() == "[]"


def test_the_import_spy_sees_project_reads() -> None:
    # Guards the test above: with a load() call added, the spy must report config.yaml.
    code = (
        "import builtins, io\n"
        "seen = []\n"
        "real = io.open\n"
        "def spy(file, *a, **k):\n"
        "    seen.append(str(file))\n"
        "    return real(file, *a, **k)\n"
        "builtins.open = io.open = spy\n"
        f"{_IMPORTS}\n"
        "from core.config import load\n"
        "load()\n"
        "print(any(p.endswith('config.yaml') for p in seen))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert run.stdout.strip() == "True"


_IMPORTS = "import app.build_bundle, app.verifier, app.agents_foundry, core.facts"
