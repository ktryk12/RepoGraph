"""
Initial MediaProduction Agents Schema

Revision ID: 001_media-production_agents
Revises:
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_media-production_agents'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    """Create media-production agents tables"""
    
    op.create_table(
        'media-production_tasks',
        sa.Column('task_id', sa.String(36), primary_key=True),
        sa.Column('task_type', sa.String(50), nullable=False),
        sa.Column('task_data', postgresql.JSON()),
        sa.Column('status', sa.String(20), server_default='pending'),
        sa.Column('result_data', postgresql.JSON()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
    )
    
    op.create_index('idx_media-production_tasks_status', 'media-production_tasks', ['status'])
    op.create_index('idx_media-production_tasks_created', 'media-production_tasks', ['created_at'])

def downgrade() -> None:
    """Drop media-production agents tables"""
    op.drop_table('media-production_tasks')
