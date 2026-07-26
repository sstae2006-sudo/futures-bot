import sqlite3

main = sqlite3.connect("market_data.db")
backup = sqlite3.connect("market_data_backup.db")

mc = main.cursor()
bc = backup.cursor()

rows = bc.execute("""
SELECT product_code, contract, resolution,
timestamp,
open, high, low, close,
volume, source, created_at
FROM bars
WHERE product_code IN ('CL','GC','US','SP','HG')
""").fetchall()

print("Old rows found:", len(rows))

inserted = 0

for r in rows:
    try:
        mc.execute("""
        INSERT INTO bars
        (product_code, contract, resolution, timestamp,
         open, high, low, close, volume, source, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, r)

        inserted += 1

    except sqlite3.IntegrityError:
        pass

main.commit()

print("Inserted:", inserted)