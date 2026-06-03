# POS CSV loader for the real Purplle dataset.
# Reads data/POS - sample transactionsb1e826f.csv (line-item export).
# Each order_id = one transaction (no multi-line invoices to group).
# transaction_id = order_id, store_id = as-is, basket_value_inr = total_amount (single row)
# timestamp = order_date+order_time Asia/Kolkata -> UTC ISO-8601 Z
# Insert into POSTransaction via app/database.py async engine, skip duplicates.
# CLI: python pipeline/load_pos.py --csv "data/POS - sample transactionsb1e826f.csv"
# Print: rows read, transactions inserted, min/max timestamp, total basket.

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytz
import sqlalchemy.exc

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, POSTransaction


def ist_to_utc(date_str: str, time_str: str) -> datetime:
    naive = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
    ist = pytz.timezone("Asia/Kolkata")
    localized = ist.localize(naive)
    return localized.astimezone(timezone.utc)


async def load_pos(csv_path: str) -> dict:
    df = pd.read_csv(csv_path)

    required = ["order_id", "store_id", "order_date", "order_time", "total_amount"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    total_rows = len(df)

    timestamps = []
    inserted = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        for _, row in df.iterrows():
            timestamp_utc = ist_to_utc(row["order_date"], row["order_time"])

            tx = POSTransaction(
                transaction_id=str(row["order_id"]),
                store_id=row["store_id"],
                basket_value_inr=row["total_amount"],
                timestamp=timestamp_utc,
            )
            session.add(tx)
            try:
                await session.commit()
                inserted += 1
                timestamps.append(timestamp_utc)
            except sqlalchemy.exc.IntegrityError:
                await session.rollback()
                skipped += 1

    total_basket = df["total_amount"].sum()
    min_ts = min(timestamps) if timestamps else None
    max_ts = max(timestamps) if timestamps else None

    return {
        "rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "total_basket": total_basket,
        "min_timestamp": min_ts,
        "max_timestamp": max_ts,
    }


async def main():
    parser = argparse.ArgumentParser(description="Load POS data from CSV")
    parser.add_argument("--csv", required=True, help="Path to POS CSV file")
    args = parser.parse_args()

    result = await load_pos(args.csv)

    print(f"Rows read: {result['rows']}")
    print(f"Transactions inserted: {result['inserted']}")
    print(f"Skipped (duplicates): {result['skipped']}")
    if result["min_timestamp"]:
        print(f"Min timestamp (UTC): {result['min_timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if result["max_timestamp"]:
        print(f"Max timestamp (UTC): {result['max_timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"Total basket value (INR): {result['total_basket']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())