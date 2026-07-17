"""Initial order management schema with orders and positions

Revision ID: 001
Revises:
Create Date: 2026-04-27 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create orders table
    op.create_table('orders',
        sa.Column('order_id', sa.String(255), primary_key=True),
        sa.Column('signal_id', sa.String(255), nullable=True),
        sa.Column('strategy_id', sa.String(255), nullable=True),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),  # BUY/SELL
        sa.Column('quantity', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('stop_loss_price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('take_profit_price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('sl_order_id', sa.String(255), nullable=True),
        sa.Column('tp_order_id', sa.String(255), nullable=True),
        sa.Column('state', sa.String(50), nullable=False),
        sa.Column('filled_qty', sa.Numeric(precision=18, scale=8), default=0),
        sa.Column('avg_price', sa.Numeric(precision=18, scale=8), default=0),
        sa.Column('pnl', sa.Numeric(precision=18, scale=8), default=0),
        sa.Column('created_at', sa.String(255), nullable=False),  # ISO timestamp
        sa.Column('updated_at', sa.String(255), nullable=False),  # ISO timestamp
        sa.Column('closed_at', sa.String(255), nullable=True),    # ISO timestamp
        sa.Column('meta', sa.Text, default='{}')                  # JSON string
    )

    # Create positions table
    op.create_table('positions',
        sa.Column('position_id', sa.String(255), primary_key=True),
        sa.Column('order_id', sa.String(255), nullable=False),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('current_price', sa.Numeric(precision=18, scale=8), default=0),
        sa.Column('unrealized_pnl', sa.Numeric(precision=18, scale=8), default=0),
        sa.Column('opened_at', sa.String(255), nullable=False),   # ISO timestamp
        sa.Column('closed_at', sa.String(255), nullable=True),    # ISO timestamp
        sa.Column('is_open', sa.Boolean, default=True, nullable=False)
    )

    # Create indexes for performance
    # Orders table indexes
    op.create_index('ix_orders_signal_id', 'orders', ['signal_id'])
    op.create_index('ix_orders_strategy_id', 'orders', ['strategy_id'])
    op.create_index('ix_orders_symbol', 'orders', ['symbol'])
    op.create_index('ix_orders_side', 'orders', ['side'])
    op.create_index('ix_orders_state', 'orders', ['state'])
    op.create_index('ix_orders_sl_order_id', 'orders', ['sl_order_id'])
    op.create_index('ix_orders_tp_order_id', 'orders', ['tp_order_id'])
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])
    op.create_index('ix_orders_updated_at', 'orders', ['updated_at'])

    # Positions table indexes
    op.create_index('ix_positions_order_id', 'positions', ['order_id'])
    op.create_index('ix_positions_symbol', 'positions', ['symbol'])
    op.create_index('ix_positions_is_open', 'positions', ['is_open'])
    op.create_index('ix_positions_opened_at', 'positions', ['opened_at'])

    # Foreign key constraint
    op.create_foreign_key('fk_positions_order_id', 'positions', 'orders', ['order_id'], ['order_id'])


def downgrade() -> None:
    # Drop foreign key
    op.drop_constraint('fk_positions_order_id', 'positions', type_='foreignkey')

    # Drop indexes
    # Positions indexes
    op.drop_index('ix_positions_opened_at', 'positions')
    op.drop_index('ix_positions_is_open', 'positions')
    op.drop_index('ix_positions_symbol', 'positions')
    op.drop_index('ix_positions_order_id', 'positions')

    # Orders indexes
    op.drop_index('ix_orders_updated_at', 'orders')
    op.drop_index('ix_orders_created_at', 'orders')
    op.drop_index('ix_orders_tp_order_id', 'orders')
    op.drop_index('ix_orders_sl_order_id', 'orders')
    op.drop_index('ix_orders_state', 'orders')
    op.drop_index('ix_orders_side', 'orders')
    op.drop_index('ix_orders_symbol', 'orders')
    op.drop_index('ix_orders_strategy_id', 'orders')
    op.drop_index('ix_orders_signal_id', 'orders')

    # Drop tables
    op.drop_table('positions')
    op.drop_table('orders')