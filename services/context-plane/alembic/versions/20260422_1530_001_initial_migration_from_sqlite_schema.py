"""Initial migration from SQLite schema

Revision ID: 001
Revises:
Create Date: 2026-04-22 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create context_payloads table
    op.create_table('context_payloads',
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('context_id')
    )

    # Create context_entries table
    op.create_table('context_entries',
        sa.Column('doc_id', sa.String(), nullable=False),
        sa.Column('doc_version', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('module_layer', sa.String(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('exports', sa.Text(), nullable=True),
        sa.Column('internal_deps', sa.Text(), nullable=True),
        sa.Column('checksum', sa.String(), nullable=True),
        sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('doc_id', 'doc_version')
    )

    # Create indexes for context_entries
    op.create_index('idx_module_layer', 'context_entries', ['module_layer'], unique=False)
    op.create_index('idx_checksum', 'context_entries', ['checksum'], unique=False)

    # Create dep_graph table
    op.create_table('dep_graph',
        sa.Column('from_doc_id', sa.String(), nullable=False),
        sa.Column('to_doc_id', sa.String(), nullable=False),
        sa.Column('dep_type', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('from_doc_id', 'to_doc_id', 'dep_type')
    )

    # Create context_retrievals table
    op.create_table('context_retrievals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_description', sa.Text(), nullable=True),
        sa.Column('task_type', sa.String(), nullable=True),
        sa.Column('doc_ids_retrieved', sa.Text(), nullable=True),
        sa.Column('strategy_used', sa.String(), nullable=True),
        sa.Column('was_useful', sa.Integer(), nullable=True),
        sa.Column('consumer', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('context_retrievals')
    op.drop_table('dep_graph')
    op.drop_index('idx_checksum', table_name='context_entries')
    op.drop_index('idx_module_layer', table_name='context_entries')
    op.drop_table('context_entries')
    op.drop_table('context_payloads')