from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bot_v3.py",
    '''    MarketEventSnapshot,
    evaluate_executable_buy,
''',
    '''    MarketEventSnapshot,
    ValidatedExecutableQuote,
    evaluate_executable_buy,
''',
)
replace_once(
    "bot_v3.py",
    '''def place_buy_order(
    market_id: str, token_id: str, price: float, shares: float, private_key: str, wallet: str
) -> dict:
    """
    Place a BUY order on Polymarket CLOB.
    Uses FOK (Fill-Or-Kill) market order to guarantee execution.
    Returns dict with success status and details.
    Uses _timeout_call to prevent indefinite hangs.
    Balance check is done on-chain — we always attempt the order for consistency.
    """
    cost = round(shares * price, 4)

    if not is_approved(USDC_ADDRESS, ROUTER, wallet):
        return {"success": False, "reason": "Router approval missing"}

    # --- Market order via CLOB (with 10s timeout) ---
    order_args = MarketOrderArgs(
        token_id=token_id,
        amount=cost,  # For BUY: amount is in dollars (USDC)
        side="BUY",
        price=price,
    )
''',
    '''def place_buy_order(
    market_id: str,
    validated_quote: ValidatedExecutableQuote,
    private_key: str,
    wallet: str,
) -> dict:
    """Submit exactly the already-validated token, notional, shares, and price limit."""
    quote = validated_quote.quote
    token_id = str(quote.token_id)
    amount = float(quote.total_cost)
    price_limit = float(quote.worst_price)
    shares = float(quote.shares)

    if not is_approved(USDC_ADDRESS, ROUTER, wallet):
        return {"success": False, "reason": "Router approval missing"}

    # For BUY, amount is the validated displayed-book notional. The worst executable
    # price is a limit, not a multiplier used to reconstruct or enlarge that amount.
    order_args = MarketOrderArgs(
        token_id=token_id,
        amount=amount,
        side="BUY",
        price=price_limit,
    )
''',
)
replace_once(
    "bot_v3.py",
    '''        "price": price,
        "shares": shares,
        "cost": cost,
''',
    '''        "price": price_limit,
        "shares": shares,
        "cost": amount,
        "all_in_cost": float(validated_quote.total_all_in_cost),
        "quote_fingerprint": validated_quote.fingerprint,
''',
)
replace_once(
    "bot_v3.py",
    '''                callback=lambda: place_buy_order(
                    market_id=best_signal["market_id"],
                    token_id=best_signal["token_id"],
                    price=best_signal["worst_price"],
                    shares=best_signal["shares"],
                    private_key=PK,
                    wallet=WALLET,
                ),
''',
    '''                callback=lambda: place_buy_order(
                    market_id=best_signal["market_id"],
                    validated_quote=validated_quote,
                    private_key=PK,
                    wallet=WALLET,
                ),
''',
)

path = Path("tests/quoting/test_scanner_integration.py")
content = path.read_text(encoding="utf-8")
content = content.replace(
    '''    assert 'price=best_signal["worst_price"]' in source
''',
    '''    assert "validated_quote=validated_quote" in source


def test_live_order_boundary_does_not_reconstruct_notional_from_price() -> None:
    source = (ROOT / "bot_v3.py").read_text(encoding="utf-8")
    start = source.index("def place_buy_order")
    end = source.index("\n\ndef cancel_order", start)
    block = source[start:end]
    assert "validated_quote: ValidatedExecutableQuote" in block
    assert "amount = float(quote.total_cost)" in block
    assert "price_limit = float(quote.worst_price)" in block
    assert "shares * price" not in block
    assert "amount=amount" in block
''',
)
path.write_text(content, encoding="utf-8")
