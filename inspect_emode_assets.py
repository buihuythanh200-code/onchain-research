
import csv
import time
from collections import defaultdict

from web3 import Web3
from requests.exceptions import HTTPError

RPC_URL = "https://rpc.mevblocker.io"
SNAPSHOT_BLOCK = 26402092

POOL = Web3.to_checksum_address(
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

INPUT_FILE = "aave_user_assets_full.csv"
OUTPUT_FILE = "aave_emode_asset_mapping.csv"

w3 = Web3(
    Web3.HTTPProvider(
        RPC_URL,
        request_kwargs={"timeout": 60}
    )
)

if not w3.is_connected() or w3.eth.chain_id != 1:
    raise RuntimeError("Ethereum RPC connection failed")

abi = [
    {
        "type": "function",
        "name": "getEModeCategoryCollateralBitmap",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "uint8"}],
        "outputs": [{"name": "", "type": "uint128"}]
    },
    {
        "type": "function",
        "name": "getEModeCategoryBorrowableBitmap",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "uint8"}],
        "outputs": [{"name": "", "type": "uint128"}]
    }
]

pool = w3.eth.contract(address=POOL, abi=abi)

with open(INPUT_FILE, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

categories = sorted({
    int(row["emode_category"])
    for row in rows
    if int(row["emode_category"]) > 0
})

reserve_names = {}

for row in rows:
    if int(row["snapshot_block"]) != SNAPSHOT_BLOCK:
        raise ValueError("Snapshot block mismatch")

    reserve_id = int(row["reserve_id"])
    symbol = row["symbol"]

    if (
        reserve_id in reserve_names
        and reserve_names[reserve_id] != symbol
    ):
        raise ValueError(
            f"Conflicting symbol at reserve {reserve_id}"
        )

    reserve_names[reserve_id] = symbol


def rpc_call(function_name, category):
    for attempt in range(6):
        try:
            time.sleep(1)

            function = getattr(
                pool.functions, function_name
            )(category)

            return function.call(
                block_identifier=SNAPSHOT_BLOCK
            )

        except HTTPError as exc:
            if (
                exc.response is None
                or exc.response.status_code != 429
                or attempt == 5
            ):
                raise

            delay = 5 * (2 ** attempt)
            print(f"RATE LIMIT: wait {delay}s")
            time.sleep(delay)


def symbols_from_bitmap(bitmap):
    return [
        reserve_names.get(i, f"RESERVE_ID_{i}")
        for i in range(128)
        if (bitmap >> i) & 1
    ]


print("=== E-MODE ASSET MAPPING ===")
print("SNAPSHOT:", SNAPSHOT_BLOCK)

results = []

for category in categories:
    collateral_bitmap = rpc_call(
        "getEModeCategoryCollateralBitmap",
        category
    )

    borrowable_bitmap = rpc_call(
        "getEModeCategoryBorrowableBitmap",
        category
    )

    collateral_assets = symbols_from_bitmap(
        collateral_bitmap
    )

    borrowable_assets = symbols_from_bitmap(
        borrowable_bitmap
    )

    results.append({
        "snapshot_block": SNAPSHOT_BLOCK,
        "emode_category": category,
        "collateral_bitmap": str(collateral_bitmap),
        "borrowable_bitmap": str(borrowable_bitmap),
        "collateral_assets": "|".join(collateral_assets),
        "borrowable_assets": "|".join(borrowable_assets)
    })

    print(f"\nCATEGORY: {category}")
    print(
        "COLLATERAL ASSETS:",
        ", ".join(collateral_assets) or "NONE"
    )
    print(
        "BORROWABLE ASSETS:",
        ", ".join(borrowable_assets) or "NONE"
    )


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

print("\n=== SUMMARY ===")
print("CATEGORIES:", len(results))
print("CSV SAVED:", OUTPUT_FILE)
