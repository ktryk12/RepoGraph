"""Initial policy enforcement schema

Revision ID: 001
Revises:
Create Date: 2026-04-27 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create policy_decisions table - main decision log
    op.create_table('policy_decisions',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('timestamp', sa.String(255), nullable=False),  # ISO timestamp
        sa.Column('session_id', sa.String(255), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('capability', sa.String(100), nullable=False),
        sa.Column('resource', sa.Text, nullable=True),
        sa.Column('effect', sa.String(20), nullable=False),  # allow/deny
        sa.Column('reason', sa.Text, nullable=False),
        sa.Column('determining_layer', sa.String(50), nullable=False),
        sa.Column('determining_rule_id', sa.String(100), nullable=False),
        sa.Column('observe_mode', sa.Boolean, default=False),
        sa.Column('enforced', sa.Boolean, default=False),
        sa.Column('trace', sa.Text, nullable=True),  # JSON array
        sa.Column('tenant', sa.String(100), nullable=True),
        sa.Column('legacy_effect', sa.String(20), nullable=True),
        sa.Column('legacy_reason', sa.Text, nullable=True),
        sa.Column('stage', sa.String(50), nullable=True)
    )

    # Create policies table - policy definitions
    op.create_table('policies',
        sa.Column('id', sa.String(255), nullable=False),
        sa.Column('version', sa.String(50), nullable=False),
        sa.Column('layer', sa.String(50), nullable=False),  # base/profile/context/session
        sa.Column('api_version', sa.String(50), nullable=True),
        sa.Column('kind', sa.String(50), nullable=True),
        sa.Column('metadata', sa.Text, nullable=True),  # JSON
        sa.Column('policy_data', sa.Text, nullable=False),  # Full policy JSON
        sa.Column('created_at', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint('id', 'version')
    )

    # Create session_policies table - session-specific overrides
    op.create_table('session_policies',
        sa.Column('session_id', sa.String(255), primary_key=True),
        sa.Column('policy_overrides', sa.Text, nullable=False),  # JSON
        sa.Column('created_at', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.String(255), nullable=False)
    )

    # Create context_policies table - repo/tenant specific policies
    op.create_table('context_policies',
        sa.Column('context_key', sa.String(255), primary_key=True),  # repo_root:tenant
        sa.Column('policy_data', sa.Text, nullable=False),  # JSON
        sa.Column('created_at', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.String(255), nullable=False)
    )

    # Create policy_divergences table - track divergence between new/legacy
    op.create_table('policy_divergences',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('timestamp', sa.String(255), nullable=False),
        sa.Column('session_id', sa.String(255), nullable=False),
        sa.Column('capability', sa.String(100), nullable=False),
        sa.Column('resource', sa.Text, nullable=True),
        sa.Column('new_decision', sa.String(20), nullable=False),
        sa.Column('new_reason', sa.Text, nullable=False),
        sa.Column('legacy_decision', sa.String(20), nullable=False),
        sa.Column('legacy_reason', sa.Text, nullable=False),
        sa.Column('stage', sa.String(50), nullable=True)
    )

    # Create policy_configurations table - config settings
    op.create_table('policy_configurations',
        sa.Column('config_key', sa.String(100), primary_key=True),
        sa.Column('config_data', sa.Text, nullable=False),  # JSON
        sa.Column('created_at', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.String(255), nullable=False)
    )

    # Create indexes for performance
    # Policy decisions indexes
    op.create_index('ix_policy_decisions_timestamp', 'policy_decisions', ['timestamp'])
    op.create_index('ix_policy_decisions_session_id', 'policy_decisions', ['session_id'])
    op.create_index('ix_policy_decisions_user_id', 'policy_decisions', ['user_id'])
    op.create_index('ix_policy_decisions_capability', 'policy_decisions', ['capability'])
    op.create_index('ix_policy_decisions_effect', 'policy_decisions', ['effect'])
    op.create_index('ix_policy_decisions_determining_layer', 'policy_decisions', ['determining_layer'])
    op.create_index('ix_policy_decisions_tenant', 'policy_decisions', ['tenant'])
    op.create_index('ix_policy_decisions_enforced', 'policy_decisions', ['enforced'])

    # Policies indexes
    op.create_index('ix_policies_layer', 'policies', ['layer'])
    op.create_index('ix_policies_created_at', 'policies', ['created_at'])

    # Session policies indexes
    op.create_index('ix_session_policies_created_at', 'session_policies', ['created_at'])

    # Context policies indexes
    op.create_index('ix_context_policies_created_at', 'context_policies', ['created_at'])

    # Divergences indexes
    op.create_index('ix_policy_divergences_timestamp', 'policy_divergences', ['timestamp'])
    op.create_index('ix_policy_divergences_session_id', 'policy_divergences', ['session_id'])
    op.create_index('ix_policy_divergences_capability', 'policy_divergences', ['capability'])

    # Configurations indexes
    op.create_index('ix_policy_configurations_updated_at', 'policy_configurations', ['updated_at'])


def downgrade() -> None:
    # Drop indexes
    # Configurations indexes
    op.drop_index('ix_policy_configurations_updated_at', 'policy_configurations')

    # Divergences indexes
    op.drop_index('ix_policy_divergences_capability', 'policy_divergences')
    op.drop_index('ix_policy_divergences_session_id', 'policy_divergences')
    op.drop_index('ix_policy_divergences_timestamp', 'policy_divergences')

    # Context policies indexes
    op.drop_index('ix_context_policies_created_at', 'context_policies')

    # Session policies indexes
    op.drop_index('ix_session_policies_created_at', 'session_policies')

    # Policies indexes
    op.drop_index('ix_policies_created_at', 'policies')
    op.drop_index('ix_policies_layer', 'policies')

    # Policy decisions indexes
    op.drop_index('ix_policy_decisions_enforced', 'policy_decisions')
    op.drop_index('ix_policy_decisions_tenant', 'policy_decisions')
    op.drop_index('ix_policy_decisions_determining_layer', 'policy_decisions')
    op.drop_index('ix_policy_decisions_effect', 'policy_decisions')
    op.drop_index('ix_policy_decisions_capability', 'policy_decisions')
    op.drop_index('ix_policy_decisions_user_id', 'policy_decisions')
    op.drop_index('ix_policy_decisions_session_id', 'policy_decisions')
    op.drop_index('ix_policy_decisions_timestamp', 'policy_decisions')

    # Drop tables
    op.drop_table('policy_configurations')
    op.drop_table('policy_divergences')
    op.drop_table('context_policies')
    op.drop_table('session_policies')
    op.drop_table('policies')
    op.drop_table('policy_decisions')