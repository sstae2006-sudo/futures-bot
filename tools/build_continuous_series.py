import sqlite3

DB = "market_data.db"

conn = sqlite3.connect(DB)
cur = conn.cursor()

# remove old continuous if rerunning
cur.execute("""
DELETE FROM bars
WHERE product_code='MES_CONTINUOUS'
""")

# build continuous MES
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
source
)

SELECT
'MES_CONTINUOUS',
'CONTINUOUS',
resolution,
timestamp,
open,
high,
low,
close,
volume,
source

FROM bars

WHERE contract LIKE 'MES%'

ORDER BY timestamp

""")

conn.commit()

count = cur.execute("""
SELECT COUNT(*)
FROM bars
WHERE product_code='MES_CONTINUOUS'
""").fetchone()[0]

print(f"Created MES continuous: {count} bars")

conn.close()