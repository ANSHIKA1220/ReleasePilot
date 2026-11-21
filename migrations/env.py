from alembic import context
from pilot.db import engine, schema

with engine.begin() as connection:
    context.configure(connection=connection, target_metadata=schema)
    with context.begin_transaction():
        context.run_migrations()
