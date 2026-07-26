import sqlite3

db = "market_data.db"

conn = sqlite3.connect(db)
cur = conn.cursor()

fixes = {
    "MES": "MES",
    "MNQ": "MNQ",
    "M2K": "M2K",
    "CL": "CL",
    "GC": "GC",
    "HG": "HG",
    "DX": "DX",
    "SP": "SP",
    "US": "US",
}

# Fix based on contract prefix
for contract_prefix, product in fixes.items():
    cur.execute(
        "UPDATE bars SET product_code=? WHERE contract LIKE ?",
        (product, contract_prefix + "%")
    )

# Continuous contracts
cur.execute(
    "UPDATE bars SET product_code='CONTINUOUS' WHERE contract='CONTINUOUS'"
)

conn.commit()

print("Products fixed")

print(
    cur.execute(
        "SELECT DISTINCT product_code, contract FROM bars LIMIT 50"
    ).fetchall()
)

conn.close()