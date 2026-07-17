"""Initial memory-plane migration from SQLite schema

Revision ID: 002
Revises:
Create Date: 2026-04-22 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '002'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create memories table
    op.create_table('memories',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('entity_type', sa.String(), nullable=True),
        sa.Column('entity_id', sa.String(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('importance', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create embeddings table
    op.create_table('embeddings',
        sa.Column('memory_id', sa.Integer(), nullable=False),
        sa.Column('vector', sa.LargeBinary(), nullable=False),
        sa.Column('dim', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['memory_id'], ['memories.id'], ),
        sa.PrimaryKeyConstraint('memory_id')
    )

    # Create Alembic version table for tracking migrations
    op.create_table('alembic_version',
        sa.Column('version_num', sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint('version_num')
    )

    # Insert the initial migration version
    op.execute("INSERT INTO alembic_version (version_num) VALUES ('002')")


def downgrade() -> None:
    op.drop_table('embeddings')
    op.drop_table('memories')
    op.drop_table('alembic_version')