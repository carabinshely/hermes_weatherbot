from pathlib import Path

path = Path("bot_v3.py")
content = path.read_text(encoding="utf-8")

old_status = '''    print(f"  Mode:     {context.label}")
    if context.mode is ExecutionMode.LIVE:
        print(f"  Wallet:   {WALLET[:8]}...{WALLET[-4:]}")
    else:
        print("  Wallet:   disabled")
    print(f"  USDC.e:    ${balance:.4f}")
'''
new_status = '''    print(f"  Mode:      {context.label}")
    print(f"  Wallet:    {WALLET[:8]}...{WALLET[-4:]}")
    print(f"  USDC.e:    ${balance:.4f}")
'''

old_loop = '''    print("=" * 60)
    print(f"  Wallet:    {WALLET[:8]}...{WALLET[-4:]}")
    print(f"  Cities:   {len(LOCATIONS)}")
'''
new_loop = '''    print("=" * 60)
    print(f"  Mode:     {context.label}")
    if context.mode is ExecutionMode.LIVE:
        print(f"  Wallet:   {WALLET[:8]}...{WALLET[-4:]}")
    else:
        print("  Wallet:   disabled")
    print(f"  Cities:   {len(LOCATIONS)}")
'''

for old, new, label in (
    (old_status, new_status, "status"),
    (old_loop, new_loop, "run loop"),
):
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label} marker count: {count}")
    content = content.replace(old, new, 1)

path.write_text(content, encoding="utf-8")
