"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-09

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("department", sa.String(length=40), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("applicant", sa.String(length=40), nullable=False),
        sa.Column("applicant_id", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reject_reason", sa.String(length=300), nullable=True),
        sa.Column("approver_name", sa.String(length=40), nullable=True),
        sa.Column("approval_note", sa.String(length=300), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["applicant_id"], ["users.id"]),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_expenses_code", "expenses", ["code"])
    op.create_index("ix_expenses_applicant_id", "expenses", ["applicant_id"])
    op.create_index("ix_expenses_status", "expenses", ["status"])

    op.create_table(
        "expense_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("expense_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("spent_on", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"]),
    )
    op.create_index("ix_expense_items_expense_id", "expense_items", ["expense_id"])


def downgrade() -> None:
    op.drop_index("ix_expense_items_expense_id", table_name="expense_items")
    op.drop_table("expense_items")
    op.drop_index("ix_expenses_status", table_name="expenses")
    op.drop_index("ix_expenses_applicant_id", table_name="expenses")
    op.drop_index("ix_expenses_code", table_name="expenses")
    op.drop_table("expenses")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
