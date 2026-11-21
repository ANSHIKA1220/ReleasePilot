from alembic import op
from migrations.schema_v1 import schema

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    schema.create_all(op.get_bind())

def downgrade():
    schema.drop_all(op.get_bind())
