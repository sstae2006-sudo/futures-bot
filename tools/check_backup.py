import sqlite3

c = sqlite3.connect("market_data_backup.db")

print("Products:")
print(c.execute(
    "SELECT COUNT(DISTINCT product_code) FROM bars"
).fetchone())

print("\nOld examples:")
print(c.execute(
    "SELECT DISTINCT product_code FROM bars LIMIT 50"
).fetchall())

print("\nCL count:")
print(c.execute(
    "SELECT COUNT(*) FROM bars WHERE product_code LIKE 'CL%'"
).fetchone())