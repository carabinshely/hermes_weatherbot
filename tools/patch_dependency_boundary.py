from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


# The official SDK is imported only when its optional client is constructed.
replace_once(
    "weatherbot/polymarket/read_client.py",
    "from datetime import datetime\nfrom decimal import Decimal\n",
    "from datetime import datetime\nfrom decimal import Decimal\nfrom importlib import import_module\n",
)
replace_once(
    "weatherbot/polymarket/read_client.py",
    "\nfrom polymarket import PublicClient\n\nfrom weatherbot.polymarket.errors import MarketDataUnavailable\n",
    "\nfrom weatherbot.dependencies import require_live_dependencies, require_module_attribute\n"
    "from weatherbot.polymarket.errors import MarketDataUnavailable\n",
)
replace_once(
    "weatherbot/polymarket/read_client.py",
    '''class OfficialPolymarketReadClient:
    """Normalize public SDK objects into repository-owned immutable models."""
''',
    '''def _default_public_client() -> PublicSdkClient:
    require_live_dependencies()
    module = import_module("polymarket")
    factory = cast(
        Callable[[], object],
        require_module_attribute(module, "PublicClient"),
    )
    return cast(PublicSdkClient, factory())


class OfficialPolymarketReadClient:
    """Normalize public SDK objects into repository-owned immutable models."""
''',
)
replace_once(
    "weatherbot/polymarket/read_client.py",
    "            factory = client_factory or (lambda: cast(PublicSdkClient, PublicClient()))\n",
    "            factory = client_factory or _default_public_client\n",
)

# The legacy CLI must import in research and paper installations without wallet packages.
replace_once(
    "bot_v3.py",
    "from runtime_security import credential_status_line\n",
    "from runtime_security import credential_status_line\n"
    "from weatherbot.dependencies import (\n"
    "    LiveDependenciesUnavailable,\n"
    "    require_live_dependencies,\n"
    ")\n",
)
replace_once(
    "bot_v3.py",
    '''from web3 import Web3
from eth_account import Account

_w3: Web3 = None


def get_w3() -> Web3:
    global _w3
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider("https://1rpc.io/matic"))
    return _w3
''',
    '''_w3: Any = None


def _web3_class():
    require_live_dependencies()
    from web3 import Web3

    return Web3


def get_w3():
    global _w3
    if _w3 is None:
        web3_class = _web3_class()
        _w3 = web3_class(web3_class.HTTPProvider("https://1rpc.io/matic"))
    return _w3
''',
)
replace_once(
    "bot_v3.py",
    '''    w3 = get_w3()
    wallet_checksum = Web3.to_checksum_address(wallet)
    usdc_checksum = Web3.to_checksum_address(USDC_ADDRESS)
''',
    '''    w3 = get_w3()
    web3_class = _web3_class()
    wallet_checksum = web3_class.to_checksum_address(wallet)
    usdc_checksum = web3_class.to_checksum_address(USDC_ADDRESS)
''',
)
replace_once(
    "bot_v3.py",
    '''    w3 = get_w3()
    bal = w3.eth.get_balance(Web3.to_checksum_address(wallet))
''',
    '''    w3 = get_w3()
    bal = w3.eth.get_balance(_web3_class().to_checksum_address(wallet))
''',
)
replace_once(
    "bot_v3.py",
    '''    usdc = w3.eth.contract(address=Web3.to_checksum_address(token), abi=usdc_abi)
    try:
        allowance = usdc.functions.allowance(
            Web3.to_checksum_address(wallet), Web3.to_checksum_address(spender)
        ).call()
''',
    '''    web3_class = _web3_class()
    usdc = w3.eth.contract(address=web3_class.to_checksum_address(token), abi=usdc_abi)
    try:
        allowance = usdc.functions.allowance(
            web3_class.to_checksum_address(wallet),
            web3_class.to_checksum_address(spender),
        ).call()
''',
)
replace_once(
    "bot_v3.py",
    '''    usdc = w3.eth.contract(address=Web3.to_checksum_address(token), abi=usdc_abi)
    nonce = get_nonce(wallet)
    build = usdc.functions.approve(Web3.to_checksum_address(spender), amount_wei).build_transaction(
''',
    '''    web3_class = _web3_class()
    usdc = w3.eth.contract(address=web3_class.to_checksum_address(token), abi=usdc_abi)
    nonce = get_nonce(wallet)
    build = usdc.functions.approve(
        web3_class.to_checksum_address(spender), amount_wei
    ).build_transaction(
''',
)
replace_once(
    "bot_v3.py",
    '''    if context.mode is ExecutionMode.LIVE:
        print(credential_status_line())
        if not PK or not WALLET:
''',
    '''    if context.mode is ExecutionMode.LIVE:
        try:
            require_live_dependencies()
        except LiveDependenciesUnavailable as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(credential_status_line())
        if not PK or not WALLET:
''',
)

# Replace the stale pip/requirements installation with explicit uv profiles.
replace_once(
    "README.md",
    '''```bash
git clone https://github.com/nicolastinkl/hermes_weatherbot.git
cd hermes_weatherbot
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
''',
    '''Install [uv](https://docs.astral.sh/uv/) and choose the smallest dependency profile
that matches the execution mode:

```bash
git clone https://github.com/carabinshely/hermes_weatherbot.git
cd hermes_weatherbot

# Research, paper, resolution, and observation tooling only.
# No wallet, Web3, signing, or official SDK packages are installed.
uv sync --locked --no-dev

# Development and tests, still without live extras.
uv sync --locked --all-groups

# Explicit live-capable environment. Funded-wallet operation remains fail-closed.
uv sync --locked --no-dev --extra live
```

Run commands through the selected locked environment, for example:

```bash
uv run --no-dev python bot_v3.py scan --mode research
uv run --no-dev python bot_v3.py scan --mode paper
uv run --no-dev python -m weatherbot.resolution --help
```
''',
)
replace_once(
    "README.md",
    '''# Live mode is fail-closed and requires all three gates:
# 1. config.json mode=live
# 2. --mode live
# 3. --confirm-live
python bot_v3.py scan --mode live --confirm-live
```

Research and paper modes do not require `PK` or `WALLET`.
''',
    '''# Live mode is fail-closed and requires the live extra plus all three gates:
# 1. uv sync --locked --no-dev --extra live
# 2. config.json mode=live
# 3. --mode live
# 4. --confirm-live
uv run --no-dev --extra live python bot_v3.py scan --mode live --confirm-live
```

Research and paper modes do not require `PK`, `WALLET`, Web3, signing packages, or the
optional official SDK. A live command in a minimal environment exits with an actionable
installation error before credential or wallet access.
''',
)
