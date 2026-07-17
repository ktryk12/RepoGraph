"""
Initial Tool Platform Schema

Revision ID: 003_tool_platform
Revises:
Create Date: 2026-04-23

Consolidated schema for tools, tool-runtime, and skill-runtime
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003_tool_platform'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create tool platform tables"""

    # Tool Definitions and Registry
    op.create_table(
        'tool_definitions',
        sa.Column('tool_id', sa.String(100), primary_key=True),
        sa.Column('tool_name', sa.String(200), nullable=False),
        sa.Column('tool_type', sa.String(50), nullable=False),  # 'git', 'test', 'lint', 'security', 'skill'
        sa.Column('category', sa.String(50)),  # 'code_quality', 'testing', 'security', 'git_ops'
        sa.Column('command_template', sa.Text(), nullable=False),
        sa.Column('parameters_schema', postgresql.JSON()),  # JSON schema for parameters
        sa.Column('environment_vars', postgresql.JSON()),  # Required environment variables
        sa.Column('timeout_seconds', sa.Integer(), server_default='300'),
        sa.Column('retry_count', sa.Integer(), server_default='0'),
        sa.Column('enabled', sa.Boolean(), server_default='true'),
        sa.Column('version', sa.String(20)),
        sa.Column('documentation', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Tool Executions and Results
    op.create_table(
        'tool_executions',
        sa.Column('execution_id', sa.String(100), primary_key=True),
        sa.Column('tool_id', sa.String(100), sa.ForeignKey('tool_definitions.tool_id')),
        sa.Column('execution_status', sa.String(20), server_default='pending'),  # pending, running, completed, failed, timeout
        sa.Column('input_parameters', postgresql.JSON()),
        sa.Column('command_executed', sa.Text()),
        sa.Column('exit_code', sa.Integer()),
        sa.Column('stdout_output', sa.Text()),
        sa.Column('stderr_output', sa.Text()),
        sa.Column('execution_duration_ms', sa.Integer()),
        sa.Column('memory_usage_mb', sa.Integer()),
        sa.Column('cpu_usage_percent', sa.Float()),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('triggered_by', sa.String(100)),  # user_id or system
        sa.Column('context_data', postgresql.JSON())  # Additional execution context
    )

    # Skill Registry and State
    op.create_table(
        'skill_registry',
        sa.Column('skill_id', sa.String(100), primary_key=True),
        sa.Column('skill_name', sa.String(200), nullable=False),
        sa.Column('skill_version', sa.String(20)),
        sa.Column('skill_path', sa.String(500), nullable=False),
        sa.Column('skill_config', postgresql.JSON()),
        sa.Column('dependencies', postgresql.ARRAY(sa.String())),
        sa.Column('capabilities', postgresql.JSON()),  # What the skill can do
        sa.Column('health_status', sa.String(20), server_default='unknown'),  # healthy, unhealthy, unknown
        sa.Column('last_health_check', sa.TIMESTAMP(timezone=True)),
        sa.Column('registration_status', sa.String(20), server_default='active'),  # active, deprecated, disabled
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Security Scan Results
    op.create_table(
        'security_scans',
        sa.Column('scan_id', sa.String(100), primary_key=True),
        sa.Column('scan_type', sa.String(50), nullable=False),  # 'bandit', 'safety', 'vulnerability'
        sa.Column('target_path', sa.String(500), nullable=False),
        sa.Column('scan_status', sa.String(20), server_default='pending'),
        sa.Column('severity_counts', postgresql.JSON()),  # {high: 2, medium: 5, low: 10}
        sa.Column('findings', postgresql.JSON()),  # Array of security findings
        sa.Column('scan_summary', postgresql.JSON()),  # Overall scan metrics
        sa.Column('baseline_scan_id', sa.String(100)),  # Reference to previous scan for comparison
        sa.Column('scan_duration_seconds', sa.Integer()),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Code Quality Metrics
    op.create_table(
        'code_quality_metrics',
        sa.Column('metric_id', sa.String(100), primary_key=True),
        sa.Column('tool_name', sa.String(50), nullable=False),  # 'pylint', 'flake8', 'black', 'coverage'
        sa.Column('target_path', sa.String(500), nullable=False),
        sa.Column('metric_type', sa.String(50), nullable=False),  # 'lint_score', 'coverage_percent', 'complexity'
        sa.Column('metric_value', sa.Float()),
        sa.Column('metric_details', postgresql.JSON()),  # Detailed breakdown
        sa.Column('threshold_passed', sa.Boolean()),
        sa.Column('threshold_value', sa.Float()),
        sa.Column('previous_value', sa.Float()),  # For trend analysis
        sa.Column('improvement_delta', sa.Float()),
        sa.Column('measured_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('execution_id', sa.String(100), sa.ForeignKey('tool_executions.execution_id'))
    )

    # Git Operations Log
    op.create_table(
        'git_operations',
        sa.Column('operation_id', sa.String(100), primary_key=True),
        sa.Column('operation_type', sa.String(50), nullable=False),  # 'apply_patch', 'commit', 'branch', 'merge'
        sa.Column('repository_path', sa.String(500), nullable=False),
        sa.Column('operation_data', postgresql.JSON()),  # Command details, file changes
        sa.Column('operation_status', sa.String(20), server_default='pending'),
        sa.Column('commit_hash', sa.String(40)),  # Git commit SHA
        sa.Column('branch_name', sa.String(100)),
        sa.Column('files_changed', postgresql.ARRAY(sa.String())),
        sa.Column('lines_added', sa.Integer()),
        sa.Column('lines_removed', sa.Integer()),
        sa.Column('conflict_files', postgresql.ARRAY(sa.String())),
        sa.Column('error_message', sa.Text()),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('triggered_by', sa.String(100))
    )

    # Create indexes for performance
    op.create_index('idx_tools_type', 'tool_definitions', ['tool_type'])
    op.create_index('idx_tools_category', 'tool_definitions', ['category'])
    op.create_index('idx_tools_enabled', 'tool_definitions', ['enabled'])

    op.create_index('idx_executions_tool', 'tool_executions', ['tool_id'])
    op.create_index('idx_executions_status', 'tool_executions', ['execution_status'])
    op.create_index('idx_executions_started', 'tool_executions', ['started_at'])
    op.create_index('idx_executions_trigger', 'tool_executions', ['triggered_by'])

    op.create_index('idx_skills_status', 'skill_registry', ['registration_status'])
    op.create_index('idx_skills_health', 'skill_registry', ['health_status'])
    op.create_index('idx_skills_name', 'skill_registry', ['skill_name'])

    op.create_index('idx_scans_type', 'security_scans', ['scan_type'])
    op.create_index('idx_scans_status', 'security_scans', ['scan_status'])
    op.create_index('idx_scans_target', 'security_scans', ['target_path'])
    op.create_index('idx_scans_completed', 'security_scans', ['completed_at'])

    op.create_index('idx_metrics_tool', 'code_quality_metrics', ['tool_name'])
    op.create_index('idx_metrics_type', 'code_quality_metrics', ['metric_type'])
    op.create_index('idx_metrics_target', 'code_quality_metrics', ['target_path'])
    op.create_index('idx_metrics_measured', 'code_quality_metrics', ['measured_at'])

    op.create_index('idx_git_ops_type', 'git_operations', ['operation_type'])
    op.create_index('idx_git_ops_status', 'git_operations', ['operation_status'])
    op.create_index('idx_git_ops_repo', 'git_operations', ['repository_path'])
    op.create_index('idx_git_ops_branch', 'git_operations', ['branch_name'])


def downgrade() -> None:
    """Drop tool platform tables"""
    op.drop_table('git_operations')
    op.drop_table('code_quality_metrics')
    op.drop_table('security_scans')
    op.drop_table('skill_registry')
    op.drop_table('tool_executions')
    op.drop_table('tool_definitions')