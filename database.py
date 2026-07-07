from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base


engine = None
SessionLocal = None


def setup_database(database_url: str) -> None:
    global engine, SessionLocal
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(database_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            await ensure_sqlite_columns(conn)
        elif conn.dialect.name == "postgresql":
            await ensure_postgres_columns(conn)


async def ensure_postgres_columns(conn) -> None:
    columns = {
        "orders": {
            "channel_message_id": "INTEGER",
            "order_type": "VARCHAR(20) DEFAULT 'passenger'",
            "has_female_passenger": "BOOLEAN DEFAULT FALSE",
        },
        "driver_trips": {
            "is_pickup_service": "BOOLEAN DEFAULT FALSE",
            "has_female_passenger": "BOOLEAN DEFAULT FALSE",
        },
        "order_messages": {
            "contact_message_id": "INTEGER",
            "location_message_id": "INTEGER",
        },
    }
    for table_name, table_columns in columns.items():
        for column_name, column_type in table_columns.items():
            await conn.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )


async def ensure_sqlite_columns(conn) -> None:
    columns = {
        "users": {
            "language": "VARCHAR(5)",
        },
        "orders": {
            "price_per_person": "INTEGER",
            "roof_luggage": "VARCHAR(20)",
            "channel_message_id": "INTEGER",
            "order_type": "VARCHAR(20) DEFAULT 'passenger'",
            "has_female_passenger": "BOOLEAN DEFAULT 0",
        },
        "driver_trips": {
            "roof_luggage": "VARCHAR(20) DEFAULT 'no'",
            "channel_message_id": "INTEGER",
            "is_pickup_service": "BOOLEAN DEFAULT 0",
            "has_female_passenger": "BOOLEAN DEFAULT 0",
        },
        "order_messages": {
            "contact_message_id": "INTEGER",
            "location_message_id": "INTEGER",
        },
    }
    for table_name, table_columns in columns.items():
        existing = await conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
        existing_names = {row[1] for row in existing.fetchall()}
        for column_name, column_type in table_columns.items():
            if column_name not in existing_names:
                await conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
