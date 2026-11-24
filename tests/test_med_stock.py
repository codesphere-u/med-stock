"""Tests for WHO EML clinic stockout early-warning, FEFO expiry waste & redistribution."""

from __future__ import annotations

import json
from datetime import date

import pytest

from med_stock import (
    ClinicDemandProfile,
    MedicineBatch,
    evaluate_clinic_stock,
    plan_district_redistribution,
)
from med_stock.cli import main as cli_main


def test_detects_expiry_waste_stockout_and_plans_fefo_redistribution() -> None:
    today = date(2026, 11, 24)
    p_short = ClinicDemandProfile(
        "CLINIC-A", "ORS-ZINC", avg_daily_consumption=25.0, daily_std_dev=5.0, lead_time_days=10
    )
    p_over = ClinicDemandProfile(
        "CLINIC-B", "ORS-ZINC", avg_daily_consumption=2.0, daily_std_dev=0.5, lead_time_days=10
    )
    batches = [
        MedicineBatch("EXPIRED-0", "ORS-ZINC", "CLINIC-A", 50, date(2026, 11, 1)),
        MedicineBatch("GOOD-1", "ORS-ZINC", "CLINIC-A", 40, date(2027, 6, 1)),
        MedicineBatch("SOON-2", "ORS-ZINC", "CLINIC-B", 300, date(2027, 1, 15)),
    ]

    st_a = evaluate_clinic_stock(p_short, batches, as_of=today)
    assert st_a.expired_units == 50
    assert st_a.status == "critical"
    assert st_a.recommended_order_units > 0

    st_b = evaluate_clinic_stock(p_over, batches, as_of=today)
    assert st_b.status == "overstocked"
    assert st_b.at_risk_expiry_units > 150

    transfers = plan_district_redistribution([p_short, p_over], batches, as_of=today)
    assert len(transfers) == 1
    assert transfers[0].from_clinic == "CLINIC-B"
    assert transfers[0].to_clinic == "CLINIC-A"
    assert transfers[0].units > 100


def test_cli_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["demo"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["redistribution_transfers"]) == 1
