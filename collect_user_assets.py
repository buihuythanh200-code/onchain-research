import os
import csv
import time
from decimal import Decimal, getcontext
from collections import defaultdict
from requests.exceptions import HTTPError, ConnectionError, Timeout
from web3 import Web3

getcontext().prec = 60
D = Decimal
RPC_URL = "https://rpc.mevblocker.io"
SNAPSHOT_BLOCK = 26402092
POOL = Web3.to_checksum_address("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
ZERO_ADDRESS = "0x" + "0" * 40
LIMIT = 530
RESERVES_FILE = "aave_reserves.csv"
POSITIONS_FILE = "aave_position_risk_analysis.csv"
OUTPUT_FILE = "aave_user_assets_full.csv"
TOLERANCE = D("0.00001")


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError("RPC connection failed")
    if w3.eth.chain_id != 1:
        raise RuntimeError("Wrong network")

    def read_contract(address, name, inputs, outputs, args=()):
        abi = [{
            "name": name, "type": "function", "stateMutability": "view",
            "inputs": [{"name": f"arg{i}", "type": t} for i, t in enumerate(inputs)],
            "outputs": [{"name": f"out{i}", "type": t} for i, t in enumerate(outputs)]
        }]
        contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
        for attempt in range(6):
            time.sleep(1)
            try:
                return getattr(contract.functions, name)(*args).call(block_identifier=SNAPSHOT_BLOCK)
            except (HTTPError, ConnectionError, Timeout) as e:
                response = getattr(e, "response", None)
                if isinstance(e, HTTPError) and (response is None or response.status_code not in (429, 500, 502, 503, 504)):
                    raise
                if attempt == 5:
                    raise
                wait = 5 * 2**attempt
                if response is not None:
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                print(f"RPC RETRY | {name} | {type(e).__name__} | WAIT {wait}s", flush=True)
                time.sleep(wait)

    with open(RESERVES_FILE, encoding="utf-8") as f:
        reserves = list(csv.DictReader(f))
    if not reserves or any(int(r["snapshot_block"]) != SNAPSHOT_BLOCK for r in reserves):
        raise RuntimeError("Empty reserves or reserve snapshot mismatch")
    reserve_map = {r["asset_address"].lower(): r for r in reserves}
    if len(reserve_map) != len(reserves):
        raise RuntimeError("Duplicate reserve addresses")
    data_provider = reserves[0]["data_provider_address"]
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        borrowers = sorted(csv.DictReader(f), key=lambda r: D(r["health_factor"]))[:LIMIT]
    borrower_map = {r["borrower_address"].lower(): r for r in borrowers}
    if not borrowers or len(borrower_map) != len(borrowers):
        raise RuntimeError("Empty borrowers or duplicate addresses")
    if any(int(r["snapshot_block"]) != SNAPSHOT_BLOCK for r in borrowers):
        raise RuntimeError("Borrower snapshot mismatch")

    reserve_id_cache = {}
    def get_asset_by_id(reserve_id):
        if reserve_id not in reserve_id_cache:
            reserve_id_cache[reserve_id] = read_contract(POOL, "getReserveAddressById", ["uint16"], ["address"], (reserve_id,))
        return reserve_id_cache[reserve_id]

    def reconcile(user_rows, borrower):
        if not user_rows:
            raise RuntimeError("No asset records")
        if len({r["asset_address"].lower() for r in user_rows}) != len(user_rows):
            raise RuntimeError("Duplicate asset records")
        collateral = sum((D(r["collateral_usd"]) for r in user_rows), D(0))
        debt = sum((D(r["debt_usd"]) for r in user_rows), D(0))
        ce = abs(collateral - D(borrower["collateral_usd"]))
        de = abs(debt - D(borrower["debt_usd"]))
        if ce > TOLERANCE or de > TOLERANCE:
            raise RuntimeError(f"Account reconciliation failed: {borrower['borrower_address']} | collateral difference={ce} | debt difference={de}")
        return ce, de

    results = []
    completed_users = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            results = list(csv.DictReader(f))
        grouped = defaultdict(list)
        for row in results:
            if int(row["snapshot_block"]) != SNAPSHOT_BLOCK:
                raise RuntimeError("Checkpoint snapshot mismatch")
            user_key = row["borrower_address"].lower()
            if user_key not in borrower_map:
                raise RuntimeError("Checkpoint contains unexpected borrower")
            grouped[user_key].append(row)
        for user_key, user_rows in grouped.items():
            reconcile(user_rows, borrower_map[user_key])
            completed_users.add(user_key)

    def save_checkpoint():
        if not results:
            return
        temp_file = OUTPUT_FILE + ".tmp"
        with open(temp_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, OUTPUT_FILE)

    print("=== USER ASSET COLLECTION ===")
    print("SNAPSHOT BLOCK:", SNAPSHOT_BLOCK)
    print("TOTAL RESERVES:", len(reserves))
    print("BORROWERS TO QUERY:", len(borrowers))
    print("ALREADY COMPLETED:", len(completed_users))

    for index, borrower in enumerate(borrowers, 1):
        user = Web3.to_checksum_address(borrower["borrower_address"])
        if user.lower() in completed_users:
            print(f"[{index}/{len(borrowers)}] SKIPPED: {user}")
            continue
        user_rows = []
        configuration = read_contract(POOL, "getUserConfiguration", ["address"], ["uint256"], (user,))
        emode = read_contract(POOL, "getUserEMode", ["address"], ["uint256"], (user,))
        related_ids = [i for i in range(128) if (configuration >> (2 * i)) & 3]
        print(f"\n[{index}/{len(borrowers)}] USER: {user}")
        print("HEALTH FACTOR:", borrower["health_factor"])
        print("E-MODE:", emode)
        print("RELATED RESERVES:", len(related_ids))
        for reserve_id in related_ids:
            asset = get_asset_by_id(reserve_id)
            if asset.lower() == ZERO_ADDRESS or asset.lower() not in reserve_map:
                raise RuntimeError(f"Unknown reserve ID {reserve_id}: {asset}")
            reserve = reserve_map[asset.lower()]
            data = read_contract(data_provider, "getUserReserveData", ["address", "address"],
                                 ["uint256"] * 7 + ["uint40", "bool"], (asset, user))
            supplied_raw, stable_debt_raw, variable_debt_raw = data[:3]
            collateral_enabled = data[8]
            bitmap_collateral = bool((configuration >> (2 * reserve_id + 1)) & 1)
            bitmap_borrowing = bool((configuration >> (2 * reserve_id)) & 1)
            if collateral_enabled != bitmap_collateral:
                raise RuntimeError("Collateral flag mismatch")
            unit = D(10) ** int(reserve["decimals"])
            price = D(reserve["price_base_raw"]) / D(reserve["base_currency_unit"])
            supplied_amount = D(supplied_raw) / unit
            debt_amount = D(stable_debt_raw + variable_debt_raw) / unit
            collateral_amount = supplied_amount if collateral_enabled else D(0)
            collateral_usd = collateral_amount * price
            debt_usd = debt_amount * price
            user_rows.append({
                "snapshot_block": SNAPSHOT_BLOCK,
                "borrower_address": user,
                "emode_category": emode,
                "reserve_id": reserve_id,
                "asset_address": asset,
                "symbol": reserve["symbol"],
                "decimals": reserve["decimals"],
                "price_usd": str(price),
                "supplied_raw": supplied_raw,
                "stable_debt_raw": stable_debt_raw,
                "variable_debt_raw": variable_debt_raw,
                "collateral_enabled": collateral_enabled,
                "borrowing_flag": bitmap_borrowing,
                "supplied_amount": str(supplied_amount),
                "collateral_amount": str(collateral_amount),
                "debt_amount": str(debt_amount),
                "collateral_usd": str(collateral_usd),
                "debt_usd": str(debt_usd),
                "base_lt_bps": reserve["liquidation_threshold_bps"],
                "base_bonus_bps": reserve["liquidation_bonus_bps"]
            })
            print(f"  {reserve['symbol']:12} | COLLATERAL: {collateral_usd:,.4f} USD | DEBT: {debt_usd:,.4f} USD")
        ce, de = reconcile(user_rows, borrower)
        print("COLLATERAL DIFFERENCE:", ce)
        print("DEBT DIFFERENCE:", de)
        results.extend(user_rows)
        save_checkpoint()
        completed_users.add(user.lower())
        print(f"CHECKPOINT SAVED: {len(completed_users)}/{len(borrowers)}", flush=True)

    print("\n=== USER ASSET COLLECTION SUMMARY ===")
    print("BORROWERS TARGETED:", len(borrowers))
    print("COMPLETED BORROWERS:", len(completed_users))
    print("REMAINING BORROWERS:", len(borrowers) - len(completed_users))
    print("ASSET RECORDS:", len(results))
    print("RESERVE IDS CACHED:", len(reserve_id_cache))
    print("CSV SAVED:", OUTPUT_FILE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user. Previously saved checkpoints are preserved.")
        raise SystemExit(130)
