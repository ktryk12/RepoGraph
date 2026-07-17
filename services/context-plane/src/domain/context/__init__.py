"""
Context Domain Package

Contains all context-related domain logic, use cases, and infrastructure
that was previously imported from AESA packages.
"""

from .retrieve_context import (
    AgentContextRequest,
    AgentContextResponse,
    AgentRetrieveContextUseCase,
    ExpertServingStrategyEngine,
    RetrieveContextRequest,
    RetrieveContextUseCase,
    ContextResult,
    create_agent_retrieve_context_use_case,
    create_retrieve_context_use_case
)

from .bootstrap import (
    ContextPlaneRuntime,
    build_context_plane_runtime,
    context_plane_store_backend,
    context_plane_db_path,
    context_plane_artifact_root,
    validate_context_plane_runtime
)

from .contracts import (
    ContextPlaneContractValidationError,
    IngestContractValidationError,
    IngestContractsService,
    get_ingest_contracts_service,
    validate_context_plane_contract,
    HEALTH_RESPONSE,
    INGEST_REQUEST,
    INGEST_RESPONSE,
    RETRIEVE_REQUEST,
    RETRIEVE_RESPONSE
)

from .infrastructure import (
    ExpertServingSummaryEngine,
    SQLiteContextStorePortAdapter,
    IndexedFile,
    RepositoryIndex,
    estimate_repository_files,
    index_repository
)

__all__ = [
    # Use cases and core classes
    'AgentContextRequest',
    'AgentContextResponse',
    'AgentRetrieveContextUseCase',
    'ExpertServingStrategyEngine',
    'RetrieveContextRequest',
    'RetrieveContextUseCase',
    'ContextResult',
    'create_agent_retrieve_context_use_case',
    'create_retrieve_context_use_case',

    # Bootstrap and configuration
    'ContextPlaneRuntime',
    'build_context_plane_runtime',
    'context_plane_store_backend',
    'context_plane_db_path',
    'context_plane_artifact_root',
    'validate_context_plane_runtime',

    # Contracts and validation
    'ContextPlaneContractValidationError',
    'IngestContractValidationError',
    'IngestContractsService',
    'get_ingest_contracts_service',
    'validate_context_plane_contract',
    'HEALTH_RESPONSE',
    'INGEST_REQUEST',
    'INGEST_RESPONSE',
    'RETRIEVE_REQUEST',
    'RETRIEVE_RESPONSE',

    # Infrastructure
    'ExpertServingSummaryEngine',
    'SQLiteContextStorePortAdapter',
    'IndexedFile',
    'RepositoryIndex',
    'estimate_repository_files',
    'index_repository'
]