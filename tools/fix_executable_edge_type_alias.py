from pathlib import Path

path = Path("weatherbot/quoting/model.py")
content = path.read_text(encoding="utf-8")
content = content.replace("from typing import TypeAlias\n", "")
content = content.replace(
    "QuoteMetadataValue: TypeAlias = str | float | bool | None",
    "type QuoteMetadataValue = str | float | bool | None",
)
path.write_text(content, encoding="utf-8")
