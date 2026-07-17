# Phase 7 Database and Events Implementation

## Overview

This document outlines the complete database migrations and Kafka event schemas for Phase 7 - Family System for GitHub URL processing with security scanning and assisted adapter generation.

## Architecture Summary

### Service Architecture
- **user-service**: Family identity, authentication, and permissions
- **repo-intake-service**: GitHub URL orchestration and security scan coordination  
- **repo-wrapper-service**: Assisted adapter generation and user approval workflow
- **5 Scanner Services**: Stateless security scanners (malware, license, CVE, content-policy, prompt-injection)
- **ui-gateway**: Backend-for-frontend (stateless initially)
- **web-ui**: Frontend interface

### Data Ownership Strategy
```
user-service: PostgreSQL (families, users, sessions, invites, permissions)
repo-intake-service: PostgreSQL (intakes, artifacts, scan_jobs, scan_results, scan_summaries)  
repo-wrapper-service: PostgreSQL (adapter_generations, templates, registrations, analytics)
scanner-services: Stateless workers (no databases initially)
ui-gateway: Stateless (Redis cache optional later)
```

## Database Migrations

### 1. user-service Database

**Purpose**: Family-scoped authentication and user management

**Key Tables**:
- `families` - Family identity with subscription tiers and member limits
- `users` - Family members with roles (owner/admin/member/viewer)
- `user_sessions` - Secure session management with token hashing
- `family_invites` - Invitation workflow with expiration
- `user_permissions` - Granular resource-level permissions

**Security Features**:
- Password hashing (never store plain passwords)
- Token hashing in sessions (never store raw JWTs)
- Family member limits enforced by triggers
- Email validation and verification workflow
- Session expiration and revocation

**Database**: `user_service_db`

### 2. repo-intake-service Database

**Purpose**: GitHub repository intake orchestration and security scan coordination

**Key Tables**:
- `repo_intakes` - Main workflow state machine for repository processing
- `repo_artifacts` - Downloaded repository snapshots for scanner processing
- `scan_jobs` - Individual security scanner job tracking
- `scan_results` - Detailed security findings from scanners
- `scan_summaries` - Aggregated security reports per repository

**Workflow States**:
```
pending → validating_url → fetching → artifact_ready → 
scanning → scan_completed → wrapper_requested → 
draft_ready → approved/rejected
```

**Security Integration**:
- GitHub URL validation (SSRF protection)
- Advisory/approval/block action thresholds per scanner
- Centralized scan result aggregation
- Progress tracking and timeout handling

**Database**: `repo_intake_service_db`

### 3. repo-wrapper-service Database

**Purpose**: Assisted adapter generation with user review and approval

**Key Tables**:
- `adapter_generations` - Main adapter generation workflow
- `adapter_templates` - Reusable patterns for common repository types
- `user_modifications` - Track user changes during review
- `adapter_registrations` - Final registered adapters
- `generation_analytics` - Performance and quality metrics

**Generation Workflow**:
```
analyzing → drafting → pending_user_review → 
user_modifying → approved → registered
```

**Quality Controls**:
- Template matching and confidence scoring
- User modification tracking
- Security approval requirements
- Quality metrics and success rate tracking

**Database**: `repo_wrapper_service_db`

## Kafka Event Schemas

### Event Flow Architecture

```
repo-intake-service → security.scan.requested.v1 → scanner-services
scanner-services → security.scan.completed.v1 → repo-intake-service
repo-intake-service → security.scan.summary.ready.v1 → repo-wrapper-service
repo-wrapper-service → adapter.draft.ready.v1 → ui-gateway
ui-gateway → user approval → adapter.approved.v1 → repo-wrapper-service
```

### Core Events

1. **Repository Intake Events**
   - `repo.intake.created.v1` - New GitHub URL submitted
   - `repo.intake.validated.v1` - URL validation completed
   - `repo.artifact.created.v1` - Repository downloaded and ready

2. **Security Scanning Events**
   - `security.scan.requested.v1` - Scanner job dispatched
   - `security.scan.completed.v1` - Scanner results available
   - `security.scan.failed.v1` - Scanner encountered error
   - `security.scan.summary.ready.v1` - All scanners completed, summary ready

3. **Adapter Generation Events**
   - `adapter.draft.requested.v1` - Request adapter generation
   - `adapter.draft.ready.v1` - Draft ready for user review
   - `adapter.approved.v1` - User approved adapter
   - `adapter.rejected.v1` - User rejected adapter
   - `adapter.registered.v1` - Adapter registered in babyAI

### Event Routing

**Topics**:
- `repo-intake` - Repository processing events
- `security-scanning` - Security scanner coordination
- `adapter-generation` - Adapter creation and approval

**Scanner Integration**:
All scanners follow identical input/output contracts:
- Input: `security.scan.requested.v1` with artifact_uri and config
- Output: `security.scan.completed.v1` with structured findings
- Error: `security.scan.failed.v1` with retry information

