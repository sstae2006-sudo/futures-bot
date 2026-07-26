import sqlite3

main = sqlite3.connect("market_data.db")
old = sqlite3.connect("market_data_backup.db")

old_rows = old.execute("""
SELECT 
product_code,
contract,
resolution,
timestamp,
open,
high,
low,
close,
volume,
source,
created_at
FROM bars
""")

cur = main.cursor()

inserted = 0
skipped = 0

for row in old_rows:
    try:
        cur.execute("""
        INSERT INTO bars
        (
        product_code,
        contract,
        resolution,
        timestamp,
        open,
        high,
        low,
        close,
        volume,
        source,
        created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, row)

        inserted += 1

    except sqlite3.IntegrityError:
        skipped += 1

    if inserted % 10000 == 0:
        main.commit()
        print("Inserted:", inserted)

main.commit()

print("DONE")
print("Inserted:", inserted)
print("Skipped:", skipped)