import sqlite3
import re

DB = "market_data.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

rows = cur.execute("""
    SELECT DISTINCT product_code
    FROM bars
""").fetchall()

updated = 0

for (old_code,) in rows:
    # Already fixed
    if old_code in ("MES", "MNQ", "M2K"):
        continue

    # Extract root symbol
    match = re.match(r"([A-Z]+)", old_code)

    if not match:
        continue

    root = match.group(1)

    # Futures contract symbols are usually 1-3 letters
    if len(root) > 3:
        root = root[:2]

    cur.execute("""
        UPDATE bars
        SET 
            contract = product_code,
            product_code = ?
        WHERE product_code = ?
    """, (root, old_code))

    updated += cur.rowcount
    print(f"{old_code} -> {root}")

conn.commit()

print()
print(f"Updated {updated} bars")

conn.close()