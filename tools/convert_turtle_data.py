from pathlib import Path
from datetime import datetime, timezone
import csv


INPUT_DIR = Path("turtle_raw")       # put downloaded .txt/.csv files here
OUTPUT_DIR = Path("turtle_converted")

OUTPUT_DIR.mkdir(exist_ok=True)


def parse_date(value: str):
    """
    Converts YYMMDD -> UTC timestamp
    """
    dt = datetime.strptime(value, "%y%m%d")
    return dt.replace(tzinfo=timezone.utc)


def convert_file(path: Path):
    output = OUTPUT_DIR / f"{path.stem}.csv"

    rows = 0

    with open(path, "r", newline="") as infile, open(
        output, "w", newline=""
    ) as outfile:

        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        # Your bot's expected format
        writer.writerow([
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ])

        for line in reader:
            if not line:
                continue

            # Remove spaces
            line = [x.strip() for x in line]

            try:
                date, o, h, l, c, volume, oi = line[:7]

                timestamp = parse_date(date)

                writer.writerow([
                    timestamp.isoformat(),
                    o,
                    h,
                    l,
                    c,
                    volume
                ])

                rows += 1

            except Exception as e:
                print(
                    f"Skipping {path.name} row {line}: {e}"
                )

    print(
        f"{path.name}: converted {rows:,} bars -> {output}"
    )


def main():

    files = list(INPUT_DIR.glob("*.txt")) + list(INPUT_DIR.glob("*.csv"))

    if not files:
        print(
            f"No files found in {INPUT_DIR.resolve()}"
        )
        return

    for file in files:
        convert_file(file)


if __name__ == "__main__":
    main()