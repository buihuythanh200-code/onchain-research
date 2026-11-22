
import csv
import time

from decimal import Decimal
from web3 import Web3

# ===== CONFIGURATION =====

RPC_URL = "https://rpc.mevblocker.io"

POOL_ADDRESS = Web3.to_checksum_address(
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

SNAPSHOT_BLOCK = 26402092

INPUT_FILE = "aave_borrowers.csv"
OUTPUT_FILE = "aave_positions_full.csv"

# Test 10 borrowers first.
LIMIT = 555

BASE_UNIT = Decimal("100000000")
HF_UNIT = Decimal("1000000000000000000")
BPS_UNIT = Decimal("10000")

MAX_UINT256 = 2**256 - 1

# ===== CONNECT TO ETHEREUM =====

w3 = Web3(
    Web3.HTTPProvider(
        RPC_URL,
        request_kwargs={"timeout": 60}
    )
)

if not w3.is_connected():
    raise RuntimeError("RPC connection failed")

if w3.eth.chain_id != 1:
    raise RuntimeError("Wrong Ethereum network")

print("CONNECTED:", w3.is_connected())
print("CHAIN ID:", w3.eth.chain_id)
print("SNAPSHOT BLOCK:", SNAPSHOT_BLOCK)

# ===== AAVE POOL ABI =====

POOL_ABI = [
    {
        "name": "getUserAccountData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "user", "type": "address"}
        ],
        "outputs": [
            {
                "name": "totalCollateralBase",
                "type": "uint256"
            },
            {
                "name": "totalDebtBase",
                "type": "uint256"
            },
            {
                "name": "availableBorrowsBase",
                "type": "uint256"
            },
            {
                "name": "currentLiquidationThreshold",
                "type": "uint256"
            },
            {
                "name": "ltv",
                "type": "uint256"
            },
            {
                "name": "healthFactor",
                "type": "uint256"
            }
        ]
    }
]

pool = w3.eth.contract(
    address=POOL_ADDRESS,
    abi=POOL_ABI
)

# ===== LOAD BORROWERS =====

with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as f:
    borrowers = list(csv.DictReader(f))

borrowers = borrowers[:LIMIT]

print("BORROWERS TO QUERY:", len(borrowers))

results = []
failed = []

# ===== COLLECT POSITIONS =====

for index, row in enumerate(borrowers, 1):

    address = Web3.to_checksum_address(
        row["borrower_address"]
    )

    try:
        if int(row["snapshot_block"]) != SNAPSHOT_BLOCK:
            raise ValueError("Snapshot block mismatch")

        data = pool.functions.getUserAccountData(
            address
        ).call(
            block_identifier=SNAPSHOT_BLOCK
        )

        (
            collateral_raw,
            debt_raw,
            available_raw,
            threshold_raw,
            ltv_raw,
            hf_raw
        ) = data

        collateral = Decimal(
            collateral_raw
        ) / BASE_UNIT

        debt = Decimal(
            debt_raw
        ) / BASE_UNIT

        available = Decimal(
            available_raw
        ) / BASE_UNIT

        threshold = Decimal(
            threshold_raw
        ) / BPS_UNIT

        ltv = Decimal(ltv_raw) / BPS_UNIT

        if debt_raw == 0:
            hf = None
            status = "NO_DEBT"
        else:
            if hf_raw == MAX_UINT256:
                raise ValueError(
                    "Unexpected infinite HF with debt"
                )

            hf = Decimal(hf_raw) / HF_UNIT

            if hf < 1:
                status = "HF_BELOW_1"
            else:
                status = "HF_AT_LEAST_1"

        result = {
            "borrower_address": address,
            "snapshot_block": SNAPSHOT_BLOCK,
            "collateral_base_raw": collateral_raw,
            "debt_base_raw": debt_raw,
            "collateral_usd": str(collateral),
            "debt_usd": str(debt),
            "available_borrow_usd": str(available),
            "liquidation_threshold": str(threshold),
            "ltv": str(ltv),
            "health_factor_raw": hf_raw,
            "health_factor": (
                str(hf) if hf is not None else ""
            ),
            "status": status
        }

        results.append(result)

        print(
            f"[{index}/{len(borrowers)}] "
            f"{address[:10]}... "
            f"| COLLATERAL: {collateral:.4f} "
            f"| DEBT: {debt:.4f} "
            f"| HF: {hf} "
            f"| {status}"
        )

    except Exception as e:

        failed.append({
            "borrower_address": address,
            "error_type": type(e).__name__
        })

        print(
            f"[{index}/{len(borrowers)}] "
            f"ERROR {address}: "
            f"{type(e).__name__}"
        )

    time.sleep(0.3)

# ===== SAVE RESULTS =====

if results:
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=results[0].keys()
        )

        writer.writeheader()
        writer.writerows(results)

if failed:
    with open(
        "aave_positions_failed.csv",
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=failed[0].keys()
        )

        writer.writeheader()
        writer.writerows(failed)

# ===== SUMMARY =====

active = [
    r for r in results
    if Decimal(r["debt_usd"]) > 0
]

at_risk = [
    r for r in active
    if r["status"] == "HF_BELOW_1"
]

total_debt = sum(
    (Decimal(r["debt_usd"]) for r in active),
    Decimal(0)
)

total_collateral = sum(
    (Decimal(r["collateral_usd"]) for r in active),
    Decimal(0)
)

print("\n=== POSITION COLLECTION SUMMARY ===")
print("TOTAL QUERIED:", len(borrowers))
print("SUCCESS:", len(results))
print("FAILED:", len(failed))
print("ACTIVE BORROWERS:", len(active))
print("HF BELOW 1:", len(at_risk))
print("TOTAL ACTIVE COLLATERAL USD:", total_collateral)
print("TOTAL ACTIVE DEBT USD:", total_debt)
print("CSV SAVED:", OUTPUT_FILE if results else "NONE")
