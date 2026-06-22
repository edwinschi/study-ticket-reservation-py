import asyncio
import os
from uuid import uuid4

from sqlalchemy import Connection, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings

EXPECTED_TABLES = {
    "alembic_version",
    "events",
    "reservation_items",
    "reservation_seats",
    "reservations",
    "seats",
    "ticket_types",
    "users",
    "visitor_sessions",
}


def get_table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


async def run_migrations(database_url: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "alembic",
        "upgrade",
        "head",
        env={**os.environ, "DATABASE_URL": database_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()

    assert process.returncode == 0, output.decode()


async def create_database(engine: AsyncEngine, database_name: str) -> None:
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database_name}"'))


async def drop_database(engine: AsyncEngine, database_name: str) -> None:
    async with engine.connect() as connection:
        await connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))


async def test_migrations_create_domain_tables() -> None:
    database_name = f"migration_test_{uuid4().hex}"
    application_url = make_url(get_settings().database_url)
    admin_url = application_url.set(database="postgres")
    test_url = application_url.set(database=database_name)
    database_url = test_url.render_as_string(hide_password=False)

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = create_async_engine(test_url)
    database_created = False

    try:
        await create_database(admin_engine, database_name)
        database_created = True
        await run_migrations(database_url)

        async with test_engine.connect() as connection:
            table_names = await connection.run_sync(get_table_names)

        assert table_names >= EXPECTED_TABLES
    finally:
        await test_engine.dispose()
        if database_created:
            await drop_database(admin_engine, database_name)
        await admin_engine.dispose()
