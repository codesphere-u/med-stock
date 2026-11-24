"""CLI for med-stock."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date

from med_stock.planner import (
    ClinicDemandProfile,
    MedicineBatch,
    evaluate_clinic_stock,
    plan_district_redistribution,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="med-stock",
        description="WHO EML rural clinic stockout early-warning, FEFO expiry & redistribution planner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Simulate district ACT antimalarial redistribution preventing expiry & stockout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        today = date(2026, 11, 24)
        profiles = [
            ClinicDemandProfile(
                "HC3-NORTH", "ACT-COARTEM", avg_daily_consumption=20.0, daily_std_dev=4.0, lead_time_days=14
            ),
            ClinicDemandProfile(
                "HC2-SOUTH", "ACT-COARTEM", avg_daily_consumption=3.0, daily_std_dev=1.0, lead_time_days=14
            ),
        ]
        batches = [
            MedicineBatch("B-101", "ACT-COARTEM", "HC3-NORTH", 80, date(2027, 4, 1)),
            MedicineBatch("B-202", "ACT-COARTEM", "HC2-SOUTH", 400, date(2027, 1, 20)),
        ]
        statuses = [evaluate_clinic_stock(p, batches, as_of=today) for p in profiles]
        transfers = plan_district_redistribution(profiles, batches, as_of=today)
        print(
            json.dumps(
                {
                    "clinics": [
                        {
                            "clinic_id": s.clinic_id,
                            "status": s.status,
                            "days_of_stock": s.days_of_stock,
                            "at_risk_expiry_units": s.at_risk_expiry_units,
                            "reorder_point": s.reorder_point_units,
                        }
                        for s in statuses
                    ],
                    "redistribution_transfers": [
                        {
                            "from": t.from_clinic,
                            "to": t.to_clinic,
                            "batch_id": t.batch_id,
                            "units": t.units,
                            "reason": t.reason,
                        }
                        for t in transfers
                    ],
                },
                indent=2,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
