# PROMPT: Build POS CSV loader per AGENTS.md real-dataset spec:
# - Read data/pos_raw.csv with pandas (line-item export, many columns ignored)
# - Group by invoice_number -> one transaction per invoice
# - transaction_id=invoice_number, store_id=as-is, basket_value_inr=sum(total_amount)
# - timestamp=order_date+order_time Asia/Kolkata -> UTC ISO-8601 Z
# - Insert into POSTransaction via app/database.py async engine, skip duplicates
# - CLI: python pipeline/load_pos.py --csv data/pos_raw.csv
# - Print: line items read, unique transactions inserted, min/max timestamp, total basket
# CHANGES MADE: loader + CLI with idempotent upsert, IST->UTC conversion via pytz.

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

    required = ["invoice_number", "store_id", "order_date", "order_time", "total_amount"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    total_line_items = len(df)

    grouped = (
        df.groupby("invoice_number", sort=False)
        .agg(
            store_id=("store_id", "first"),
            basket_value_inr=("total_amount", "sum"),
            order_date=("order_date", "first"),
            order_time=("order_time", "first"),
        )
        .reset_index()
    )

    inserted = 0
    skipped = 0
    timestamps = []

    async with AsyncSessionLocal() as session:
        for _, row in grouped.iterrows():
            timestamp_utc = ist_to_utc(row["order_date"], row["order_time"])

            tx = POSTransaction(
                transaction_id=row["invoice_number"],
                store_id=row["store_id"],
                basket_value_inr=row["basket_value_inr"],
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

    total_basket = grouped["basket_value_inr"].sum()
    min_ts = min(timestamps) if timestamps else None
    max_ts = max(timestamps) if timestamps else None

    return {
        "line_items": total_line_items,
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

    print(f"Line items read: {result['line_items']}")
    print(f"Unique transactions inserted: {result['inserted']}")
    if result["min_timestamp"]:
        print(f"Min transaction timestamp (UTC): {result['min_timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if result["max_timestamp"]:
        print(f"Max transaction timestamp (UTC): {result['max_timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"Total basket value (INR): {result['total_basket']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())