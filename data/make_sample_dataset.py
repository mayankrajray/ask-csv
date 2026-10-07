"""Generate data/sales.csv — realistic Indian retail sales with planted anomalies.

Planted on purpose so the anomaly-detection demo has something to show:
* several revenue spikes (typo-level outliers, 18-40x normal value)
* one negative quantity row (a refund / data-entry error)
* one missing city
* one exact duplicate row (data quality check demo)

Run:  python data/make_sample_dataset.py
"""
import numpy as np
import pandas as pd
from pathlib import Path

rng = np.random.default_rng(42)

REGIONS = {
    "North": ["Delhi", "Jaipur", "Lucknow"],
    "South": ["Bengaluru", "Chennai", "Kochi"],
    "East": ["Kolkata", "Bhubaneswar", "Patna"],
    "West": ["Mumbai", "Pune", "Ahmedabad"],
}
REGION_BIAS = {"North": 1.0, "South": 1.25, "East": 0.7, "West": 1.15}
CHANNELS = ["Online", "In-store", "App"]
FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Kabir",
         "Rohan", "Devansh", "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Anika", "Navya",
         "Myra", "Ishita", "Simran", "Meera", "Kavya", "Tara", "Nisha"]
LAST = ["Sharma", "Verma", "Iyer", "Nair", "Patel", "Reddy", "Das", "Bose", "Khan", "Singh",
        "Mehta", "Joshi", "Chopra", "Ghosh", "Pillai"]

PRODUCTS = {
    "Electronics": [("Wireless Earbuds", 3499), ("Smartwatch", 6999),
                    ("Bluetooth Speaker", 2499), ("Power Bank", 1499)],
    "Apparel": [("Cotton Kurta", 1299), ("Running Shoes", 2799),
                ("Denim Jacket", 3299), ("T-shirt", 599)],
    "Home & Kitchen": [("Mixer Grinder", 3299), ("Air Fryer", 5499),
                       ("Casserole Set", 1799), ("Table Lamp", 999)],
    "Beauty": [("Face Serum", 899), ("Hair Dryer", 1899), ("Sunscreen SPF 50", 499)],
    "Grocery": [("Cold Pressed Oil", 399), ("Organic Honey", 549), ("Dry Fruits Pack", 999)],
}


def main() -> None:
    n = 480
    start = pd.Timestamp("2024-01-05")
    rows = []
    for i in range(n):
        region = rng.choice(list(REGIONS))
        city = rng.choice(REGIONS[region])
        cat = rng.choice(list(PRODUCTS))
        product, price = PRODUCTS[cat][rng.integers(len(PRODUCTS[cat]))]
        date = start + pd.Timedelta(days=int(rng.integers(0, 430)))
        qty = int(rng.integers(1, 6))
        revenue = qty * price * REGION_BIAS[region] * float(rng.normal(1.0, 0.15))
        rows.append([
            f"ORD-{i + 1:05d}", date.date(), region, city, cat, product,
            f"{rng.choice(FIRST)} {rng.choice(LAST)}", qty, int(price),
            round(revenue, 2), rng.choice(CHANNELS),
        ])

    df = pd.DataFrame(rows, columns=[
        "order_id", "order_date", "region", "city", "category", "product",
        "customer", "quantity", "unit_price", "revenue", "channel",
    ])

    # --- planted anomalies -------------------------------------------------
    spike_idx = rng.choice(df.index, size=6, replace=False)
    for idx in spike_idx:
        df.loc[idx, "revenue"] = round(df.loc[idx, "revenue"] * float(rng.uniform(18, 40)), 2)
    df.loc[df.index[-1], "revenue"] = round(df.loc[df.index[-1], "revenue"] * 50, 2)
    df.loc[df.index[7], "quantity"] = -3                      # refund / entry error
    df.loc[df.index[7], "revenue"] = -round(df.loc[df.index[7], "unit_price"] * 3, 2)
    df.loc[df.index[13], "city"] = None                      # missing value
    df = pd.concat([df, df.iloc[[42]]], ignore_index=True)   # duplicate row
    df = df.sample(frac=1, random_state=7).reset_index(drop=True)

    out = Path(__file__).parent / "sales.csv"
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} rows to {out}")
    print("Planted: 7 revenue spikes, 1 negative quantity, 1 missing city, 1 duplicate row")


if __name__ == "__main__":
    main()
