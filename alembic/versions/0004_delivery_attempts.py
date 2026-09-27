"""Durable bounded delivery attempts, retry schedule, and dead-letter references."""
from alembic import op
import sqlalchemy as sa

revision = '0004_delivery_attempts'
down_revision = '0003_parse_quality'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ingestion_jobs') as batch:
        batch.add_column(sa.Column('delivery_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('next_retry_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('dead_letter_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('replay_job_id', sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table('ingestion_jobs') as batch:
        for column in ('replay_job_id', 'dead_letter_at', 'next_retry_at', 'delivery_attempts'):
            batch.drop_column(column)
