"""Initial orchestrator-worker schema

Revision ID: 001
Revises:
Create Date: 2026-04-22 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create orchestrator-worker schema"""

    # Episodes table - tracks episode execution requests and results
    op.create_table(
        'episodes',
        sa.Column('episode_id', sa.String(100), nullable=False),
        sa.Column('workflow_id', sa.String(100), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('task_ref', sa.Text(), nullable=False),
        sa.Column('truth_pack_ref', sa.Text(), nullable=False),
        sa.Column('context_id', sa.String(100), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('execution_result', sa.JSON(), nullable=True),
        sa.Column('final_score', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKey('episode_id'),
    )

    # Workflow states table - tracks workflow execution state
    op.create_table(
        'workflow_states',
        sa.Column('workflow_id', sa.String(100), nullable=False),
        sa.Column('episode_id', sa.String(100), nullable=False),
        sa.Column('current_node', sa.String(100), nullable=True),
        sa.Column('completed_nodes', sa.JSON(), nullable=True),
        sa.Column('state_data', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKey('workflow_id'),
    )

    # Worker results table - tracks individual worker execution results
    op.create_table(
        'worker_results',
        sa.Column('result_id', sa.String(100), nullable=False),
        sa.Column('workflow_id', sa.String(100), nullable=False),
        sa.Column('episode_id', sa.String(100), nullable=False),
        sa.Column('worker_type', sa.String(50), nullable=False),
        sa.Column('partition_id', sa.String(100), nullable=False),
        sa.Column('result_data', sa.JSON(), nullable=True),
        sa.Column('execution_time_ms', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKey('result_id'),
    )

    # Indexes for performance
    op.create_index('idx_episodes_status', 'episodes', ['status'])
    op.create_index('idx_episodes_created_at', 'episodes', ['created_at'])
    op.create_index('idx_episodes_workflow_id', 'episodes', ['workflow_id'])

    op.create_index('idx_workflow_states_status', 'workflow_states', ['status'])
    op.create_index('idx_workflow_states_episode_id', 'workflow_states', ['episode_id'])

    op.create_index('idx_worker_results_workflow_id', 'worker_results', ['workflow_id'])
    op.create_index('idx_worker_results_worker_type', 'worker_results', ['worker_type'])
    op.create_index('idx_worker_results_status', 'worker_results', ['status'])

    # Foreign key constraints
    op.create_foreign_key(
        'fk_workflow_states_episode_id',
        'workflow_states', 'episodes',
        ['episode_id'], ['episode_id'],
        ondelete='CASCADE'
    )

    op.create_foreign_key(
        'fk_worker_results_workflow_id',
        'worker_results', 'workflow_states',
        ['workflow_id'], ['workflow_id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    """Drop orchestrator-worker schema"""

    # Drop foreign key constraints
    op.drop_constraint('fk_worker_results_workflow_id', 'worker_results', type_='foreignkey')
    op.drop_constraint('fk_workflow_states_episode_id', 'workflow_states', type_='foreignkey')

    # Drop indexes
    op.drop_index('idx_worker_results_status')
    op.drop_index('idx_worker_results_worker_type')
    op.drop_index('idx_worker_results_workflow_id')
    op.drop_index('idx_workflow_states_episode_id')
    op.drop_index('idx_workflow_states_status')
    op.drop_index('idx_episodes_workflow_id')
    op.drop_index('idx_episodes_created_at')
    op.drop_index('idx_episodes_status')

    # Drop tables
    op.drop_table('worker_results')
    op.drop_table('workflow_states')
    op.drop_table('episodes')