## Security Thresholds

### Default Scanner Configuration

```json
{
  "malware-scanner": {
    "action_on_critical": "block",
    "critical_threshold": 1
  },
  "cve-scanner": {
    "action_on_critical": "approval_required", 
    "critical_threshold": 3
  },
  "license-checker": {
    "action_on_critical": "approval_required",
    "critical_threshold": 1  
  },
  "content-policy-checker": {
    "action_on_critical": "approval_required",
    "critical_threshold": 1
  },
  "prompt-injection-detector": {
    "action_on_critical": "advisory",
    "critical_threshold": 2
  }
}
```

### Action Types
- **advisory** - Show warning, allow proceed
- **approval_required** - Require explicit user approval
- **block** - Hard stop, prevent adapter generation

## Implementation Phases

### Phase 7A - Foundation (Week 1-2)
- Database migrations for user-service, repo-intake-service, repo-wrapper-service
- Kafka topics and basic event producers/consumers
- Service skeletons with health checks
- Basic authentication and GitHub URL validation

### Phase 7B - Repository Processing (Week 3-4)  
- GitHub repository cloning and artifact creation
- Scanner job dispatch and coordination
- Basic scan result aggregation
- Progress tracking and error handling

### Phase 7C - Scanner Fleet (Week 5-6)
- Implement all 5 security scanners as stateless services
- Scanner input/output contract standardization
- Security threshold evaluation
- Advisory/approval/block logic

### Phase 7D - Adapter Generation (Week 7-8)
- Repository analysis and template matching
- Adapter draft generation
- User review interface in web-ui
- User modification tracking

### Phase 7E - Production Readiness (Week 9-10)
- Rate limiting and resource constraints
- Comprehensive error handling and retries
- Audit logging and monitoring
- Family user acceptance testing

## Migration Commands

### Setup Phase 7 Databases

```bash
# Create databases
createdb user_service_db
createdb repo_intake_service_db  
createdb repo_wrapper_service_db

# Run migrations
cd services/user-service/migrations && alembic upgrade head
cd services/repo-intake-service/migrations && alembic upgrade head
cd services/repo-wrapper-service/migrations && alembic upgrade head
```

### Kafka Topics Setup

```bash
# Create topics with appropriate partitions and replication
kafka-topics.sh --create --topic repo-intake --partitions 3 --replication-factor 1
kafka-topics.sh --create --topic security-scanning --partitions 5 --replication-factor 1
kafka-topics.sh --create --topic adapter-generation --partitions 3 --replication-factor 1
```

## Key Design Decisions

### 1. Stateless Scanners
- Scanners have no databases initially to reduce operational complexity
- repo-intake-service owns all scan state and results
- Scanners can add caching/persistence later without breaking contracts

### 2. Family-Scoped Security  
- All operations scoped to family_id for data isolation
- No multi-tenancy complexity, single-family deployments
- User roles within family: owner/admin/member/viewer

### 3. Advisory-First Security
- Default to advisory warnings rather than hard blocks
- User can configure thresholds per scanner type
- Security findings inform but don't prevent adapter generation by default

### 4. Assisted Adapter Generation
- AI generates draft, user reviews and modifies
- No automatic deployment without user approval
- Track user modifications for quality feedback loops

### 5. Event-Driven Architecture
- All inter-service communication via Kafka events
- Services can scale and deploy independently  
- Clear event contracts with versioning
- Correlation IDs for distributed tracing

## Data Consistency Patterns

### 1. Event Sourcing
- Scan results are immutable once created
- Status changes tracked via events
- Full audit trail for all user actions

### 2. Saga Pattern
- Repository intake workflow spans multiple services
- Compensating actions for failure scenarios
- Progress tracking with timeout handling

### 3. CQRS
- Write operations via service APIs
- Read operations via optimized views
- Event handlers update read models

## Monitoring and Observability

### Key Metrics to Track
- Repository intake success/failure rates
- Scanner performance and error rates
- User approval/rejection rates for adapters
- Family activity and adoption metrics
- Security finding trends and patterns

### Health Checks
- Database connectivity per service
- Kafka producer/consumer health
- Scanner service availability
- Artifact storage accessibility

## Security Considerations

### 1. Input Validation
- GitHub URL whitelist (only github.com)
- SSRF protection in repository cloning
- File size and count limits for artifacts
- Malicious content detection before processing

### 2. Data Protection
- Password hashing with bcrypt/argon2
- JWT token hashing in database storage
- Sensitive data encryption at rest
- Audit logging for all user actions

### 3. Access Control
- Family-scoped data isolation
- Role-based permissions within families
- Session management with proper expiration
- API rate limiting per family

This implementation provides a robust foundation for Phase 7 while maintaining the pragmatic approach of starting with essential databases and adding complexity as needed.