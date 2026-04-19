"""Graph edge labels and node field predicates used by the indexer."""

# --- Structural relations (original) ---
DEFINES = "DEFINES"
CALLS = "CALLS"
IMPORTS = "IMPORTS"
IN_FILE = "IN_FILE"
AT_LINE = "AT_LINE"
INHERITS = "INHERITS"

# --- New relation types (Fase 1) ---
TESTS = "TESTS"                        # test_symbol TESTS target_symbol
CONFIGURES = "CONFIGURES"              # config_node CONFIGURES module_node
ENTRYPOINT_FOR = "ENTRYPOINT_FOR"      # module ENTRYPOINT_FOR service_name
BELONGS_TO_SERVICE = "BELONGS_TO_SERVICE"  # symbol BELONGS_TO_SERVICE service_name
DEPENDS_ON_RUNTIME = "DEPENDS_ON_RUNTIME"  # module DEPENDS_ON_RUNTIME dep
IMPLEMENTS_INTERFACE = "IMPLEMENTS_INTERFACE"  # class IMPLEMENTS_INTERFACE interface
MENTIONED_IN_DOC = "MENTIONED_IN_DOC"  # symbol MENTIONED_IN_DOC doc_path

# --- Node field predicates (Fase 1) ---
SIGNATURE = "signature"        # source signature of function/class
SERVICE_NAME = "service_name"  # inferred service/subsystem name
RISK_LEVEL = "risk_level"      # low | medium | high
IS_TEST = "is_test"            # "true" if symbol lives in a test file
IS_ENTRYPOINT = "is_entrypoint"  # "true" if file is a recognised entrypoint

# --- Summary predicates (Fase 2) — written by external consumers, not by RepoGraph ---
SHORT_SUMMARY = "short_summary"    # L3: per-symbol one-liner
FILE_SUMMARY = "file_summary"      # L2: per-file paragraph
SERVICE_SUMMARY = "service_summary"  # L1: per-service overview
REPO_SUMMARY = "repo_summary"      # L0: whole-repo overview

# Sentinel node for repo-level and service-level summary storage
REPO_NODE = "__repo__"

# --- Knowledge graph predicates (Fase 8) ---
OWNED_BY = "OWNED_BY"              # file/symbol OWNED_BY team_or_person
DOCUMENTED_BY = "DOCUMENTED_BY"   # symbol DOCUMENTED_BY doc_node_id
CI_COVERS = "CI_COVERS"            # ci_job_node CI_COVERS file
ADR_DECIDES = "ADR_DECIDES"        # adr_node ADR_DECIDES symbol_or_module
DOC_TYPE = "doc_type"              # doc | adr | runbook | ci_workflow
DOC_TITLE = "doc_title"            # human-readable title of a doc node
CI_JOB_NAME = "ci_job_name"        # name of a CI job node
