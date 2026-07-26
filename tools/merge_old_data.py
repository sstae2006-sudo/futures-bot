import sqlite3

main = sqlite3.connect("market_data.db")
backup = sqlite3.connect("market_data_backup.db")

main.execute("ATTACH DATABASE 'market_data_backup.db' AS old")

main.execute("""
INSERT OR IGNORE INTO bars
(product_code, contract, resolution, timestamp,
 open, high, low, close, volume, source, created_at)

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

FROM old.bars
""")

main.commit()

print("merged")

print(
main.execute(
"SELECT COUNT(*) FROM bars"
).fetchone()
)