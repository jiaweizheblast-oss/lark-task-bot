from __future__ import annotations

from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from tg_automation.storage import models  # noqa: F401
from tg_automation.storage.base import Base
from tg_automation.storage.database import get_db


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
async def client(session: Session):
    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
