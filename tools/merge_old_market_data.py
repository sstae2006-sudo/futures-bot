import sqlite3

NEW_DB = "market_data.db"
OLD_DB = "market_data_backup.db"

new = sqlite3.connect(NEW_DB)
old = sqlite3.connect(OLD_DB)

new_cur = new.cursor()
old_cur = old.cursor()

# Products already in new database
existing = {
    row[0]
    for row in new_cur.execute(
        "SELECT DISTINCT product_code FROM bars"
    ).fetchall()
}

print("Existing products:")
print(existing)

# Find old products not in new db
old_products = [
    row[0]
    for row in old_cur.execute(
        "SELECT DISTINCT product_code FROM bars"
    ).fetchall()
    if row[0] not in existing
]

print("\nImporting:")
print(old_products)

inserted = 0

for product in old_products:
    rows = old_cur.execute(
        """
        SELECT product_code, contract, resolution,
               timestamp, open, high, low, close,
               volume, source, created_at
        FROM bars
        WHERE product_code = ?
        """,
        (product,)
    )

    batch = rows.fetchall()

    new_cur.executemany(
        """
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch
    )

    inserted += len(batch)
    print(product, len(batch))

new.commit()

print("\nDONE")
print("Inserted:", inserted)

new.close()
old.close()