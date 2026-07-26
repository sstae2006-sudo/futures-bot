import pandas as pd

# load Massive CSV data
df = pd.read_csv("data/sample.csv")

print("Loaded rows:", len(df))

# show columns
print(df.head())

# save converted file
df.to_json("data/mes_data.json", orient="records")

print("Saved data/mes_data.json")