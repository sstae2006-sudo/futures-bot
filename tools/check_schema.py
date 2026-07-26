import sqlite3

for db in ["market_data.db", "market_data_backup.db"]:
    print("\n====", db, "====")
    c = sqlite3.connect(db)

    result = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='bars'"
    ).fetchone()

    print(result[0])