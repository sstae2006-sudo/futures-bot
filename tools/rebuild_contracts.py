import sqlite3
import re

DB="market_data.db"

conn=sqlite3.connect(DB)
cur=conn.cursor()

cur.execute("""
CREATE TABLE bars_new AS
SELECT * FROM bars WHERE 0
""")

rows=cur.execute("""
SELECT 
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
""").fetchall()

inserted=0
skipped=0

seen=set()

for row in rows:
    old_contract=row[0]

    m=re.match(r"([A-Z]+)", old_contract)

    if not m:
        skipped+=1
        continue

    product=m.group(1)

    if len(product)>3:
        product=product[:2]

    key=(product,row[1],row[2])

    # remove collisions
    if key in seen:
        skipped+=1
        continue

    seen.add(key)

    cur.execute("""
    INSERT INTO bars_new
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
    """,
    (
    product,
    old_contract,
    *row[1:]
    ))

    inserted+=1


cur.execute("DROP TABLE bars")
cur.execute("ALTER TABLE bars_new RENAME TO bars")

conn.commit()

print("Inserted:",inserted)
print("Skipped duplicates:",skipped)

conn.close()