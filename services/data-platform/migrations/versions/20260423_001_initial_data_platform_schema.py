"""
Initial Data Platform Schema

Revision ID: 001_data_platform
Revises:
Create Date: 2026-04-23

Consolidated schema for data-exporter, artifact-writer, execution-audit, and publisher
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_data_platform'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create data platform tables"""

    # Data Export Tracking
    op.create_table(
        'data_exports',
        sa.Column('export_id', sa.String(100), primary_key=True),
        sa.Column('export_type', sa.String(50), nullable=False),  # 'csv', 'json', 'parquet', etc.
        sa.Column('source_table', sa.String(100)),
        sa.Column('filter_criteria', postgresql.JSON()),
        sa.Column('export_status', sa.String(20), server_default='pending'),  # pending, running, completed, failed
        sa.Column('file_path', sa.String(500)),
        sa.Column('file_size_bytes', sa.BigInteger()),
        sa.Column('row_count', sa.Integer()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('error_message', sa.Text())
    )

    # Artifact Storage and Metadata
    op.create_table(
        'artifacts',
        sa.Column('artifact_id', sa.String(100), primary_key=True),
        sa.Column('artifact_type', sa.String(50), nullable=False),  # 'document', 'model', 'dataset', etc.
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger()),
        sa.Column('file_hash_sha256', sa.String(64)),
        sa.Column('mime_type', sa.String(100)),
        sa.Column('metadata_json', postgresql.JSON()),
        sa.Column('source_system', sa.String(100)),
        sa.Column('created_by', sa.String(100)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('archived', sa.Boolean(), server_default='false')
    )

    # Execution Audit Logs
    op.create_table(
        'execution_audits',
        sa.Column('audit_id', sa.String(100), primary_key=True),
        sa.Column('execution_id', sa.String(100), nullable=False),
        sa.Column('service_name', sa.String(100), nullable=False),
        sa.Column('operation_type', sa.String(50), nullable=False),  # 'create', 'update', 'delete', 'execute'
        sa.Column('operation_data', postgresql.JSON()),
        sa.Column('execution_status', sa.String(20), nullable=False),  # 'success', 'failure', 'timeout'
        sa.Column('execution_duration_ms', sa.Integer()),
        sa.Column('error_code', sa.String(50)),
        sa.Column('error_message', sa.Text()),
        sa.Column('user_id', sa.String(100)),
        sa.Column('session_id', sa.String(100)),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('request_data', postgresql.JSON()),
        sa.Column('response_data', postgresql.JSON())
    )

    # Publishing Operations
    op.create_table(
        'publications',
        sa.Column('publication_id', sa.String(100), primary_key=True),
        sa.Column('content_id', sa.String(100), nullable=False),
        sa.Column('platform', sa.String(50), nullable=False),  # 'twitter', 'linkedin', 'youtube', 'newsletter'
        sa.Column('publication_type', sa.String(50), nullable=False),  # 'post', 'video', 'article', 'newsletter'
        sa.Column('content_text', sa.Text()),
        sa.Column('media_urls', postgresql.ARRAY(sa.String())),
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('published_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('publication_status', sa.String(20), server_default='draft'),  # draft, scheduled, published, failed
        sa.Column('platform_post_id', sa.String(100)),  # ID from external platform
        sa.Column('platform_url', sa.String(500)),
        sa.Column('engagement_metrics', postgresql.JSON()),  # likes, shares, comments, etc.
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('error_message', sa.Text())
    )

    # Create indexes for performance
    op.create_index('idx_exports_status', 'data_exports', ['export_status'])
    op.create_index('idx_exports_type', 'data_exports', ['export_type'])
    op.create_index('idx_exports_created', 'data_exports', ['created_at'])

    op.create_index('idx_artifacts_type', 'artifacts', ['artifact_type'])
    op.create_index('idx_artifacts_created', 'artifacts', ['created_at'])
    op.create_index('idx_artifacts_source', 'artifacts', ['source_system'])
    op.create_index('idx_artifacts_archived', 'artifacts', ['archived'])

    op.create_index('idx_audits_execution', 'execution_audits', ['execution_id'])
    op.create_index('idx_audits_service', 'execution_audits', ['service_name'])
    op.create_index('idx_audits_status', 'execution_audits', ['execution_status'])
    op.create_index('idx_audits_timestamp', 'execution_audits', ['timestamp'])
    op.create_index('idx_audits_user', 'execution_audits', ['user_id'])

    op.create_index('idx_publications_platform', 'publications', ['platform'])
    op.create_index('idx_publications_status', 'publications', ['publication_status'])
    op.create_index('idx_publications_scheduled', 'publications', ['scheduled_at'])
    op.create_index('idx_publications_published', 'publications', ['published_at'])


def downgrade() -> None:
    """Drop data platform tables"""
    op.drop_table('publications')
    op.drop_table('execution_audits')
    op.drop_table('artifacts')
    op.drop_table('data_exports')