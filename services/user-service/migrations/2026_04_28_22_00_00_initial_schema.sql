-- Initial user-service database schema
-- Created: 2026-04-28 22:00:00

-- =====================================================
-- FAMILIES (CORE FAMILY IDENTITY)
-- =====================================================

CREATE TABLE families (
    family_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_name VARCHAR(100) NOT NULL,
    family_slug VARCHAR(50) UNIQUE NOT NULL,
    owner_user_id UUID NOT NULL,
    max_members INTEGER NOT NULL DEFAULT 4 CHECK (max_members > 0 AND max_members <= 10),
    subscription_tier VARCHAR(50) NOT NULL DEFAULT 'free' CHECK (subscription_tier IN ('free', 'pro')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for families
CREATE INDEX idx_families_owner_user_id ON families(owner_user_id);
CREATE INDEX idx_families_created_at ON families(created_at DESC);
CREATE INDEX idx_families_subscription_tier ON families(subscription_tier);

-- =====================================================
-- USERS (FAMILY MEMBERS)
-- =====================================================

CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(50) NOT NULL,
    display_name VARCHAR(100),
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    email_verified BOOLEAN NOT NULL DEFAULT false,
    email_verification_token VARCHAR(255),
    email_verification_expires TIMESTAMP WITH TIME ZONE,
    password_reset_token VARCHAR(255),
    password_reset_expires TIMESTAMP WITH TIME ZONE,
    last_login TIMESTAMP WITH TIME ZONE,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for users
CREATE INDEX idx_users_family_id ON users(family_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_active ON users(active);
CREATE INDEX idx_users_created_at ON users(created_at DESC);
CREATE INDEX idx_users_email_verification_token ON users(email_verification_token) WHERE email_verification_token IS NOT NULL;
CREATE INDEX idx_users_password_reset_token ON users(password_reset_token) WHERE password_reset_token IS NOT NULL;

-- Unique constraint for username within family
CREATE UNIQUE INDEX idx_users_family_username ON users(family_id, username);

-- =====================================================
-- USER SESSIONS (AUTHENTICATION)
-- =====================================================

CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    family_id UUID NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    refresh_token_hash TEXT,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    refresh_expires_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    last_used TIMESTAMP WITH TIME ZONE,
    user_agent TEXT,
    ip_address INET,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for sessions
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_family_id ON user_sessions(family_id);
CREATE INDEX idx_user_sessions_token_hash ON user_sessions(token_hash);
CREATE INDEX idx_user_sessions_expires_at ON user_sessions(expires_at);
CREATE INDEX idx_user_sessions_active ON user_sessions(user_id, expires_at DESC)
WHERE revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP;

-- =====================================================
-- FAMILY INVITATIONS
-- =====================================================

CREATE TABLE family_invites (
    invite_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'viewer')),
    invite_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE,
    accepted_by UUID,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_by UUID,
    created_by UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for invites
CREATE INDEX idx_family_invites_family_id ON family_invites(family_id);
CREATE INDEX idx_family_invites_email ON family_invites(email);
CREATE INDEX idx_family_invites_invite_token_hash ON family_invites(invite_token_hash);
CREATE INDEX idx_family_invites_expires_at ON family_invites(expires_at);
CREATE INDEX idx_family_invites_created_by ON family_invites(created_by);

-- Prevent duplicate pending invites
CREATE UNIQUE INDEX idx_family_invites_unique_pending ON family_invites(family_id, email)
WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP;

-- =====================================================
-- USER PERMISSIONS (GRANULAR ACCESS CONTROL)
-- =====================================================

CREATE TABLE user_permissions (
    permission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    family_id UUID NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(255),
    permission VARCHAR(100) NOT NULL,
    granted_by UUID NOT NULL,
    granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_by UUID
);

-- Indexes for permissions
CREATE INDEX idx_user_permissions_user_id ON user_permissions(user_id);
CREATE INDEX idx_user_permissions_family_id ON user_permissions(family_id);
CREATE INDEX idx_user_permissions_resource ON user_permissions(resource_type, resource_id);
CREATE INDEX idx_user_permissions_active ON user_permissions(user_id, resource_type, permission)
WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link users to families
ALTER TABLE users
ADD CONSTRAINT fk_users_family_id
FOREIGN KEY (family_id) REFERENCES families(family_id)
ON DELETE CASCADE;

-- Link sessions to users and families
ALTER TABLE user_sessions
ADD CONSTRAINT fk_user_sessions_user_id
FOREIGN KEY (user_id) REFERENCES users(user_id)
ON DELETE CASCADE;

ALTER TABLE user_sessions
ADD CONSTRAINT fk_user_sessions_family_id
FOREIGN KEY (family_id) REFERENCES families(family_id)
ON DELETE CASCADE;

-- Link invites to families and users
ALTER TABLE family_invites
ADD CONSTRAINT fk_family_invites_family_id
FOREIGN KEY (family_id) REFERENCES families(family_id)
ON DELETE CASCADE;

ALTER TABLE family_invites
ADD CONSTRAINT fk_family_invites_created_by
FOREIGN KEY (created_by) REFERENCES users(user_id)
ON DELETE CASCADE;

ALTER TABLE family_invites
ADD CONSTRAINT fk_family_invites_accepted_by
FOREIGN KEY (accepted_by) REFERENCES users(user_id)
ON DELETE SET NULL;

ALTER TABLE family_invites
ADD CONSTRAINT fk_family_invites_revoked_by
FOREIGN KEY (revoked_by) REFERENCES users(user_id)
ON DELETE SET NULL;

-- Link permissions to users and families
ALTER TABLE user_permissions
ADD CONSTRAINT fk_user_permissions_user_id
FOREIGN KEY (user_id) REFERENCES users(user_id)
ON DELETE CASCADE;

ALTER TABLE user_permissions
ADD CONSTRAINT fk_user_permissions_family_id
FOREIGN KEY (family_id) REFERENCES families(family_id)
ON DELETE CASCADE;

ALTER TABLE user_permissions
ADD CONSTRAINT fk_user_permissions_granted_by
FOREIGN KEY (granted_by) REFERENCES users(user_id)
ON DELETE CASCADE;

ALTER TABLE user_permissions
ADD CONSTRAINT fk_user_permissions_revoked_by
FOREIGN KEY (revoked_by) REFERENCES users(user_id)
ON DELETE SET NULL;

-- Link family owner to user
ALTER TABLE families
ADD CONSTRAINT fk_families_owner_user_id
FOREIGN KEY (owner_user_id) REFERENCES users(user_id)
DEFERRABLE INITIALLY DEFERRED;

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_families_updated_at
    BEFORE UPDATE ON families
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Update last_used on session access
CREATE OR REPLACE FUNCTION update_session_last_used()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_used = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Auto-cleanup expired sessions
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM user_sessions
    WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '7 days';

    DELETE FROM family_invites
    WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '7 days';
END;
$$ language 'plpgsql';

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure family member count doesn't exceed limit
CREATE OR REPLACE FUNCTION check_family_member_limit()
RETURNS TRIGGER AS $$
DECLARE
    current_count INTEGER;
    max_allowed INTEGER;
BEGIN
    SELECT COUNT(*), f.max_members
    INTO current_count, max_allowed
    FROM users u
    JOIN families f ON f.family_id = u.family_id
    WHERE u.family_id = NEW.family_id AND u.active = true
    GROUP BY f.max_members;

    IF current_count >= max_allowed THEN
        RAISE EXCEPTION 'Family has reached maximum member limit of %', max_allowed;
    END IF;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER check_family_member_limit_trigger
    BEFORE INSERT ON users
    FOR EACH ROW
    EXECUTE FUNCTION check_family_member_limit();

-- Ensure invite expires in future
ALTER TABLE family_invites ADD CONSTRAINT check_invite_expiration_future
CHECK (expires_at > created_at);

-- Ensure session expires in future
ALTER TABLE user_sessions ADD CONSTRAINT check_session_expiration_future
CHECK (expires_at > created_at);

-- Ensure refresh token expires after access token
ALTER TABLE user_sessions ADD CONSTRAINT check_refresh_expiration_order
CHECK (refresh_expires_at IS NULL OR refresh_expires_at >= expires_at);

-- Ensure valid email format (basic check)
ALTER TABLE users ADD CONSTRAINT check_email_format
CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$');

ALTER TABLE family_invites ADD CONSTRAINT check_invite_email_format
CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$');

-- Ensure family slug is URL-safe
ALTER TABLE families ADD CONSTRAINT check_family_slug_format
CHECK (family_slug ~* '^[a-z0-9-]+$');

-- Ensure username is valid
ALTER TABLE users ADD CONSTRAINT check_username_format
CHECK (username ~* '^[a-zA-Z0-9_-]+$');

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Partial indexes for active content
CREATE INDEX idx_users_active_family ON users(family_id, role, created_at DESC)
WHERE active = true;

CREATE INDEX idx_sessions_active_user ON user_sessions(user_id, created_at DESC)
WHERE revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP;

CREATE INDEX idx_invites_pending_family ON family_invites(family_id, created_at DESC)
WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP;

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE families IS 'Family identity and subscription management';
COMMENT ON TABLE users IS 'Family members with roles and authentication';
COMMENT ON TABLE user_sessions IS 'Active user sessions with security tokens';
COMMENT ON TABLE family_invites IS 'Pending family invitation management';
COMMENT ON TABLE user_permissions IS 'Granular resource-level permissions';

COMMENT ON COLUMN families.max_members IS 'Maximum allowed family members (enforced by trigger)';
COMMENT ON COLUMN families.subscription_tier IS 'Family subscription level affecting features';
COMMENT ON COLUMN users.role IS 'Family role: owner, admin, member, viewer';
COMMENT ON COLUMN user_sessions.token_hash IS 'Hashed JWT token for security (not plain JWT)';
COMMENT ON COLUMN family_invites.invite_token_hash IS 'Hashed invite token for security';
COMMENT ON COLUMN user_permissions.resource_type IS 'Type of resource: repo, adapter, scan, etc.';