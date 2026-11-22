
import csv
import time
from collections import Counter

from web3 import Web3

RPC_URL = "https://rpc.mevblocker.io"
SNAPSHOT_BLOCK = 26402092

POOL = Web3.to_checksum_address(
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

INPUT_FILE = "aave_user_assets_full.csv"
OUTPUT_FILE = "aave_emode_categories.csv"

# Load completed user asset records.
with open(INPUT_FILE, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

users = {}

for row in rows:
    if int(row["snapshot_block"]) != SNAPSHOT_BLOCK:
        raise ValueError("Snapshot mismatch")

    address = row["borrower_address"].lower()
    category = int(row["emode_category"])

    if address in users and users[address] != category:
        raise ValueError("Conflicting E-mode categories")

    users[address] = category

counts = Counter(users.values())

print("=== USER E-MODE DISTRIBUTION ===")
print("UNIQUE BORROWERS:", len(users))

for category, count in sorted(counts.items()):
    print(f"E-MODE {category}: {count} borrowers")

# Read historical E-mode configuration.
w3 = Web3(
    Web3.HTTPProvider(
        RPC_URL,
        request_kwargs={"timeout": 60}
    )
)

if not w3.is_connected() or w3.eth.chain_id != 1:
    raise RuntimeError("Ethereum RPC connection failed")

abi = [{
    "type": "function",
    "name": "getEModeCategoryData",
    "stateMutability": "view",
    "inputs": [
        {"name": "id", "type": "uint8"}
    ],
    "outputs": [{
        "name": "",
        "type": "tuple",
        "components": [
            {"name": "ltv", "type": "uint16"},
            {
                "name": "liquidationThreshold",
                "type": "uint16"
            },
            {
                "name": "liquidationBonus",
                "type": "uint16"
            },
            {"name": "priceSource", "type": "address"},
            {"name": "label", "type": "string"}
        ]
    }]
}]

pool = w3.eth.contract(address=POOL, abi=abi)

output_rows = []

print("\n=== HISTORICAL E-MODE CONFIG ===")

for category, count in sorted(counts.items()):
    if category == 0:
        print("E-MODE 0: Default reserve configuration")
        continue

    # A light delay to reduce RPC rate limits.
    time.sleep(2)

    try:
        data = pool.functions.getEModeCategoryData(
            category
        ).call(block_identifier=SNAPSHOT_BLOCK)

    except Exception as exc:
        print(
            f"E-MODE {category}: QUERY FAILED "
            f"({type(exc).__name__}: {exc})"
        )
        raise

    ltv, threshold, bonus, price_source, label = data

    record = {
        "snapshot_block": SNAPSHOT_BLOCK,
        "emode_category": category,
        "borrower_count": count,
        "label": label,
        "ltv_bps": ltv,
        "liquidation_threshold_bps": threshold,
        "liquidation_bonus_bps": bonus,
        "price_source": price_source
    }

    output_rows.append(record)

    print(f"\nCATEGORY: {category}")
    print("LABEL:", label)
    print("BORROWERS:", count)
    print("LTV:", ltv / 100, "%")
    print("LIQUIDATION THRESHOLD:", threshold / 100, "%")
    print("LIQUIDATION BONUS PARAM:", bonus)
    print("PRICE SOURCE:", price_source)

if output_rows:
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=output_rows[0].keys()
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print("\nCSV SAVED:", OUTPUT_FILE)

print("\n=== SUMMARY ===")
print("BORROWERS:", len(users))
print("E-MODE CATEGORIES USED:", len(counts))
print("DONE")
