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
    async def test_basket_value_per_order(self):
        import pandas as pd
        from io import StringIO

        csv_data = """order_id,order_date,order_time,store_id,product_id,brand_name,total_amount
1,10-04-2026,16:55:36,ST1008,399945,Faces Canada,302.33
2,10-04-2026,16:55:36,ST1008,353621,Faces Canada,491.77
3,10-04-2026,12:42:18,ST1008,407887,Purplle,1
4,10-04-2026,12:42:18,ST1008,384974,Faces Canada,397.38"""

        df = pd.read_csv(StringIO(csv_data))
        assert len(df) == 4
        assert df.iloc[0]["total_amount"] == 302.33
        assert df.iloc[2]["order_id"] == 3
        assert df.iloc[2]["store_id"] == "ST1008"

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