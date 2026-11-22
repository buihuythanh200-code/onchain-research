
import csv
import time
from requests.exceptions import HTTPError
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput

RPC_URL = "https://rpc.mevblocker.io"
SNAPSHOT_BLOCK = 26402092

POOL_ADDRESS = Web3.to_checksum_address(
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
)

w3 = Web3(
    Web3.HTTPProvider(
        RPC_URL,
        request_kwargs={"timeout": 60}
    )
)

if not w3.is_connected():
    raise RuntimeError("RPC connection failed")

if w3.eth.chain_id != 1:
    raise RuntimeError("Wrong network")



def read_contract(address, name, inputs,
                  outputs, args=()):

    abi = [{
        "name": name,
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": f"arg{i}", "type": t}
            for i, t in enumerate(inputs)
        ],
        "outputs": [
            {"name": f"out{i}", "type": t}
            for i, t in enumerate(outputs)
        ]
    }]

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=abi
    )

    max_retries = 6

    for attempt in range(max_retries):

        try:
            # Reduce RPC request frequency.
            time.sleep(1.0)

            result = getattr(
                contract.functions, name
            )(*args).call(
                block_identifier=SNAPSHOT_BLOCK
            )

            return result

        except HTTPError as e:

            if (
                e.response is None
                or e.response.status_code != 429
            ):
                raise

            if attempt == max_retries - 1:
                raise

            wait_time = 5 * (2 ** attempt)

            retry_after = e.response.headers.get(
                "Retry-After", ""
            )

            if retry_after.isdigit():
                wait_time = max(
                    wait_time, int(retry_after)
                )

            print(
                f"RATE LIMIT | FUNCTION: {name} "
                f"| RETRY: {attempt + 1} "
                f"| WAIT: {wait_time}s"
            )

            time.sleep(wait_time)


def get_token_symbol(asset):

    try:
        # Standard ERC-20 string symbol.
        return read_contract(
            asset,
            "symbol",
            [],
            ["string"]
        )

    except BadFunctionCallOutput:

        # Legacy token bytes32 symbol.
        raw_symbol = read_contract(
            asset,
            "symbol",
            [],
            ["bytes32"]
        )

        symbol = raw_symbol.rstrip(
            b"\x00"
        ).decode("utf-8")

        print(
            f"SYMBOL FALLBACK: {symbol} "
            f"(bytes32)"
        )

        return symbol


print("=== AAVE V3 RESERVE COLLECTION ===")
print("CHAIN ID:", w3.eth.chain_id)
print("SNAPSHOT BLOCK:", SNAPSHOT_BLOCK)

# Obtain historical reserve list.
reserves = read_contract(
    POOL_ADDRESS,
    "getReservesList",
    [],
    ["address[]"]
)

# Obtain historical addresses provider.
provider = read_contract(
    POOL_ADDRESS,
    "ADDRESSES_PROVIDER",
    [],
    ["address"]
)

# Obtain protocol data provider.
data_provider = read_contract(
    provider,
    "getPoolDataProvider",
    [],
    ["address"]
)

# Obtain historical oracle.
oracle = read_contract(
    provider,
    "getPriceOracle",
    [],
    ["address"]
)

base_unit = read_contract(
    oracle,
    "BASE_CURRENCY_UNIT",
    [],
    ["uint256"]
)

print("TOTAL RESERVES:", len(reserves))
print("ADDRESSES PROVIDER:", provider)
print("DATA PROVIDER:", data_provider)
print("ORACLE:", oracle)
print("BASE CURRENCY UNIT:", base_unit)

rows = []

for i, asset in enumerate(reserves, 1):

    symbol = get_token_symbol(asset)

    decimals = read_contract(
        asset,
        "decimals",
        [],
        ["uint8"]
    )

    configuration = read_contract(
        POOL_ADDRESS,
        "getConfiguration",
        ["address"],
        ["uint256"],
        (asset,)
    )

    ltv_bps = configuration & 0xFFFF

    lt_bps = (
        configuration >> 16
    ) & 0xFFFF

    bonus_bps = (
        configuration >> 32
    ) & 0xFFFF

    price_raw = read_contract(
        oracle,
        "getAssetPrice",
        ["address"],
        ["uint256"],
        (asset,)
    )

    row = {
        "snapshot_block": SNAPSHOT_BLOCK,
        "asset_address": asset,
        "symbol": symbol,
        "decimals": decimals,
        "ltv_bps": ltv_bps,
        "liquidation_threshold_bps": lt_bps,
        "liquidation_bonus_bps": bonus_bps,
        "price_base_raw": price_raw,
        "base_currency_unit": base_unit,
        "data_provider_address": data_provider,
        "oracle_address": oracle
    }

    rows.append(row)

    print(
        f"[{i}/{len(reserves)}] "
        f"{symbol:12} "
        f"| LT: {lt_bps / 100:.2f}% "
        f"| BONUS PARAM: {bonus_bps} "
        f"| PRICE: {price_raw / base_unit:.6f}"
    )


output = "aave_reserves.csv"

with open(
    output,
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=rows[0].keys()
    )

    writer.writeheader()
    writer.writerows(rows)

print("\n=== RESERVE COLLECTION SUMMARY ===")
print("TOTAL RESERVES:", len(reserves))
print("SUCCESS:", len(rows))
print("CSV SAVED:", output)
