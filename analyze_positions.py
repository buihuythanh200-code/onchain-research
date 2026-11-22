
import csv
from decimal import Decimal
from statistics import median

D = Decimal

INPUT_FILE = "aave_positions_full.csv"
OUTPUT_FILE = "aave_position_risk_analysis.csv"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

active = []
errors = []
seen = set()

# ===== VALIDATE DATA =====

for row in rows:
    address = row["borrower_address"].lower()

    if address in seen:
        raise ValueError(f"Duplicate borrower: {address}")

    seen.add(address)

    if int(row["snapshot_block"]) != 26402092:
        raise ValueError("Snapshot block mismatch")

    collateral = D(row["collateral_usd"])
    debt = D(row["debt_usd"])
    threshold = D(row["liquidation_threshold"])

    if collateral < 0 or debt < 0:
        raise ValueError("Negative financial value")

    if not D(0) <= threshold <= D(1):
        raise ValueError("Invalid liquidation threshold")

    if debt == 0:
        continue

    hf = D(row["health_factor"])

    # Recalculate HF from aggregate account data.
    hf_calculated = collateral * threshold / debt

    error = abs(hf - hf_calculated)

    errors.append(error)

    # Price decline needed to reach HF = 1,
    # assuming proportional collateral decline
    # and unchanged debt and threshold.
    critical_drop = (
        (D(1) - D(1) / hf) * D(100)
        if hf > 1 else D(0)
    )

    if hf < 1:
        risk_group = "LIQUIDATABLE"
    elif hf < D("1.05"):
        risk_group = "VERY_HIGH"
    elif hf < D("1.10"):
        risk_group = "HIGH"
    elif hf < D("1.25"):
        risk_group = "ELEVATED"
    elif hf < D("1.50"):
        risk_group = "MODERATE"
    elif hf < D("2.00"):
        risk_group = "LOW"
    else:
        risk_group = "VERY_LOW"

    active.append({
        "borrower_address": row["borrower_address"],
        "snapshot_block": row["snapshot_block"],
        "collateral_usd": collateral,
        "debt_usd": debt,
        "liquidation_threshold": threshold,
        "health_factor": hf,
        "hf_calculated": hf_calculated,
        "hf_absolute_error": error,
        "critical_drop_percent": critical_drop,
        "risk_group": risk_group
    })

if not active:
    raise RuntimeError("No active borrowers")

active.sort(key=lambda x: x["health_factor"])

# ===== DATA QUALITY =====

print("=== DATA QUALITY REPORT ===")
print("TOTAL ACCOUNTS:", len(rows))
print("UNIQUE ACCOUNTS:", len(seen))
print("ACTIVE BORROWERS:", len(active))
print("NO DEBT:", len(rows) - len(active))
print("MAX HF ABSOLUTE ERROR:", max(errors))
print("MEDIAN HF ERROR:", median(errors))

print("\n=== HEALTH FACTOR SUMMARY ===")
print("MIN HF:", active[0]["health_factor"])
print("MAX HF:", active[-1]["health_factor"])
print("MEDIAN HF:", median(
    r["health_factor"] for r in active
))

# ===== RISK DISTRIBUTION =====

groups = [
    "LIQUIDATABLE",
    "VERY_HIGH",
    "HIGH",
    "ELEVATED",
    "MODERATE",
    "LOW",
    "VERY_LOW"
]

print("\n=== RISK DISTRIBUTION ===")

for group in groups:

    members = [
        r for r in active
        if r["risk_group"] == group
    ]

    debt = sum(
        (r["debt_usd"] for r in members),
        D(0)
    )

    print(
        f"{group:15} "
        f"| COUNT: {len(members):3} "
        f"| DEBT USD: {debt:,.2f}"
    )

# ===== TOP 10 RISKY BORROWERS =====

print("\n=== TOP 10 LOWEST HEALTH FACTORS ===")

for i, row in enumerate(active[:10], 1):

    print(
        f"{i:2}. "
        f"{row['borrower_address'][:12]}... "
        f"| HF: {row['health_factor']:.6f} "
        f"| DEBT: {row['debt_usd']:,.2f} "
        f"| DROP TO HF=1: "
        f"{row['critical_drop_percent']:.4f}%"
    )

# ===== SAVE ANALYSIS =====

with open(
    OUTPUT_FILE,
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=active[0].keys()
    )

    writer.writeheader()
    writer.writerows(active)

print("\nCSV SAVED:", OUTPUT_FILE)
