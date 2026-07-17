"""
Initial Editorial Agents Schema

Revision ID: 001_editorial_agents
Revises:
Create Date: 2026-04-27

Schema for editorial operations including content, reviews, and publishing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_editorial_agents'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create editorial agents tables"""

    # Editorial Content (articles, posts, media content)
    op.create_table(
        'editorial_content',
        sa.Column('content_id', sa.String(36), primary_key=True),
        sa.Column('content_type', sa.String(50), nullable=False),  # 'article', 'newsletter', 'social_post', 'video_script'
        sa.Column('title', sa.String(300)),
        sa.Column('slug', sa.String(200)),
        sa.Column('content_body', sa.Text()),
        sa.Column('content_data', postgresql.JSON()),  # Structured content data
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),  # draft, in_review, approved, published, archived
        sa.Column('target_audience', sa.String(100)),
        sa.Column('content_category', sa.String(50)),
        sa.Column('tags', postgresql.JSON()),  # Content tags and keywords
        sa.Column('seo_data', postgresql.JSON()),  # SEO metadata
        sa.Column('created_by', sa.String(100)),
        sa.Column('reviewed_by', sa.String(100)),
        sa.Column('approved_by', sa.String(100)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('published_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for editorial_content
    op.create_index('idx_content_type', 'editorial_content', ['content_type'])
    op.create_index('idx_content_status', 'editorial_content', ['status'])
    op.create_index('idx_content_created', 'editorial_content', ['created_at'])
    op.create_index('idx_content_published', 'editorial_content', ['published_at'])
    op.create_index('idx_content_category', 'editorial_content', ['content_category'])

    # Editorial Review Tasks
    op.create_table(
        'editorial_reviews',
        sa.Column('review_id', sa.String(36), primary_key=True),
        sa.Column('content_id', sa.String(36), nullable=False),
        sa.Column('review_type', sa.String(50), nullable=False),  # 'legal', 'fact_check', 'style', 'seo', 'compliance'
        sa.Column('reviewer_type', sa.String(50)),  # 'human', 'ai_agent', 'automated'
        sa.Column('reviewer_id', sa.String(100)),
        sa.Column('review_criteria', postgresql.JSON()),  # Review checklist and criteria
        sa.Column('review_result', postgresql.JSON()),  # Review findings and decisions
        sa.Column('status', sa.String(20), server_default='pending'),  # pending, in_progress, completed, rejected
        sa.Column('priority', sa.Integer(), server_default='3'),
        sa.Column('decision', sa.String(20)),  # approved, rejected, needs_revision
        sa.Column('feedback', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for editorial_reviews
    op.create_index('idx_reviews_content_id', 'editorial_reviews', ['content_id'])
    op.create_index('idx_reviews_type', 'editorial_reviews', ['review_type'])
    op.create_index('idx_reviews_status', 'editorial_reviews', ['status'])
    op.create_index('idx_reviews_reviewer', 'editorial_reviews', ['reviewer_id'])

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_reviews_content',
        'editorial_reviews', 'editorial_content',
        ['content_id'], ['content_id'],
        ondelete='CASCADE'
    )

    # Publication Schedule and Distribution
    op.create_table(
        'publication_schedule',
        sa.Column('publication_id', sa.String(36), primary_key=True),
        sa.Column('content_id', sa.String(36), nullable=False),
        sa.Column('platform', sa.String(50), nullable=False),  # 'website', 'newsletter', 'twitter', 'linkedin'
        sa.Column('scheduled_time', sa.TIMESTAMP(timezone=True)),
        sa.Column('publication_data', postgresql.JSON()),  # Platform-specific publication data
        sa.Column('status', sa.String(20), server_default='scheduled'),  # scheduled, published, failed, cancelled
        sa.Column('published_url', sa.String(500)),
        sa.Column('engagement_metrics', postgresql.JSON()),  # Views, clicks, shares, etc.
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('published_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for publication_schedule
    op.create_index('idx_publication_content', 'publication_schedule', ['content_id'])
    op.create_index('idx_publication_platform', 'publication_schedule', ['platform'])
    op.create_index('idx_publication_status', 'publication_schedule', ['status'])
    op.create_index('idx_publication_scheduled', 'publication_schedule', ['scheduled_time'])

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_publication_content',
        'publication_schedule', 'editorial_content',
        ['content_id'], ['content_id'],
        ondelete='CASCADE'
    )

    # Editorial Performance Metrics
    op.create_table(
        'editorial_metrics',
        sa.Column('metric_id', sa.String(36), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('agent_type', sa.String(50), nullable=False),  # 'journalist', 'legal_review', 'audience', etc.
        sa.Column('content_id', sa.String(36)),
        sa.Column('metric_type', sa.String(50), nullable=False),  # 'readability', 'seo_score', 'engagement', 'compliance'
        sa.Column('metric_name', sa.String(100), nullable=False),
        sa.Column('metric_value', sa.Numeric(10, 4)),
        sa.Column('metrics_data', postgresql.JSON()),
        sa.Column('recorded_at', sa.TIMESTAMP(timezone=True), nullable=False),
    )

    # Create indexes for editorial_metrics
    op.create_index('idx_editorial_metrics_agent', 'editorial_metrics', ['agent_type'])
    op.create_index('idx_editorial_metrics_content', 'editorial_metrics', ['content_id'])
    op.create_index('idx_editorial_metrics_type', 'editorial_metrics', ['metric_type'])
    op.create_index('idx_editorial_metrics_recorded', 'editorial_metrics', ['recorded_at'])

    # Editorial Workflow States
    op.create_table(
        'editorial_workflows',
        sa.Column('workflow_id', sa.String(36), primary_key=True),
        sa.Column('content_id', sa.String(36), nullable=False),
        sa.Column('workflow_name', sa.String(100), nullable=False),  # 'standard_article', 'breaking_news', 'newsletter'
        sa.Column('current_step', sa.String(50)),
        sa.Column('workflow_data', postgresql.JSON()),  # Workflow state and progress
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
    )

    # Create indexes for editorial_workflows
    op.create_index('idx_workflows_content', 'editorial_workflows', ['content_id'])
    op.create_index('idx_workflows_name', 'editorial_workflows', ['workflow_name'])
    op.create_index('idx_workflows_status', 'editorial_workflows', ['status'])


def downgrade() -> None:
    """Drop editorial agents tables"""
    op.drop_table('editorial_workflows')
    op.drop_table('editorial_metrics')
    op.drop_table('publication_schedule')
    op.drop_table('editorial_reviews')
    op.drop_table('editorial_content')