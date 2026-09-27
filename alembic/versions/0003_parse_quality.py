"""Add quarantine status without altering existing data or indexes."""
from alembic import op

revision = "0003_parse_quality"
down_revision = "0002_runtime_business_data"
branch_labels = None
depends_on = None

STATUSES = {
    "documents": ("received", "stored", "parsed", "chunked", "indexed", "published", "archived", "failed", "duplicate"),
    "ingestion_jobs": ("pending", "running", "succeeded", "failed", "retrying", "cancelled"),
}


def _change(add):
    for table, statuses in STATUSES.items():
        if add:
            statuses += ("requires_review",)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_status_valid"), type_="check")
            batch.create_check_constraint(op.f(f"ck_{table}_status_valid"),
                                          "status IN (" + ", ".join(repr(s) for s in statuses) + ")")


def upgrade():
    _change(True)


def downgrade():
    # Refuse downgrade while quarantines exist; never silently release or
    # relabel them. The restored constraint enforces this transactionally.
    _change(False)
