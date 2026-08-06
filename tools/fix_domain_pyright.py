from pathlib import Path

path = Path("weatherbot/domain/reducers.py")
content = path.read_text(encoding="utf-8")
old = '''    elif isinstance(event, MarketResolved):
        next_state = _apply_resolution(state, event)
    elif isinstance(event, PositionSettled):
        next_state = _apply_settlement(state, event)
    else:
        raise TypeError(f"unsupported domain event: {type(event).__name__}")
'''
new = '''    elif isinstance(event, MarketResolved):
        next_state = _apply_resolution(state, event)
    else:
        next_state = _apply_settlement(state, event)
'''
count = content.count(old)
if count != 1:
    raise SystemExit(f"event union marker count: {count}")
path.write_text(content.replace(old, new), encoding="utf-8")
