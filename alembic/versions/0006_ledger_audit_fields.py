"""Align call attempts with runtime ownership and lifecycle conventions."""
from alembic import op
import sqlalchemy as sa

revision = '0006_ledger_audit_fields'
down_revision = '0005_model_call_ledger'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('runtime_model_calls') as batch:
        batch.add_column(sa.Column('created_by', sa.String(64), nullable=False, server_default='legacy-ledger'))
        batch.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.execute('UPDATE runtime_model_calls SET created_at=started_at, updated_at=COALESCE(ended_at, started_at)')
    with op.batch_alter_table('runtime_model_calls') as batch:
        batch.alter_column('created_at', nullable=False, existing_type=sa.DateTime())
        batch.alter_column('updated_at', nullable=False, existing_type=sa.DateTime())
        batch.alter_column('created_by', server_default=None, existing_type=sa.String(64))


def downgrade():
    with op.batch_alter_table('runtime_model_calls') as batch:
        for name in ('deleted_at', 'updated_at', 'created_at', 'created_by'):
            batch.drop_column(name)
