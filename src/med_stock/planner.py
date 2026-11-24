"""Essential Medicines (WHO EML) stockout early-warning, FEFO expiry & district redistribution engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MedicineBatch:
    batch_id: str
    sku: str
    clinic_id: str
    units_on_hand: int
    expiry_date: date
    unit_cost_usd: float = 0.0


@dataclass(frozen=True)
class ClinicDemandProfile:
    clinic_id: str
    sku: str
    avg_daily_consumption: float
    daily_std_dev: float
    lead_time_days: int
    service_level_z: float = 1.65  # 95% cycle service level


@dataclass(frozen=True)
class ClinicStockStatus:
    clinic_id: str
    sku: str
    usable_units: int
    expired_units: int
    at_risk_expiry_units: int  # Units that will expire before consumed at current demand
    days_of_stock: float
    safety_stock_units: int
    reorder_point_units: int
    recommended_order_units: int
    status: str  # stockout | critical | reorder | healthy | overstocked


@dataclass(frozen=True)
class RedistributionTransfer:
    from_clinic: str
    to_clinic: str
    sku: str
    batch_id: str
    units: int
    reason: str


def evaluate_clinic_stock(
    profile: ClinicDemandProfile,
    batches: list[MedicineBatch],
    *,
    as_of: date,
    target_cover_days: int = 45,
) -> ClinicStockStatus:
    """Evaluate usable FEFO inventory, expiry waste risk, ROP, and reorder need for one clinic SKU."""
    if profile.avg_daily_consumption < 0 or profile.lead_time_days < 0:
        raise ValueError("Consumption and lead time must be non-negative")

    relevant = [b for b in batches if b.clinic_id == profile.clinic_id and b.sku == profile.sku]
    expired_units = sum(b.units_on_hand for b in relevant if b.expiry_date <= as_of)
    usable_batches = sorted(
        [b for b in relevant if b.expiry_date > as_of and b.units_on_hand > 0],
        key=lambda b: b.expiry_date,
    )
    usable_units = sum(b.units_on_hand for b in usable_batches)

    # Simulate FEFO consumption forward to count units that expire before they can be dispensed
    at_risk_expiry = 0
    cumulative_consumed_days = 0.0
    rate = profile.avg_daily_consumption
    for b in usable_batches:
        days_left = (b.expiry_date - as_of).days
        if rate <= 0:
            at_risk_expiry += b.units_on_hand
            continue
        dispensable_window_days = max(0.0, days_left - cumulative_consumed_days)
        max_dispensable = math.floor(dispensable_window_days * rate)
        consumed_from_batch = min(b.units_on_hand, max_dispensable)
        at_risk_expiry += b.units_on_hand - consumed_from_batch
        cumulative_consumed_days += consumed_from_batch / rate

    effective_usable = max(0, usable_units - at_risk_expiry)
    days_of_stock = (effective_usable / rate) if rate > 0 else 999.0

    safety_stock = math.ceil(
        profile.service_level_z * profile.daily_std_dev * math.sqrt(max(1, profile.lead_time_days))
    )
    rop = math.ceil(rate * profile.lead_time_days) + safety_stock
    target_units = math.ceil(rate * target_cover_days) + safety_stock
    order_qty = max(0, target_units - effective_usable) if effective_usable <= rop else 0

    if effective_usable == 0 and rate > 0:
        status = "stockout"
    elif days_of_stock <= profile.lead_time_days:
        status = "critical"
    elif effective_usable <= rop:
        status = "reorder"
    elif at_risk_expiry > 0:
        status = "overstocked"
    else:
        status = "healthy"

    return ClinicStockStatus(
        clinic_id=profile.clinic_id,
        sku=profile.sku,
        usable_units=usable_units,
        expired_units=expired_units,
        at_risk_expiry_units=at_risk_expiry,
        days_of_stock=round(days_of_stock, 1),
        safety_stock_units=safety_stock,
        reorder_point_units=rop,
        recommended_order_units=order_qty,
        status=status,
    )


def plan_district_redistribution(
    profiles: list[ClinicDemandProfile],
    batches: list[MedicineBatch],
    *,
    as_of: date,
) -> list[RedistributionTransfer]:
    """Transfer soon-to-expire surplus stock from overstocked clinics to stockout/critical clinics."""
    statuses = {
        (p.clinic_id, p.sku): evaluate_clinic_stock(p, batches, as_of=as_of)
        for p in profiles
    }
    transfers: list[RedistributionTransfer] = []
    skus = sorted({p.sku for p in profiles})

    for sku in skus:
        donors = [
            st for (_, s), st in statuses.items() if s == sku and st.at_risk_expiry_units > 0
        ]
        recipients = sorted(
            [st for (_, s), st in statuses.items() if s == sku and st.status in ("stockout", "critical")],
            key=lambda st: st.days_of_stock,
        )
        donor_surplus = {d.clinic_id: d.at_risk_expiry_units for d in donors}

        for rec in recipients:
            needed = max(1, rec.reorder_point_units - rec.usable_units)
            for donor in donors:
                avail = donor_surplus.get(donor.clinic_id, 0)
                if avail <= 0 or needed <= 0:
                    continue
                # Pick shortest valid expiry batch at donor (FEFO rescue)
                donor_batches = sorted(
                    [b for b in batches if b.clinic_id == donor.clinic_id and b.sku == sku and b.expiry_date > as_of],
                    key=lambda b: b.expiry_date,
                )
                if not donor_batches:
                    continue
                qty = min(avail, needed, donor_batches[0].units_on_hand)
                if qty <= 0:
                    continue
                donor_surplus[donor.clinic_id] -= qty
                needed -= qty
                transfers.append(
                    RedistributionTransfer(
                        from_clinic=donor.clinic_id,
                        to_clinic=rec.clinic_id,
                        sku=sku,
                        batch_id=donor_batches[0].batch_id,
                        units=qty,
                        reason=(
                            f"Rescue expiry-risk surplus ({donor.clinic_id}) "
                            f"for {rec.status} clinic ({rec.clinic_id})"
                        ),
                    )
                )
    return transfers
