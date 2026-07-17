"""
Initial Development Agents Schema

Revision ID: 001_development_agents
Revises:
Create Date: 2026-04-27

Schema for development agent operations including tasks, artifacts, and metrics.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_development_agents'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create development agents tables"""

    # Development Agent Tasks
    op.create_table(
        'dev_agent_tasks',
        sa.Column('task_id', sa.String(36), primary_key=True),
        sa.Column('agent_type', sa.String(50), nullable=False),  # 'architect', 'repair', 'requirements'
        sa.Column('task_type', sa.String(50), nullable=False),  # 'architecture', 'bug_fix', 'requirements_analysis', 'code_review'
        sa.Column('task_description', sa.Text()),
        sa.Column('task_data', postgresql.JSON()),  # Original task parameters and context
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),  # pending, in_progress, completed, failed
        sa.Column('priority', sa.Integer(), server_default='3'),  # 1=high, 5=low
        sa.Column('result_data', postgresql.JSON()),  # Task execution results
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for dev_agent_tasks
    op.create_index('idx_dev_tasks_agent_type', 'dev_agent_tasks', ['agent_type'])
    op.create_index('idx_dev_tasks_status', 'dev_agent_tasks', ['status'])
    op.create_index('idx_dev_tasks_created', 'dev_agent_tasks', ['created_at'])
    op.create_index('idx_dev_tasks_type_status', 'dev_agent_tasks', ['task_type', 'status'])

    # Development Artifacts (code, docs, diagrams, etc.)
    op.create_table(
        'dev_artifacts',
        sa.Column('artifact_id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), nullable=True),  # Link to originating task
        sa.Column('artifact_type', sa.String(50), nullable=False),  # 'code', 'documentation', 'architecture_diagram', 'test', 'config'
        sa.Column('artifact_subtype', sa.String(50)),  # 'python', 'typescript', 'markdown', 'yaml', 'json'
        sa.Column('title', sa.String(200)),
        sa.Column('content', sa.Text()),  # Actual artifact content
        sa.Column('file_path', sa.String(500)),  # Original or intended file path
        sa.Column('content_hash', sa.String(64)),  # SHA256 hash of content
        sa.Column('metadata', postgresql.JSON()),  # Size, lines_of_code, dependencies, etc.
        sa.Column('version', sa.String(20), server_default='1.0.0'),
        sa.Column('status', sa.String(20), server_default='draft'),  # draft, reviewed, approved, deployed
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for dev_artifacts
    op.create_index('idx_artifacts_task_id', 'dev_artifacts', ['task_id'])
    op.create_index('idx_artifacts_type', 'dev_artifacts', ['artifact_type'])
    op.create_index('idx_artifacts_status', 'dev_artifacts', ['status'])
    op.create_index('idx_artifacts_created', 'dev_artifacts', ['created_at'])

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_artifacts_task',
        'dev_artifacts', 'dev_agent_tasks',
        ['task_id'], ['task_id'],
        ondelete='SET NULL'
    )

    # Agent Performance Metrics
    op.create_table(
        'agent_metrics',
        sa.Column('metric_id', sa.String(36), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('agent_id', sa.String(100)),
        sa.Column('agent_type', sa.String(50), nullable=False),
        sa.Column('task_id', sa.String(36)),  # Optional link to specific task
        sa.Column('metric_type', sa.String(50), nullable=False),  # 'performance', 'quality', 'efficiency'
        sa.Column('metric_name', sa.String(100), nullable=False),  # 'execution_time', 'code_quality_score', 'success_rate'
        sa.Column('metric_value', sa.Numeric(10, 4)),
        sa.Column('metrics_data', postgresql.JSON()),  # Detailed metrics and context
        sa.Column('recorded_at', sa.TIMESTAMP(timezone=True), nullable=False),
    )

    # Create indexes for agent_metrics
    op.create_index('idx_metrics_agent_type', 'agent_metrics', ['agent_type'])
    op.create_index('idx_metrics_type_name', 'agent_metrics', ['metric_type', 'metric_name'])
    op.create_index('idx_metrics_recorded', 'agent_metrics', ['recorded_at'])
    op.create_index('idx_metrics_agent_id', 'agent_metrics', ['agent_id'])

    # Development Sessions (grouping related tasks/work)
    op.create_table(
        'dev_sessions',
        sa.Column('session_id', sa.String(36), primary_key=True),
        sa.Column('session_name', sa.String(200)),
        sa.Column('session_type', sa.String(50), nullable=False),  # 'feature_development', 'bug_fix_session', 'architecture_review'
        sa.Column('project_context', sa.String(200)),  # Project or module being worked on
        sa.Column('description', sa.Text()),
        sa.Column('goals', postgresql.JSON()),  # Session objectives
        sa.Column('status', sa.String(20), server_default='active'),  # active, completed, paused, cancelled
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for dev_sessions
    op.create_index('idx_sessions_type', 'dev_sessions', ['session_type'])
    op.create_index('idx_sessions_status', 'dev_sessions', ['status'])
    op.create_index('idx_sessions_created', 'dev_sessions', ['created_at'])

    # Link tasks to sessions
    op.add_column('dev_agent_tasks',
                  sa.Column('session_id', sa.String(36)))

    op.create_foreign_key(
        'fk_tasks_session',
        'dev_agent_tasks', 'dev_sessions',
        ['session_id'], ['session_id'],
        ondelete='SET NULL'
    )

    op.create_index('idx_tasks_session', 'dev_agent_tasks', ['session_id'])


def downgrade() -> None:
    """Drop development agents tables"""
    op.drop_table('agent_metrics')
    op.drop_table('dev_artifacts')
    op.drop_table('dev_agent_tasks')
    op.drop_table('dev_sessions')