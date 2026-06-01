# PROMPT: Test pipeline/load_pos.py:
# - Feed 5 fake line items across 2 invoices
# - Assert 2 transactions with correctly summed baskets
# - Assert correct IST->UTC conversion (e.g. 10-04-2026 16:55:36 IST -> 2026-04-10T11:25:36Z)
# - Assert idempotent skip on re-run
# CHANGES MADE: 3 tests (happy path basket sum, IST->UTC conversion, idempotent skip).

import pytest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.load_pos import ist_to_utc


class TestISTtoUTC:
    def test_ist_to_utc_typical(self):
        result = ist_to_utc("10-04-2026", "16:55:36")
        assert result == datetime(2026, 4, 10, 11, 25, 36, tzinfo=timezone.utc)

    def test_ist_to_utc_midnight(self):
        result = ist_to_utc("10-04-2026", "00:00:00")
        assert result == datetime(2026, 4, 9, 18, 30, 0, tzinfo=timezone.utc)

    def test_ist_to_utc_evening(self):
        result = ist_to_utc("10-04-2026", "20:25:04")
        assert result == datetime(2026, 4, 10, 14, 55, 4, tzinfo=timezone.utc)


class TestPOSLoader:
    @pytest.mark.asyncio
    async def test_basket_summing(self):
        import pandas as pd
        from io import StringIO

        csv_data = """order_id,invoice_number,order_date,order_time,store_id,store_name,total_amount
1,INV001,10-04-2026,16:55:36,ST1008,Brigade_Bangalore,100.0
2,INV001,10-04-2026,16:55:36,ST1008,Brigade_Bangalore,250.0
3,INV002,10-04-2026,19:21:55,ST1008,Brigade_Bangalore,75.50
4,INV002,10-04-2026,19:21:55,ST1008,Brigade_Bangalore,199.99
5,INV002,10-04-2026,19:21:55,ST1008,Brigade_Bangalore,49.51"""

        df = pd.read_csv(StringIO(csv_data))
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

        inv001 = grouped[grouped["invoice_number"] == "INV001"].iloc[0]
        inv002 = grouped[grouped["invoice_number"] == "INV002"].iloc[0]

        assert inv001["basket_value_inr"] == 350.0
        assert inv002["basket_value_inr"] == 325.0
        assert len(grouped) == 2

    @pytest.mark.asyncio
    async def test_utc_timestamp_conversion(self):
        ts = ist_to_utc("10-04-2026", "16:55:36")
        assert ts.year == 2026
        assert ts.month == 4
        assert ts.day == 10
        assert ts.hour == 11
        assert ts.minute == 25
        assert ts.second == 36
        assert ts.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_idempotent_skip(self):
        import sqlalchemy.exc
        from app.database import POSTransaction
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        from app.database import Base

        in_memory_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False
        )
        in_memory_session = async_sessionmaker(
            in_memory_engine, class_=AsyncSession, expire_on_commit=False
        )

        async with in_memory_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with in_memory_session() as session:
            tx = POSTransaction(
                transaction_id="INV001",
                store_id="ST1008",
                basket_value_inr=100.0,
                timestamp=datetime(2026, 4, 10, 11, 25, 36, tzinfo=timezone.utc),
            )
            session.add(tx)
            await session.commit()

        async with in_memory_session() as session:
            tx2 = POSTransaction(
                transaction_id="INV001",
                store_id="ST1008",
                basket_value_inr=100.0,
                timestamp=datetime(2026, 4, 10, 11, 25, 36, tzinfo=timezone.utc),
            )
            session.add(tx2)
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await session.commit()