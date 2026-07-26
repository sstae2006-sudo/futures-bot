import sqlite3
from pathlib import Path

DB = Path("market_data.db")


def product_from_contract(contract):
    known = [
        "MES",
        "MNQ",
        "CL",
        "GC",
        "HG",
        "SP",
        "US",
        "DX",
    ]

    for product in known:
        if contract.startswith(product):
            return product

    return contract


conn = sqlite3.connect(DB)

cur = conn.execute("""
SELECT DISTINCT contract
FROM bars
""")

contracts = [row[0] for row in cur.fetchall()]

print(f"Found {len(contracts)} contracts")

changed = 0

for contract in contracts:
    product = product_from_contract(contract)

    if product != contract:
        conn.execute(
            """
            UPDATE bars
            SET product_code = ?
            WHERE contract = ?
            """,
            (product, contract)
        )
        changed += 1

conn.commit()

print(f"Updated {changed} contracts")
print("DONE")

conn.close()