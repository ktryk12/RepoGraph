"""
UI Service Database Models

Dedicated database models for ui-service following database-per-service pattern.
Handles user sessions, dashboard state, and WebSocket connections.
"""

from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

Base = declarative_base()


class UserSession(Base):
    """
    User sessions for UI service
    """
    __tablename__ = 'user_sessions'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Session identification
    session_id = Column(String(100), nullable=False, unique=True, index=True)
    user_id = Column(String(100), nullable=False, index=True)
    username = Column(String(100), nullable=True)

    # Session state
    status = Column(String(50), nullable=False, default='active')  # active, inactive, expired, terminated
    session_data = Column(JSON, nullable=True)  # User preferences, temporary data
    authentication_method = Column(String(50), nullable=True)

    # User preferences
    theme = Column(String(50), nullable=False, default='light')  # light, dark, auto
    language = Column(String(10), nullable=False, default='en')
    timezone = Column(String(50), nullable=True)
    dashboard_layout = Column(JSON, nullable=True)
    notification_preferences = Column(JSON, nullable=True)

    # Connection metadata
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent = Column(String(500), nullable=True)
    browser = Column(String(100), nullable=True)
    operating_system = Column(String(100), nullable=True)
    screen_resolution = Column(String(20), nullable=True)

    # Activity tracking
    last_activity_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    page_views = Column(Integer, nullable=False, default=0)
    actions_performed = Column(Integer, nullable=False, default=0)
    time_spent_seconds = Column(Integer, nullable=False, default=0)

    # Security
    csrf_token = Column(String(64), nullable=True)
    security_flags = Column(JSON, nullable=True)
    login_attempts = Column(Integer, nullable=False, default=0)
    last_failed_login = Column(DateTime, nullable=True)

    # Expiration
    expires_at = Column(DateTime, nullable=True)
    max_idle_minutes = Column(Integer, nullable=False, default=60)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<UserSession(session_id={self.session_id}, user_id={self.user_id}, status={self.status})>"


class WebSocketConnection(Base):
    """
    WebSocket connections for real-time communication
    """
    __tablename__ = 'websocket_connections'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Connection identification
    connection_id = Column(String(100), nullable=False, unique=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)  # Link to user session
    user_id = Column(String(100), nullable=True, index=True)

    # Connection state
    status = Column(String(50), nullable=False, default='connected')  # connected, disconnected, error
    connection_type = Column(String(50), nullable=False, default='websocket')
    protocol_version = Column(String(20), nullable=True)

    # Subscriptions
    subscribed_channels = Column(JSON, nullable=False, default=list)  # ["dashboard", "jobs", "notifications"]
    subscription_filters = Column(JSON, nullable=True)  # User-specific filters

    # Message statistics
    messages_sent = Column(Integer, nullable=False, default=0)
    messages_received = Column(Integer, nullable=False, default=0)
    bytes_sent = Column(Integer, nullable=False, default=0)
    bytes_received = Column(Integer, nullable=False, default=0)

    # Performance metrics
    avg_response_time_ms = Column(Float, nullable=True)
    last_ping_time_ms = Column(Float, nullable=True)
    connection_quality = Column(String(20), nullable=False, default='good')  # excellent, good, fair, poor

    # Error tracking
    error_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)

    # Connection metadata
    remote_address = Column(String(45), nullable=True)
    client_info = Column(JSON, nullable=True)

    # Activity
    last_message_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Timestamps
    connected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    disconnected_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<WebSocketConnection(connection_id={self.connection_id}, status={self.status})>"


class DashboardWidget(Base):
    """
    Dashboard widgets and their configurations
    """
    __tablename__ = 'dashboard_widgets'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Widget identification
    widget_id = Column(String(100), nullable=False, index=True)
    widget_type = Column(String(50), nullable=False)  # "chart", "table", "metric", "status", "log"
    widget_name = Column(String(100), nullable=False)
    widget_title = Column(String(200), nullable=False)

    # User/Session association
    user_id = Column(String(100), nullable=True, index=True)  # null = global widget
    session_id = Column(String(100), nullable=True, index=True)  # session-specific override

    # Layout
    position_x = Column(Integer, nullable=False, default=0)
    position_y = Column(Integer, nullable=False, default=0)
    width = Column(Integer, nullable=False, default=4)
    height = Column(Integer, nullable=False, default=3)
    z_index = Column(Integer, nullable=False, default=0)

    # Configuration
    config = Column(JSON, nullable=False)  # Widget-specific configuration
    data_source = Column(String(100), nullable=False)  # "video-service", "voice-service", "system"
    refresh_interval_seconds = Column(Integer, nullable=False, default=30)
    auto_refresh_enabled = Column(Boolean, nullable=False, default=True)

    # State
    is_visible = Column(Boolean, nullable=False, default=True)
    is_interactive = Column(Boolean, nullable=False, default=True)
    is_minimized = Column(Boolean, nullable=False, default=False)

    # Cache
    cached_data = Column(JSON, nullable=True)
    cache_expires_at = Column(DateTime, nullable=True)
    last_data_update = Column(DateTime, nullable=True)

    # Performance
    load_time_ms = Column(Integer, nullable=True)
    error_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_viewed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<DashboardWidget(widget_id={self.widget_id}, type={self.widget_type})>"


