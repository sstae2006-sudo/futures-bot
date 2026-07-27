"""
One-time repair for the turtle-data corruption diagnosed in
docs/DATABASE_CORRUPTION_REPORT.md (2026-07-26): product_code/contract
swap + century-pivot timestamp shift, both confined to
`bars` rows with source='turtletrader'.

Deletes the 342,494 corrupted rows so tools/import_turtle_data.py (now
fixed) can reload them from the regenerated turtle_converted/ CSVs
with correct product_code/contract/timestamp. Logs the row count
before and after so the operation is auditable.

Kept on disk rather than run-and-discard, per the approved repair plan
in docs/DATABASE_CORRUPTION_REPORT.md section 9.
"""
from __future__ import annotations

import sqlite3

DB_PATH = "market_data.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM bars")
    total_before = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bars WHERE source = 'turtletrader'")
    turtle_before = cur.fetchone()[0]

    print(f"bars total before delete:        {total_before:,}")
    print(f"bars source='turtletrader' before delete: {turtle_before:,}")

    cur.execute("DELETE FROM bars WHERE source = 'turtletrader'")
    deleted = cur.rowcount
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM bars")
    total_after = cur.fetchone()[0]

    print(f"rows deleted:                     {deleted:,}")
    print(f"bars total after delete:          {total_after:,}")

    assert total_after == total_before - deleted, (
        "row-count arithmetic didn't add up -- stopping without further action"
    )
    assert deleted == turtle_before, (
        f"expected to delete {turtle_before:,} rows, deleted {deleted:,} instead"
    )

    conn.close()
    print("Delete verified consistent. Ready for tools/import_turtle_data.py.")


if __name__ == "__main__":
    main()
