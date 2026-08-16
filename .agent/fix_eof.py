from pathlib import Path

path = Path("docs/METEOGRAM.md")
path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
