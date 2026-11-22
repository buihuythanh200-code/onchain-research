
import csv
import time
from requests.exceptions import HTTPError
from web3 import Web3

RPC_URL = "https://rpc.mevblocker.io"
SNAPSHOT_BLOCK = 26402092

POOL = Web3.to_checksum_address(
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

UNKNOWN_IDS = [29, 31, 66]

w3 = Web3(Web3.HTTPProvider(
    RPC_URL,
    request_kwargs={"timeout": 60}
))

if not w3.is_connected() or w3.eth.chain_id != 1:
    raise RuntimeError("Ethereum connection failed")

abi = [{
    "name": "getReserveAddressById",
    "type": "function",
    "stateMutability": "view",
    "inputs": [
        {"name": "id", "type": "uint16"}
    ],
    "outputs": [
        {"name": "", "type": "address"}
    ]
}]

pool = w3.eth.contract(address=POOL, abi=abi)

with open("aave_reserves.csv", encoding="utf-8") as f:
    reserves = list(csv.DictReader(f))

reserve_by_address = {
    r["asset_address"].lower(): r
    for r in reserves
}

with open(
    "aave_emode_asset_mapping.csv",
    encoding="utf-8"
) as f:
    mapping_rows = list(csv.DictReader(f))

resolved = {}

print("=== RESOLVE UNKNOWN RESERVE IDS ===")

for reserve_id in UNKNOWN_IDS:

    for attempt in range(6):
        try:
            time.sleep(1.5)

            address = pool.functions.getReserveAddressById(
                reserve_id
            ).call(block_identifier=SNAPSHOT_BLOCK)

            break

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

    asset = reserve_by_address.get(address.lower())

    if asset is None:
        raise RuntimeError(
            f"Reserve {reserve_id} not found "
            f"in aave_reserves.csv: {address}"
        )

    resolved[reserve_id] = asset["symbol"]

    print(
        f"RESERVE ID {reserve_id}"
        f" | TOKEN: {asset['symbol']}"
        f" | ADDRESS: {address}"
    )

# Replace unresolved labels in the mapping CSV.
for row in mapping_rows:

    if int(row["snapshot_block"]) != SNAPSHOT_BLOCK:
        raise ValueError("Snapshot mismatch")

    for reserve_id, symbol in resolved.items():

        placeholder = f"RESERVE_ID_{reserve_id}"

        for field in (
            "collateral_assets",
            "borrowable_assets"
        ):
            values = row[field].split("|")
            values = [
                symbol if x == placeholder else x
                for x in values
            ]
            row[field] = "|".join(values)

output = "aave_emode_asset_mapping_resolved.csv"

with open(
    output,
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=mapping_rows[0].keys()
    )

    writer.writeheader()
    writer.writerows(mapping_rows)

print("\n=== SUMMARY ===")
print("UNKNOWN IDS:", len(UNKNOWN_IDS))
print("RESOLVED:", len(resolved))
print("CSV SAVED:", output)
