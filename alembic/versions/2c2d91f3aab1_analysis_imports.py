"""Allow analysis datasets without a survey template.

Revision ID: 2c2d91f3aab1
Revises: bbd29b9fcf98
"""

from alembic import op
import sqlalchemy as sa

revision = "2c2d91f3aab1"
down_revision = "bbd29b9fcf98"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("surveys") as batch_op:
        batch_op.alter_column("template_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Imported analysis datasets have no template, so they must be removed or
    # assigned a template before a downgrade can restore the NOT NULL constraint.
    with op.batch_alter_table("surveys") as batch_op:
        batch_op.alter_column("template_id", existing_type=sa.Integer(), nullable=False)
