import pytest
import asyncio
import os

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./data/test_store.db"
)

from app.database import init_db, engine, Base

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """
    Create all tables once per test session.
    Wipe the test DB file at start so fixed event_ids
    like 'idem-fixed-001' do not carry over between runs.
    """
    db_path = "./data/test_store.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs("./data", exist_ok=True)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    yield
    loop2 = asyncio.new_event_loop()
    loop2.run_until_complete(engine.dispose())
    loop2.close()