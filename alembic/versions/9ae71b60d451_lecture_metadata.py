"""Store editable lecturer names for imported lecture keys.

Revision ID: 9ae71b60d451
Revises: 2c2d91f3aab1
"""

from alembic import op
import sqlalchemy as sa

revision = "9ae71b60d451"
down_revision = "2c2d91f3aab1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("slot_choices") as batch_op:
        batch_op.add_column(sa.Column("lecturer_name", sa.String(length=160), nullable=True))
    # Imported datasets initially used the SurveyJS key as the display text.
    # Clear those placeholder titles so they are visibly available for editing.
    op.execute("UPDATE slot_choices SET text = '' WHERE text = value")
    op.execute(
        "UPDATE survey_slots SET page_title = 'Industry Lectures' "
        "WHERE key_prefix = 'il'"
    )


def downgrade() -> None:
    with op.batch_alter_table("slot_choices") as batch_op:
        batch_op.drop_column("lecturer_name")
