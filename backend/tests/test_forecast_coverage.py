"""Fix #55: forecasts for the regions that do not train.

Four states train the shared model, on six medicines. The publisher writes
forecasts for those states only by default, but `--states` accepted any state,
and the web service used any fresh forecast row it found — so a forecast from
a model that never saw a state could have been presented while the screen
beside it said "burn rate — this state is not in the shared model yet".

No held-out-state check exists, so nothing may claim the model works outside
the states it trained on. A forecast is therefore used only where the model
trained: the publisher refuses any other state, and both places that read
forecasts ignore a row outside the model's coverage.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app import earlywarning, services, workspace

FEDERATION = Path(__file__).resolve().parents[1] / "federation"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_a_forecast_applies_only_where_the_model_trained():
    assert services.forecast_applies("MH", "ORS")
    assert services.forecast_applies("UP", "ZINC")
    assert not services.forecast_applies("RJ", "ORS")        # a state that never trained
    assert not services.forecast_applies("MH", "MORPH10")    # a medicine it never saw
    assert not services.forecast_applies("RJ", "MORPH10")


def test_the_coverage_is_one_definition():
    assert workspace.MODEL_STATES is services.MODEL_STATES
    assert workspace.MODEL_SKUS is services.MODEL_SKUS


def test_a_forecast_row_outside_the_coverage_is_ignored_and_the_burn_rate_stands():
    fresh = (9.0, NOW)
    assert services.pick_rate(4.0, fresh, state="MH", sku="ORS") == (9.0, "federated", NOW)
    assert services.pick_rate(4.0, fresh, state="RJ", sku="ORS") == (4.0, "burn_rate", None)
    assert services.pick_rate(4.0, fresh, state="MH", sku="MORPH10") == (4.0, "burn_rate", None)
    assert services.pick_rate(4.0, None, state="MH", sku="ORS") == (4.0, "burn_rate", None)


def test_the_next_14_days_strip_reads_forecasts_only_inside_the_coverage():
    sql = earlywarning.PAIRS_SQL
    assert "f.state_silo = ANY(:model_states)" in sql
    assert "s.sku_code = ANY(:model_skus)" in sql


def test_the_publisher_refuses_a_state_the_model_did_not_train_on():
    src = (FEDERATION / "publish_forecast.py").read_text(encoding="utf-8")
    # The refusal, and the fact that it runs before any model call or write.
    guard = re.search(r"outside = \[s for s in states if s not in SILOS\]\n\s+if outside:", src)
    assert guard, "publish_forecast.py no longer refuses states outside SILOS"
    assert guard.start() < src.index("model = DemandLSTM()")
    assert "held-out" in src
