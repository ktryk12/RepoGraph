"""
Initial Policy Management Schema

Revision ID: 005_policy_management
Revises:
Create Date: 2026-04-23

Consolidated schema for policy, policy-validator, and policy_bootstrap
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_policy_management'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create policy management tables"""

    # Policy Definitions and Versions
    op.create_table(
        'policies',
        sa.Column('policy_id', sa.String(100), primary_key=True),
        sa.Column('policy_name', sa.String(200), nullable=False),
        sa.Column('policy_version', sa.String(20), nullable=False),
        sa.Column('policy_type', sa.String(50), nullable=False),  # 'user_profile', 'resource_access', 'operational'
        sa.Column('policy_category', sa.String(50)),  # 'security', 'privacy', 'performance', 'compliance'
        sa.Column('policy_content', sa.Text(), nullable=False),  # YAML or JSON policy definition
        sa.Column('policy_format', sa.String(20), server_default='yaml'),  # 'yaml', 'json', 'rego'
        sa.Column('policy_status', sa.String(20), server_default='draft'),  # draft, active, deprecated, disabled
        sa.Column('effective_from', sa.TIMESTAMP(timezone=True)),
        sa.Column('effective_until', sa.TIMESTAMP(timezone=True)),
        sa.Column('approval_status', sa.String(20), server_default='pending'),  # pending, approved, rejected
        sa.Column('approved_by', sa.String(100)),
        sa.Column('approval_date', sa.TIMESTAMP(timezone=True)),
        sa.Column('metadata_json', postgresql.JSON()),
        sa.Column('tags', postgresql.ARRAY(sa.String())),
        sa.Column('dependencies', postgresql.ARRAY(sa.String())),  # Other policy IDs this depends on
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by', sa.String(100)),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Policy Validation Results
    op.create_table(
        'policy_validations',
        sa.Column('validation_id', sa.String(100), primary_key=True),
        sa.Column('policy_id', sa.String(100), sa.ForeignKey('policies.policy_id')),
        sa.Column('validation_type', sa.String(50), nullable=False),  # 'syntax', 'semantic', 'security', 'performance'
        sa.Column('validation_status', sa.String(20), nullable=False),  # 'passed', 'failed', 'warning'
        sa.Column('validation_results', postgresql.JSON()),  # Detailed validation results
        sa.Column('error_messages', postgresql.ARRAY(sa.String())),
        sa.Column('warnings', postgresql.ARRAY(sa.String())),
        sa.Column('recommendations', postgresql.ARRAY(sa.String())),
        sa.Column('validator_version', sa.String(20)),
        sa.Column('validation_config', postgresql.JSON()),
        sa.Column('validation_duration_ms', sa.Integer()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('validated_by', sa.String(100))
    )

    # Policy Enforcement Logs
    op.create_table(
        'policy_enforcements',
        sa.Column('enforcement_id', sa.String(100), primary_key=True),
        sa.Column('policy_id', sa.String(100), sa.ForeignKey('policies.policy_id')),
        sa.Column('request_id', sa.String(100)),  # Reference to the request being evaluated
        sa.Column('user_id', sa.String(100)),
        sa.Column('resource', sa.String(200)),
        sa.Column('action', sa.String(100)),
        sa.Column('decision', sa.String(20), nullable=False),  # 'allow', 'deny', 'abstain'
        sa.Column('confidence_score', sa.Float()),
        sa.Column('evaluation_context', postgresql.JSON()),  # Context used during evaluation
        sa.Column('rule_matches', postgresql.JSON()),  # Which rules matched
        sa.Column('evaluation_trace', postgresql.JSON()),  # Step-by-step evaluation trace
        sa.Column('enforcement_point', sa.String(100)),  # Where the policy was enforced
        sa.Column('bypass_reason', sa.String(200)),  # If bypassed, why
        sa.Column('evaluation_duration_ms', sa.Integer()),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('session_id', sa.String(100))
    )

    # Governance Events and Audit Trail
    op.create_table(
        'governance_events',
        sa.Column('event_id', sa.String(100), primary_key=True),
        sa.Column('event_type', sa.String(50), nullable=False),  # 'policy_created', 'policy_updated', 'violation_detected'
        sa.Column('event_category', sa.String(50)),  # 'policy_lifecycle', 'compliance', 'security'
        sa.Column('entity_id', sa.String(100)),  # Policy ID or other entity affected
        sa.Column('entity_type', sa.String(50)),  # 'policy', 'user', 'resource'
        sa.Column('actor_id', sa.String(100)),  # Who triggered this event
        sa.Column('actor_type', sa.String(50)),  # 'user', 'system', 'service'
        sa.Column('event_data', postgresql.JSON()),  # Event-specific data
        sa.Column('before_state', postgresql.JSON()),  # State before the event
        sa.Column('after_state', postgresql.JSON()),  # State after the event
        sa.Column('impact_assessment', postgresql.JSON()),  # Impact of this event
        sa.Column('compliance_implications', postgresql.JSON()),  # Compliance-related impacts
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('source_system', sa.String(100)),
        sa.Column('correlation_id', sa.String(100))  # For linking related events
    )

    # Constitution and Rule Management
    op.create_table(
        'constitution_rules',
        sa.Column('rule_id', sa.String(100), primary_key=True),
        sa.Column('rule_name', sa.String(200), nullable=False),
        sa.Column('rule_type', sa.String(50), nullable=False),  # 'constitutional', 'statutory', 'procedural'
        sa.Column('rule_level', sa.Integer(), nullable=False),  # Priority/hierarchy level
        sa.Column('rule_content', sa.Text(), nullable=False),
        sa.Column('rule_format', sa.String(20), server_default='yaml'),
        sa.Column('parent_rule_id', sa.String(100), sa.ForeignKey('constitution_rules.rule_id')),
        sa.Column('interpretation_notes', sa.Text()),
        sa.Column('enforcement_guidance', sa.Text()),
        sa.Column('exceptions', postgresql.JSON()),  # Documented exceptions to this rule
        sa.Column('precedents', postgresql.JSON()),  # Historical enforcement decisions
        sa.Column('rule_status', sa.String(20), server_default='active'),  # active, suspended, repealed
        sa.Column('effective_from', sa.TIMESTAMP(timezone=True)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by', sa.String(100))
    )

    # Policy Templates and Blueprints
    op.create_table(
        'policy_templates',
        sa.Column('template_id', sa.String(100), primary_key=True),
        sa.Column('template_name', sa.String(200), nullable=False),
        sa.Column('template_category', sa.String(50), nullable=False),  # 'user_profiles', 'resource_access', 'data_governance'
        sa.Column('template_content', sa.Text(), nullable=False),  # Template with placeholders
        sa.Column('parameter_schema', postgresql.JSON()),  # Schema for template parameters
        sa.Column('usage_guidelines', sa.Text()),
        sa.Column('example_instances', postgresql.JSON()),  # Example filled templates
        sa.Column('compatibility_matrix', postgresql.JSON()),  # Compatible with which policy types
        sa.Column('validation_rules', postgresql.JSON()),  # Rules for validating template instances
        sa.Column('version', sa.String(20), nullable=False),
        sa.Column('template_status', sa.String(20), server_default='active'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by', sa.String(100)),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )

    # Create indexes for performance
    op.create_index('idx_policies_type', 'policies', ['policy_type'])
    op.create_index('idx_policies_status', 'policies', ['policy_status'])
    op.create_index('idx_policies_category', 'policies', ['policy_category'])
    op.create_index('idx_policies_effective', 'policies', ['effective_from', 'effective_until'])
    op.create_index('idx_policies_created', 'policies', ['created_at'])

    op.create_index('idx_validations_policy', 'policy_validations', ['policy_id'])
    op.create_index('idx_validations_type', 'policy_validations', ['validation_type'])
    op.create_index('idx_validations_status', 'policy_validations', ['validation_status'])

    op.create_index('idx_enforcements_policy', 'policy_enforcements', ['policy_id'])
    op.create_index('idx_enforcements_user', 'policy_enforcements', ['user_id'])
    op.create_index('idx_enforcements_decision', 'policy_enforcements', ['decision'])
    op.create_index('idx_enforcements_timestamp', 'policy_enforcements', ['timestamp'])
    op.create_index('idx_enforcements_resource', 'policy_enforcements', ['resource'])

    op.create_index('idx_events_type', 'governance_events', ['event_type'])
    op.create_index('idx_events_category', 'governance_events', ['event_category'])
    op.create_index('idx_events_entity', 'governance_events', ['entity_id', 'entity_type'])
    op.create_index('idx_events_actor', 'governance_events', ['actor_id'])
    op.create_index('idx_events_timestamp', 'governance_events', ['timestamp'])
    op.create_index('idx_events_correlation', 'governance_events', ['correlation_id'])

    op.create_index('idx_constitution_type', 'constitution_rules', ['rule_type'])
    op.create_index('idx_constitution_level', 'constitution_rules', ['rule_level'])
    op.create_index('idx_constitution_parent', 'constitution_rules', ['parent_rule_id'])
    op.create_index('idx_constitution_status', 'constitution_rules', ['rule_status'])

    op.create_index('idx_templates_category', 'policy_templates', ['template_category'])
    op.create_index('idx_templates_status', 'policy_templates', ['template_status'])
    op.create_index('idx_templates_name', 'policy_templates', ['template_name'])


def downgrade() -> None:
    """Drop policy management tables"""
    op.drop_table('policy_templates')
    op.drop_table('constitution_rules')
    op.drop_table('governance_events')
    op.drop_table('policy_enforcements')
    op.drop_table('policy_validations')
    op.drop_table('policies')