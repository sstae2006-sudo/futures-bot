import sqlite3

c = sqlite3.connect("market_data.db")

for symbol in ["CL", "GC", "US", "SP", "HG"]:
    count = c.execute(
        "SELECT COUNT(*) FROM bars WHERE product_code LIKE ?",
        (symbol + "%",)
    ).fetchone()[0]

    print(symbol, count)