class UIEvent(Base):
    """
    UI events and user interactions tracking
    """
    __tablename__ = 'ui_events'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Event identification
    event_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)  # "click", "view", "navigation", "error"
    event_category = Column(String(50), nullable=False)  # "user_action", "system_event", "error"

    # User context
    user_id = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    connection_id = Column(String(100), nullable=True)

    # Event details
    page_url = Column(String(500), nullable=True)
    page_title = Column(String(200), nullable=True)
    element_id = Column(String(100), nullable=True)
    element_type = Column(String(50), nullable=True)
    action_performed = Column(String(100), nullable=True)

    # Event data
    event_data = Column(JSON, nullable=True)
    metadata = Column(JSON, nullable=True)

    # Performance metrics
    response_time_ms = Column(Integer, nullable=True)
    server_processing_time_ms = Column(Integer, nullable=True)

    # Error information (if event_type = "error")
    error_message = Column(Text, nullable=True)
    error_code = Column(String(50), nullable=True)
    stack_trace = Column(Text, nullable=True)

    # Client information
    browser_info = Column(JSON, nullable=True)
    screen_info = Column(JSON, nullable=True)
    device_info = Column(JSON, nullable=True)

    # Timestamps
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<UIEvent(event_type={self.event_type}, user_id={self.user_id})>"


class Notification(Base):
    """
    Notifications for users in the UI
    """
    __tablename__ = 'notifications'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Notification identification
    notification_id = Column(String(100), nullable=False, unique=True, index=True)
    notification_type = Column(String(50), nullable=False)  # "info", "warning", "error", "success"
    priority = Column(Integer, nullable=False, default=5)  # 1 = low, 10 = critical

    # Target
    user_id = Column(String(100), nullable=True, index=True)  # null = broadcast to all users
    session_id = Column(String(100), nullable=True, index=True)  # session-specific notification
    target_channels = Column(JSON, nullable=True)  # WebSocket channels to send to

    # Content
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    action_url = Column(String(500), nullable=True)
    action_text = Column(String(100), nullable=True)

    # Behavior
    is_dismissible = Column(Boolean, nullable=False, default=True)
    auto_dismiss_seconds = Column(Integer, nullable=True)  # null = manual dismiss only
    requires_acknowledgment = Column(Boolean, nullable=False, default=False)

    # State
    status = Column(String(50), nullable=False, default='pending')  # pending, sent, delivered, dismissed, expired
    is_read = Column(Boolean, nullable=False, default=False)
    read_at = Column(DateTime, nullable=True)
    dismissed_at = Column(DateTime, nullable=True)

    # Source information
    source_service = Column(String(50), nullable=True)  # "video-service", "voice-service", etc.
    source_event_id = Column(String(100), nullable=True)
    correlation_id = Column(String(100), nullable=True)

    # Delivery tracking
    delivery_attempts = Column(Integer, nullable=False, default=0)
    last_delivery_attempt = Column(DateTime, nullable=True)
    delivery_method = Column(String(50), nullable=True)  # "websocket", "push", "email"

    # Expiration
    expires_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    scheduled_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Notification(notification_id={self.notification_id}, type={self.notification_type})>"


class UIMetric(Base):
    """
    UI performance and usage metrics
    """
    __tablename__ = 'ui_metrics'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Metric identification
    metric_name = Column(String(100), nullable=False, index=True)
    metric_type = Column(String(50), nullable=False)  # "counter", "gauge", "histogram", "timer"
    category = Column(String(50), nullable=False)  # "performance", "usage", "error", "business"

    # Context
    user_id = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    page_url = Column(String(500), nullable=True)

    # Metric data
    metric_value = Column(Float, nullable=False)
    metric_unit = Column(String(20), nullable=True)  # "ms", "count", "percent", "bytes"
    dimensions = Column(JSON, nullable=True)  # Additional metric dimensions

    # Aggregation support
    count = Column(Integer, nullable=False, default=1)
    sum = Column(Float, nullable=False, default=0.0)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)

    # Timestamps
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<UIMetric(name={self.metric_name}, value={self.metric_value}, type={self.metric_type})>"


# Database schema version for migrations
SCHEMA_VERSION = "1.0.0"