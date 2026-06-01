import pytest
import asyncio
import os
import shutil

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_artifacts/test_store.db"

from app.database import init_db, engine

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """
    Create all tables once per test session.
    Wipe the test DB file at start so fixed event_ids
    like 'idem-fixed-001' do not carry over between runs.
    """
    db_path = "./test_artifacts/test_store.db"
    artifacts_dir = "./test_artifacts"
    os.makedirs(artifacts_dir, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()
    yield
    loop2 = asyncio.new_event_loop()
    loop2.run_until_complete(engine.dispose())
    loop2.close()