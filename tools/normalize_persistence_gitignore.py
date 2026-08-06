from __future__ import annotations

import subprocess
from pathlib import Path

original = subprocess.run(
    ["git", "show", "origin/main:.gitignore"],
    check=True,
    capture_output=True,
    text=True,
).stdout
marker = "cover/\n\n# Translations\n"
addition = """cover/

# Runtime financial state and SQLite sidecars
state/
*.sqlite
*.sqlite3
*.sqlite-journal
*.sqlite3-journal
*.sqlite-wal
*.sqlite3-wal
*.sqlite-shm
*.sqlite3-shm

# Translations
"""
if original.count(marker) != 1:
    raise SystemExit(f"gitignore marker count: {original.count(marker)}")
Path(".gitignore").write_text(original.replace(marker, addition), encoding="utf-8")
