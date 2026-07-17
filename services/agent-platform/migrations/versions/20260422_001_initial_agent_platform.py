"""
Initial Agent Platform Schema

Revision ID: 001_agent_platform
Revises:
Create Date: 2026-04-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_agent_platform'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create agent platform tables"""

    # Agent definitions table
    op.create_table(
        'agent_definitions',
        sa.Column('agent_id', sa.String(100), primary_key=True),
        sa.Column('agent_name', sa.String(200), nullable=False),
        sa.Column('agent_type', sa.String(50), nullable=False),
        sa.Column('agent_spec', postgresql.JSON(), nullable=False),
        sa.Column('metadata_json', postgresql.JSON()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Agent registry table
    op.create_table(
        'agent_registry',
        sa.Column('registry_id', sa.String(100), primary_key=True),
        sa.Column('agent_id', sa.String(100), sa.ForeignKey('agent_definitions.agent_id')),
        sa.Column('endpoint_url', sa.String(500)),
        sa.Column('capabilities', postgresql.JSON()),
        sa.Column('health_status', sa.String(20), server_default='unknown'),
        sa.Column('last_health_check', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Agent execution state table
    op.create_table(
        'agent_executions',
        sa.Column('execution_id', sa.String(100), primary_key=True),
        sa.Column('agent_id', sa.String(100), sa.ForeignKey('agent_definitions.agent_id')),
        sa.Column('task_id', sa.String(100)),
        sa.Column('execution_state', sa.String(20), server_default='pending'),
        sa.Column('input_data', postgresql.JSON()),
        sa.Column('output_data', postgresql.JSON()),
        sa.Column('error_data', postgresql.JSON()),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Agent repair operations table
    op.create_table(
        'agent_repairs',
        sa.Column('repair_id', sa.String(100), primary_key=True),
        sa.Column('agent_id', sa.String(100), sa.ForeignKey('agent_definitions.agent_id')),
        sa.Column('execution_id', sa.String(100), sa.ForeignKey('agent_executions.execution_id')),
        sa.Column('repair_type', sa.String(50), nullable=False),
        sa.Column('repair_data', postgresql.JSON()),
        sa.Column('repair_status', sa.String(20), server_default='pending'),
        sa.Column('repair_result', postgresql.JSON()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True))
    )

    # Create indexes for performance
    op.create_index('idx_agent_type', 'agent_definitions', ['agent_type'])
    op.create_index('idx_registry_agent', 'agent_registry', ['agent_id'])
    op.create_index('idx_execution_agent', 'agent_executions', ['agent_id'])
    op.create_index('idx_execution_state', 'agent_executions', ['execution_state'])
    op.create_index('idx_repair_agent', 'agent_repairs', ['agent_id'])
    op.create_index('idx_repair_status', 'agent_repairs', ['repair_status'])


def downgrade() -> None:
    """Drop agent platform tables"""
    op.drop_table('agent_repairs')
    op.drop_table('agent_executions')
    op.drop_table('agent_registry')
    op.drop_table('agent_definitions')