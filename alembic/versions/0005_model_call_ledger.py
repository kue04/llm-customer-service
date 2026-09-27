"""Persistent model attempt ledger; prices and unknown usage remain explicit."""
from alembic import op
from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Numeric, String, Table, Float

# Frozen migration schema; do not import the evolving runtime table definition.
calls = Table('runtime_model_calls', MetaData(),
              Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False, index=True),
              Column('request_id', String(64), nullable=False, index=True), Column('job_id', String(64), nullable=False, index=True),
              Column('attempt', Integer, nullable=False), Column('provider', String(32), nullable=False),
              Column('model', String(256), nullable=False), Column('kind', String(32), nullable=False),
              Column('started_at', DateTime, nullable=False), Column('ended_at', DateTime),
              Column('status', String(32), nullable=False), Column('error_code', String(64)),
              Column('prompt_tokens', Integer), Column('completion_tokens', Integer), Column('total_tokens', Integer),
              Column('counting_method', String(64)), Column('price_version', String(128)), Column('currency', String(16)),
              Column('amount', Numeric(24, 12)), Column('elapsed_ms', Float), Column('resources', JSON))

revision = '0005_model_call_ledger'
down_revision = '0004_delivery_attempts'
branch_labels = None
depends_on = None


def upgrade():
    calls.create(op.get_bind())


def downgrade():
    calls.drop(op.get_bind())
