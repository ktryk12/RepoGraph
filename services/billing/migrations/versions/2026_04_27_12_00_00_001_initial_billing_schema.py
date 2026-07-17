"""Initial billing schema with subscriptions and billing events

Revision ID: 001
Revises:
Create Date: 2026-04-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create subscriptions table
    op.create_table('subscriptions',
        sa.Column('customer_id', sa.String(255), primary_key=True),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        sa.Column('stripe_sub_id', sa.String(255), nullable=True),
        sa.Column('tier', sa.String(50), nullable=False, default='free'),
        sa.Column('status', sa.String(50), nullable=False, default='inactive'),
        sa.Column('current_period_end', sa.String(255), nullable=True),  # ISO timestamp
        sa.Column('created_at', sa.String(255), nullable=False),         # ISO timestamp
        sa.Column('updated_at', sa.String(255), nullable=False)          # ISO timestamp
    )

    # Create billing_events table
    op.create_table('billing_events',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('event_type', sa.String(255), nullable=False),
        sa.Column('stripe_event_id', sa.String(255), nullable=True),
        sa.Column('customer_id', sa.String(255), nullable=True),
        sa.Column('payload', sa.Text, nullable=False),                   # JSON string
        sa.Column('recorded_at', sa.String(255), nullable=False)         # ISO timestamp
    )

    # Create indexes for performance
    op.create_index('ix_subscriptions_stripe_customer_id', 'subscriptions', ['stripe_customer_id'])
    op.create_index('ix_subscriptions_stripe_sub_id', 'subscriptions', ['stripe_sub_id'])
    op.create_index('ix_subscriptions_tier', 'subscriptions', ['tier'])
    op.create_index('ix_subscriptions_status', 'subscriptions', ['status'])

    op.create_index('ix_billing_events_event_type', 'billing_events', ['event_type'])
    op.create_index('ix_billing_events_customer_id', 'billing_events', ['customer_id'])
    op.create_index('ix_billing_events_stripe_event_id', 'billing_events', ['stripe_event_id'])
    op.create_index('ix_billing_events_recorded_at', 'billing_events', ['recorded_at'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_billing_events_recorded_at', 'billing_events')
    op.drop_index('ix_billing_events_stripe_event_id', 'billing_events')
    op.drop_index('ix_billing_events_customer_id', 'billing_events')
    op.drop_index('ix_billing_events_event_type', 'billing_events')

    op.drop_index('ix_subscriptions_status', 'subscriptions')
    op.drop_index('ix_subscriptions_tier', 'subscriptions')
    op.drop_index('ix_subscriptions_stripe_sub_id', 'subscriptions')
    op.drop_index('ix_subscriptions_stripe_customer_id', 'subscriptions')

    # Drop tables
    op.drop_table('billing_events')
    op.drop_table('subscriptions')