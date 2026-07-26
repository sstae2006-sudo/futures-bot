import sqlite3

c = sqlite3.connect("market_data.db")
c.execute("DELETE FROM bars WHERE product_code='CONTINUOUS'")
c.commit()

print("removed")