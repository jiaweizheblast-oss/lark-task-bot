# Testing

The suite uses isolated SQLite databases and fake gateways; it never calls Telegram.

```powershell
pytest
ruff check apps src tests migrations
python -m compileall -q apps src tests migrations
```

Migration smoke test:

```powershell
$env:DATABASE_URL='sqlite:///./migration-smoke.db'
alembic upgrade head
alembic check
alembic downgrade base
alembic upgrade head
```

The migration smoke database is disposable and must not contain production data.

The automated suite also upgrades a populated copy of the initial schema and verifies that the
new private-bot and campaign columns receive safe defaults without losing existing rows.
