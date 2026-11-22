
import os
import csv
import time
import requests

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BLOCKSCOUT_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "Missing BLOCKSCOUT_API_KEY in .env"
    )

API_URL = "https://api.blockscout.com/v2/api"

AAVE_POOL = (
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

SNAPSHOT_BLOCK = 26402092
FROM_BLOCK = SNAPSHOT_BLOCK - 4999
TO_BLOCK = SNAPSHOT_BLOCK

CHUNK_SIZE = 500

BORROW_SIGNATURE = (
    "Borrow(address,address,address,"
    "uint256,uint8,uint256,uint16)"
)

BORROW_TOPIC = (
    "0x" + Web3.keccak(
        text=BORROW_SIGNATURE
    ).hex().removeprefix("0x")
)

session = requests.Session()

borrowers = set()
total_events = 0


def fetch_logs(start, end):
    params = {
        "chain_id": "1",
        "module": "logs",
        "action": "getLogs",
        "address": AAVE_POOL,
        "fromBlock": start,
        "toBlock": end,
        "topic0": BORROW_TOPIC,
        "apikey": API_KEY
    }

    response = session.get(
        API_URL,
        params=params,
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    logs = data.get("result")

    if data.get("status") == "1":
        if not isinstance(logs, list):
            raise RuntimeError(
                "Unexpected log response"
            )

        # Split large responses to reduce
        # the risk of API result truncation.
        if len(logs) >= 1000:
            if start == end:
                raise RuntimeError(
                    f"Possible truncation at block {start}"
                )

            mid = (start + end) // 2

            return (
                fetch_logs(start, mid)
                + fetch_logs(mid + 1, end)
            )

        return logs

    message = str(data.get("message", "")).lower()
    result_text = str(logs).lower()

    if (
        "no logs found" in message
        or "no records found" in message
        or "no logs found" in result_text
        or "no records found" in result_text
    ):
        return []

    raise RuntimeError(
        f"Blockscout response error: "
        f"{data.get('message')}"
    )


print("=== AAVE V3 BORROWER COLLECTION ===")
print("SNAPSHOT BLOCK:", SNAPSHOT_BLOCK)
print("FROM BLOCK:", FROM_BLOCK)
print("TO BLOCK:", TO_BLOCK)
print("BORROW TOPIC:", BORROW_TOPIC)

for start in range(
    FROM_BLOCK, TO_BLOCK + 1, CHUNK_SIZE
):
    end = min(
        start + CHUNK_SIZE - 1,
        TO_BLOCK
    )

    logs = fetch_logs(start, end)

    total_events += len(logs)

    for log in logs:
        topics = log.get("topics", [])

        if len(topics) != 4:
            raise RuntimeError(
                "Unexpected Borrow event format"
            )

        # topics[2] = indexed onBehalfOf
        borrower = Web3.to_checksum_address(
            "0x" + topics[2][-40:]
        )

        borrowers.add(borrower)

    print(
        f"BLOCKS {start} - {end}"
        f" | EVENTS: {len(logs)}"
        f" | UNIQUE BORROWERS: {len(borrowers)}"
    )

    time.sleep(0.3)


output = "aave_borrowers.csv"

with open(
    output,
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.writer(f)

    writer.writerow([
        "borrower_address",
        "snapshot_block"
    ])

    for borrower in sorted(borrowers):
        writer.writerow([
            borrower,
            SNAPSHOT_BLOCK
        ])


print("\n=== COLLECTION SUMMARY ===")
print("TOTAL BORROW EVENTS:", total_events)
print("UNIQUE BORROWERS:", len(borrowers))
print("CSV SAVED:", output)
