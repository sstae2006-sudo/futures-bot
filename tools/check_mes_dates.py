import sqlite3

c = sqlite3.connect("market_data.db")

print(c.execute(
    "SELECT MIN(timestamp), MAX(timestamp) FROM bars WHERE product_code='MES'"
).fetchone())