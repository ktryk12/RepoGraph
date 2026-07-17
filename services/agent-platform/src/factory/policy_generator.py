"""
Phase 7 Policy Generator

Handles YAML policy generation and management of the 6 standard profiles.
Stage 1: Focus on profile deployment and basic policy templates.
"""

import logging
import yaml
from typing import Dict, Any, Optional, List
from datetime import datetime

from .state_machine import (
    PolicyConfig, PolicyBudget, PolicyObligation, CapabilityConstraint,
    ProfileType, RiskTier
)

logger = logging.getLogger(__name__)


class PolicyGenerator:
    """
    Policy generator for Phase 7 dynamic policy system

    Manages:
    - 6 standard profiles (readonly, research, editor, builder, operator, admin_assisted)
    - Dynamic policy generation based on intent and context
    - Policy validation and constraint management
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.deployed_profiles = {}
        self._policy_templates = {}

        logger.info("Policy Generator initialized")

    async def initialize(self) -> None:
        """Initialize policy generator and load profile templates"""
        try:
            # Load standard profile templates
            self._load_profile_templates()

            logger.info("Policy Generator initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize policy generator: {e}")
            raise

    def _load_profile_templates(self) -> None:
        """Load the 6 standard profile templates"""

        # Base profile - deny everything by default
        self._policy_templates["base"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "base",
                "layer": "base",
                "version": "2026.04.22-1",
                "risk_tier": "low"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {"effect": "deny", "reason": "default"},
                "write_code": {"effect": "deny", "reason": "default"},
                "browse_web": {"effect": "deny", "reason": "default"},
                "run_cli": {"effect": "deny", "reason": "default"},
                "spawn_agents": {"effect": "deny", "reason": "default"},
                "access_sensitive_data": {"effect": "deny", "reason": "default"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision", "layer", "rule_id"]}
            ],
            "budget": {
                "token_budget": 0,
                "wallclock_seconds": 0,
                "cost_usd": 0.0
            }
        }

        # Readonly profile - for codebase analysis
        self._policy_templates["readonly"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/readonly",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "low"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["${context.repo_root}/**"],
                        "exclude_globs": ["**/.env*", "**/secrets/**", "**/*.pem", "**/id_rsa*"],
                        "max_file_bytes": 2_000_000
                    }
                },
                "write_code": {"effect": "deny", "reason": "readonly profile"},
                "browse_web": {"effect": "deny", "reason": "readonly profile"},
                "run_cli": {
                    "effect": "allow",
                    "constraints": {
                        "binaries": ["rg", "git", "tokei", "semgrep", "gitleaks", "find", "wc", "grep", "cat", "ls"],
                        "args_deny_regex": ["--exec", ";.*", "\\|.*", ">.*", ">>.*"],
                        "network": False,
                        "max_runtime_seconds": 30
                    }
                },
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 1,
                        "child_profiles": ["readonly"]
                    }
                },
                "access_sensitive_data": {"effect": "deny"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision", "layer", "rule_id"]},
                {"type": "rate_limit", "capability": "run_cli", "per_session": 100}
            ],
            "budget": {
                "token_budget": 200_000,
                "wallclock_seconds": 900,
                "cost_usd": 0.50
            }
        }

        # Research profile - web browsing + reading
        self._policy_templates["research"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/research",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "low"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["${context.repo_root}/**"],
                        "exclude_globs": ["**/.env*", "**/secrets/**"],
                        "max_file_bytes": 1_000_000
                    }
                },
                "write_code": {"effect": "deny", "reason": "research profile"},
                "browse_web": {
                    "effect": "allow",
                    "constraints": {
                        "allowed_domains": ["github.com", "stackoverflow.com", "docs.python.org", "developer.mozilla.org"],
                        "max_requests_per_hour": 50
                    }
                },
                "run_cli": {
                    "effect": "allow",
                    "constraints": {
                        "binaries": ["curl", "wget", "git", "grep", "find"],
                        "network": True,
                        "max_runtime_seconds": 60
                    }
                },
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 2,
                        "child_profiles": ["readonly", "research"]
                    }
                },
                "access_sensitive_data": {"effect": "deny"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision"]},
                {"type": "rate_limit", "capability": "browse_web", "per_session": 50}
            ],
            "budget": {
                "token_budget": 300_000,
                "wallclock_seconds": 1800,
                "cost_usd": 1.0
            }
        }

        # Editor profile - small code changes
        self._policy_templates["editor"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/editor",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "medium"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["${context.repo_root}/**"],
                        "exclude_globs": ["**/.env*", "**/secrets/**"]
                    }
                },
                "write_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["${context.repo_root}/**"],
                        "exclude_globs": ["**/migrations/**", "**/production/**", "**/.env*"],
                        "max_file_changes": 10,
                        "backup_required": True
                    }
                },
                "browse_web": {
                    "effect": "allow",
                    "constraints": {
                        "allowed_domains": ["github.com", "stackoverflow.com"],
                        "max_requests_per_hour": 20
                    }
                },
                "run_cli": {
                    "effect": "allow",
                    "constraints": {
                        "binaries": ["git", "npm", "pip", "pytest", "eslint"],
                        "network": True,
                        "max_runtime_seconds": 120
                    }
                },
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 1,
                        "child_profiles": ["readonly"]
                    }
                },
                "access_sensitive_data": {"effect": "deny"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision"]},
                {"type": "approval_required", "capability": "write_code", "threshold": 5}
            ],
            "budget": {
                "token_budget": 500_000,
                "wallclock_seconds": 3600,
                "cost_usd": 2.0
            }
        }

        # Builder profile - significant development
        self._policy_templates["builder"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/builder",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "high"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {"effect": "allow"},
                "write_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["${context.repo_root}/**"],
                        "exclude_globs": ["**/production/**"],
                        "backup_required": True
                    }
                },
                "browse_web": {
                    "effect": "allow",
                    "constraints": {
                        "max_requests_per_hour": 100
                    }
                },
                "run_cli": {
                    "effect": "allow",
                    "constraints": {
                        "network": True,
                        "max_runtime_seconds": 600
                    }
                },
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 3,
                        "child_profiles": ["readonly", "research", "editor"]
                    }
                },
                "access_sensitive_data": {"effect": "deny"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision"]},
                {"type": "approval_required", "capability": "write_code", "threshold": 20}
            ],
            "budget": {
                "token_budget": 1_000_000,
                "wallclock_seconds": 7200,
                "cost_usd": 5.0
            }
        }

        # Operator profile - system operations
        self._policy_templates["operator"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/operator",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "high"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {"effect": "allow"},
                "write_code": {
                    "effect": "allow",
                    "constraints": {
                        "paths": ["**/config/**", "**/scripts/**"],
                        "exclude_globs": ["**/production/**"]
                    }
                },
                "browse_web": {"effect": "allow"},
                "run_cli": {
                    "effect": "allow",
                    "constraints": {
                        "network": True,
                        "max_runtime_seconds": 300
                    }
                },
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 2,
                        "child_profiles": ["readonly", "research"]
                    }
                },
                "access_sensitive_data": {
                    "effect": "allow",
                    "constraints": {
                        "data_types": ["logs", "metrics", "non_production_config"]
                    }
                }
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision"]},
                {"type": "approval_required", "capability": "access_sensitive_data", "threshold": 1}
            ],
            "budget": {
                "token_budget": 500_000,
                "wallclock_seconds": 3600,
                "cost_usd": 3.0
            }
        }

        # Admin assisted profile - requires human approval
        self._policy_templates["admin_assisted"] = {
            "apiVersion": "babyai.policy/v1",
            "kind": "PolicySet",
            "metadata": {
                "id": "profile/admin_assisted",
                "layer": "profile",
                "parent": "base",
                "version": "2026.04.22-1",
                "risk_tier": "critical"
            },
            "defaults": {"effect": "deny"},
            "capabilities": {
                "read_code": {"effect": "allow"},
                "write_code": {"effect": "allow"},
                "browse_web": {"effect": "allow"},
                "run_cli": {"effect": "allow"},
                "spawn_agents": {
                    "effect": "allow",
                    "constraints": {
                        "max_depth": 5,
                        "child_profiles": ["readonly", "research", "editor", "builder", "operator"]
                    }
                },
                "access_sensitive_data": {"effect": "allow"}
            },
            "obligations": [
                {"type": "audit_log", "fields": ["session_id", "capability", "resource", "decision"]},
                {"type": "approval_required", "capability": "*", "threshold": 1},
                {"type": "human_oversight", "required": True}
            ],
            "budget": {
                "token_budget": 2_000_000,
                "wallclock_seconds": 14400,
                "cost_usd": 10.0
            }
        }

        logger.info("Loaded 7 policy templates (base + 6 profiles)")

    async def deploy_profile(self, profile_name: str) -> bool:
        """Deploy a standard profile for Stage 1"""
        try:
            if profile_name not in self._policy_templates:
                logger.error(f"Unknown profile: {profile_name}")
                return False

            profile_policy = self._policy_templates[profile_name]

            # Store the profile policy (in Stage 1, just keep in memory)
            self.deployed_profiles[profile_name] = {
                "policy": profile_policy,
                "deployed_at": datetime.utcnow().isoformat(),
                "version": profile_policy["metadata"]["version"],
                "active": True
            }

            logger.info(f"Deployed profile: {profile_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to deploy profile {profile_name}: {e}")
            return False

    async def generate_policy(self, intent_classification, context: Dict[str, Any]) -> PolicyConfig:
        """
        Generate dynamic policy based on intent and context

        Stage 1: Return base profile policy
        Stage 2+: Full dynamic generation with context overrides
        """
        try:
            # For Stage 1, return the static profile policy
            profile_name = intent_classification.profile
            base_policy = self._policy_templates.get(profile_name, self._policy_templates["readonly"])

            # Create PolicyConfig from template
            policy = PolicyConfig(
                api_version=base_policy["apiVersion"],
                kind=base_policy["kind"],
                metadata=base_policy["metadata"].copy(),
                defaults=base_policy["defaults"].copy(),
                capabilities={
                    cap: CapabilityConstraint(
                        effect=def_["effect"],
                        reason=def_.get("reason"),
                        constraints=def_.get("constraints"),
                        sealed=def_.get("sealed", False)
                    )
                    for cap, def_ in base_policy["capabilities"].items()
                },
                obligations=[
                    PolicyObligation(
                        type=obl["type"],
                        fields=obl.get("fields"),
                        capability=obl.get("capability"),
                        per_session=obl.get("per_session")
                    )
                    for obl in base_policy.get("obligations", [])
                ],
                budget=PolicyBudget(
                    token_budget=base_policy["budget"]["token_budget"],
                    wallclock_seconds=base_policy["budget"]["wallclock_seconds"],
                    cost_usd=base_policy["budget"]["cost_usd"],
                    max_agents=intent_classification.complexity.value if hasattr(intent_classification.complexity, 'value') else 5,
                    max_depth=base_policy.get("capabilities", {}).get("spawn_agents", {}).get("constraints", {}).get("max_depth", 1)
                )
            )

            # Add session-specific metadata
            policy.metadata["session_id"] = context.get("session_id")
            policy.metadata["generated_at"] = datetime.utcnow().isoformat()
            policy.metadata["intent_confidence"] = intent_classification.confidence

            return policy

        except Exception as e:
            logger.error(f"Policy generation failed: {e}")
            # Return safe readonly policy
            return await self.generate_policy(
                intent_classification._replace(profile="readonly"),
                context
            )

    def get_deployed_profiles(self) -> List[str]:
        """Get list of deployed profiles"""
        return list(self.deployed_profiles.keys())

    def get_profile_info(self, profile_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a deployed profile"""
        return self.deployed_profiles.get(profile_name)

    async def validate_policy_yaml(self, yaml_content: str) -> Dict[str, Any]:
        """Validate policy YAML against schema"""
        try:
            policy_data = yaml.safe_load(yaml_content)

            # Basic validation
            validation_result = {
                "valid": True,
                "errors": [],
                "warnings": []
            }

            # Check required fields
            required_fields = ["apiVersion", "kind", "metadata", "capabilities"]
            for field in required_fields:
                if field not in policy_data:
                    validation_result["valid"] = False
                    validation_result["errors"].append(f"Missing required field: {field}")

            # Validate capability effects
            if "capabilities" in policy_data:
                for cap, definition in policy_data["capabilities"].items():
                    if "effect" not in definition:
                        validation_result["valid"] = False
                        validation_result["errors"].append(f"Capability {cap} missing effect")
                    elif definition["effect"] not in ["allow", "deny"]:
                        validation_result["valid"] = False
                        validation_result["errors"].append(f"Capability {cap} has invalid effect: {definition['effect']}")

            return validation_result

        except yaml.YAMLError as e:
            return {
                "valid": False,
                "errors": [f"YAML parse error: {str(e)}"],
                "warnings": []
            }

    async def get_statistics(self) -> Dict[str, Any]:
        """Get policy generator statistics"""
        return {
            "deployed_profiles": len(self.deployed_profiles),
            "profile_names": list(self.deployed_profiles.keys()),
            "templates_loaded": len(self._policy_templates),
            "stage": "stage1_shadow"
        }

    async def shutdown(self) -> None:
        """Shutdown policy generator"""
        try:
            logger.info("Policy Generator shutdown complete")
        except Exception as e:
            logger.error(f"Error during policy generator shutdown: {e}")
            raise