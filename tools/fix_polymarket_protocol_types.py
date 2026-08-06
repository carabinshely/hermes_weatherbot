from __future__ import annotations

from pathlib import Path

path = Path("weatherbot/polymarket/read_client.py")
content = path.read_text(encoding="utf-8")
replacements = {
    "from decimal import Decimal\n": "from datetime import datetime\nfrom decimal import Decimal\n",
    "    end_date: object | None\n": "    end_date: datetime | None\n",
    "    timestamp: object | None\n": "    timestamp: datetime | None\n",
    "            end_date=cast(object, end_date),\n": "            end_date=end_date,\n",
    "                timestamp=cast(object, book.timestamp),\n": "                timestamp=book.timestamp,\n",
}
for old, new in replacements.items():
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"expected one marker {old!r}, found {count}")
    content = content.replace(old, new, 1)
path.write_text(content, encoding="utf-8")
