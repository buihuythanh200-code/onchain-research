
import csv
from decimal import Decimal
from statistics import median

D = Decimal

INPUT_FILE = "aave_positions_full.csv"

WAD = 10**18
PERCENTAGE_FACTOR = 10000
HALF_PERCENTAGE_FACTOR = 5000


def percent_mul(value, percentage):
    return (
        value * percentage
        + HALF_PERCENTAGE_FACTOR
    ) // PERCENTAGE_FACTOR


def wad_div(a, b):
    if b == 0:
        raise ZeroDivisionError("Debt is zero")

    return (a * WAD + b // 2) // b


def calculate_hf(collateral_raw, debt_raw, lt_bps):
    weighted_collateral = percent_mul(
        collateral_raw, lt_bps
    )

    return wad_div(
        weighted_collateral, debt_raw
    )


with open(INPUT_FILE, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

results = []

for row in rows:

    collateral = int(row["collateral_base_raw"])
    debt = int(row["debt_base_raw"])

    if debt == 0:
        continue

    lt_bps = int(
        D(row["liquidation_threshold"]) * 10000
    )

    actual_hf = int(row["health_factor_raw"])

    predicted_hf = calculate_hf(
        collateral, debt, lt_bps
    )

    error_raw = abs(actual_hf - predicted_hf)

    actual_decimal = D(actual_hf) / D(WAD)
    predicted_decimal = D(predicted_hf) / D(WAD)

    relative_error = (
        D(error_raw) / D(actual_hf)
        if actual_hf > 0 else None
    )

    results.append({
        "address": row["borrower_address"],
        "debt_usd": D(row["debt_usd"]),
        "actual_hf": actual_decimal,
        "predicted_hf": predicted_decimal,
        "error_raw": error_raw,
        "absolute_error": D(error_raw) / D(WAD),
        "relative_error": relative_error
    })


results.sort(
    key=lambda r: r["error_raw"],
    reverse=True
)

exact = sum(
    r["error_raw"] == 0 for r in results
)

classification_mismatches = sum(
    (r["actual_hf"] < 1)
    != (r["predicted_hf"] < 1)
    for r in results
)

print("=== HEALTH FACTOR VALIDATION ===")
print("ACTIVE BORROWERS:", len(results))
print("EXACT MATCH:", exact)
print("NON-EXACT MATCH:", len(results) - exact)

print(
    "MAX ABSOLUTE ERROR:",
    max(r["absolute_error"] for r in results)
)

print(
    "MEDIAN ABSOLUTE ERROR:",
    median(r["absolute_error"] for r in results)
)

print(
    "LIQUIDATION CLASSIFICATION MISMATCHES:",
    classification_mismatches
)

print("\n=== TOP 5 LARGEST ERRORS ===")

for r in results[:5]:
    print("\nADDRESS:", r["address"])
    print("DEBT USD:", r["debt_usd"])
    print("ON-CHAIN HF:", r["actual_hf"])
    print("CALCULATED HF:", r["predicted_hf"])
    print("ABSOLUTE ERROR:", r["absolute_error"])
    print("RELATIVE ERROR:", r["relative_error"])

output = "aave_hf_validation.csv"

with open(
    output, "w", newline="", encoding="utf-8"
) as f:
    writer = csv.DictWriter(
        f, fieldnames=results[0].keys()
    )
    writer.writeheader()
    writer.writerows(results)

print("\nCSV SAVED:", output)
