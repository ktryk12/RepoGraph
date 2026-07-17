"""
Policy Enforcer Service

Phase 7 policy enforcer implementing OPA-like policy evaluation.
Stage 1: Observe mode - logs decisions without enforcement.
"""

import logging
import asyncio
import json
import hashlib
from typing import Dict, Any, List, Optional, Literal, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Policy evaluation decision"""
    effect: Literal["allow", "deny"]
    reason: str
    determining_layer: str
    determining_rule_id: str
    trace: List[str]
    timestamp: str
    resource: Optional[str] = None
    capability: Optional[str] = None


@dataclass
class PolicyContext:
    """Context for policy evaluation"""
    session_id: str
    user_id: str
    repo_root: Optional[str] = None
    tenant: Optional[str] = None
    risk_hints: Optional[List[str]] = None


class PolicyEnforcerService:
    """
    Policy Enforcer implementing Phase 7 layered policy evaluation.

    Stage 1: Observe mode - evaluates all requests and logs decisions
    Stage 2: Progressive enforcement - enforce only for readonly profile + internal tenants
    Stage 3: General availability - all 6 profiles under enforcement, legacy audit-only
    """

    def __init__(self, store, event_bus, config: Optional[Dict] = None):
        self.store = store
        self.event_bus = event_bus
        self.config = config or {}

        # Stage 3: full enforcement configuration
        self.observe_mode = config.get("observe_mode", False)  # Stage 3: enforcement by default
        self.enforce_profiles = set(config.get("enforce_profiles", [
            "readonly", "research", "editor", "builder", "operator", "admin_assisted"
        ]))  # Stage 3: all 6 profiles
        self.enforce_tenants = set(config.get("enforce_tenants", ["internal", "external"]))  # Stage 3: all tenants
        self.legacy_mode = config.get("legacy_mode", "audit_only")  # Stage 3: audit-only, not parallel enforcement

        # Loaded policies by layer
        self.policies = {
            "base": {},
            "profile": {},
            "context": {},
            "session": {}
        }

        # Decision statistics
        self.stats = {
            "total_decisions": 0,
            "by_effect": {"allow": 0, "deny": 0},
            "by_layer": {},
            "by_capability": {},
            "would_deny_count": 0,  # Stage 1: track what would be denied
            "divergence_count": 0   # Stage 1: track divergence from legacy
        }

        logger.info(f"Policy Enforcer Service initialized in {'observe' if self.observe_mode else 'enforce'} mode")

    async def initialize(self) -> None:
        """Initialize policy enforcer"""
        try:
            # Load base policies
            await self._load_base_policies()

            # Initialize OPA components (Stage 2+)
            # await self._initialize_opa_engine()

            logger.info("Policy Enforcer Service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Policy Enforcer Service: {e}")
            raise

    async def _load_base_policies(self) -> None:
        """Load base policies from policy storage"""
        try:
            # Load the base policy and 6 standard profiles
            profiles = ["base", "readonly", "research", "editor", "builder", "operator", "admin_assisted"]

            for profile in profiles:
                policy_data = await self._get_policy_from_store(profile)
                if policy_data:
                    layer = policy_data.get("metadata", {}).get("layer", "profile")
                    self.policies[layer][profile] = policy_data

            logger.info(f"Loaded {len(profiles)} policies across layers")

        except Exception as e:
            logger.error(f"Failed to load base policies: {e}")
            raise

    async def _get_policy_from_store(self, policy_id: str) -> Optional[Dict[str, Any]]:
        """Get policy from store - Stage 3 supports all 6 profiles"""
        # This would read from policy-store in production
        # For Stage 3, return all 6 profile policies

        if policy_id == "base":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "base", "layer": "base", "version": "2026.04.22-1"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "deny", "reason": "default"},
                    "write_code": {"effect": "deny", "reason": "default"},
                    "run_cli": {"effect": "deny", "reason": "default"},
                    "browse_web": {"effect": "deny", "reason": "default"},
                    "spawn_agents": {"effect": "deny", "reason": "default"},
                    "access_sensitive_data": {"effect": "deny", "reason": "default"}
                }
            }
        elif policy_id == "readonly":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/readonly", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "low"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow", "constraints": {"paths": ["${context.repo_root}/**"]}},
                    "write_code": {"effect": "deny", "reason": "readonly profile"},
                    "run_cli": {"effect": "allow", "constraints": {"binaries": ["rg", "git", "grep", "find", "wc"]}},
                    "browse_web": {"effect": "deny", "reason": "readonly profile"},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 1}},
                    "access_sensitive_data": {"effect": "deny", "reason": "readonly profile"}
                },
                "budget": {"token_budget": 200000, "cost_usd": 0.5}
            }
        elif policy_id == "research":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/research", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "low"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow", "constraints": {"paths": ["${context.repo_root}/**"]}},
                    "write_code": {"effect": "deny", "reason": "research profile"},
                    "run_cli": {"effect": "allow", "constraints": {"binaries": ["curl", "wget", "git", "grep"]}},
                    "browse_web": {"effect": "allow", "constraints": {"allowed_domains": ["github.com", "stackoverflow.com"]}},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 2}},
                    "access_sensitive_data": {"effect": "deny", "reason": "research profile"}
                },
                "budget": {"token_budget": 300000, "cost_usd": 1.0}
            }
        elif policy_id == "editor":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/editor", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "medium"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow"},
                    "write_code": {"effect": "allow", "constraints": {"max_file_changes": 10, "backup_required": True}},
                    "run_cli": {"effect": "allow", "constraints": {"binaries": ["git", "npm", "pip", "pytest"]}},
                    "browse_web": {"effect": "allow", "constraints": {"allowed_domains": ["github.com", "stackoverflow.com"]}},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 1}},
                    "access_sensitive_data": {"effect": "deny", "reason": "editor profile"}
                },
                "budget": {"token_budget": 500000, "cost_usd": 2.0}
            }
        elif policy_id == "builder":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/builder", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "high"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow"},
                    "write_code": {"effect": "allow", "constraints": {"backup_required": True}},
                    "run_cli": {"effect": "allow", "constraints": {"max_runtime_seconds": 600}},
                    "browse_web": {"effect": "allow"},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 3}},
                    "access_sensitive_data": {"effect": "deny", "reason": "builder profile"}
                },
                "budget": {"token_budget": 1000000, "cost_usd": 5.0}
            }
        elif policy_id == "operator":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/operator", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "high"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow"},
                    "write_code": {"effect": "allow", "constraints": {"paths": ["**/config/**", "**/scripts/**"]}},
                    "run_cli": {"effect": "allow"},
                    "browse_web": {"effect": "allow"},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 2}},
                    "access_sensitive_data": {"effect": "allow", "constraints": {"data_types": ["logs", "metrics"]}}
                },
                "budget": {"token_budget": 500000, "cost_usd": 3.0}
            }
        elif policy_id == "admin_assisted":
            return {
                "apiVersion": "babyai.policy/v1",
                "kind": "PolicySet",
                "metadata": {"id": "profile/admin_assisted", "layer": "profile", "version": "2026.04.22-1", "risk_tier": "critical"},
                "defaults": {"effect": "deny"},
                "capabilities": {
                    "read_code": {"effect": "allow"},
                    "write_code": {"effect": "allow"},
                    "run_cli": {"effect": "allow"},
                    "browse_web": {"effect": "allow"},
                    "spawn_agents": {"effect": "allow", "constraints": {"max_depth": 5}},
                    "access_sensitive_data": {"effect": "allow"}
                },
                "obligations": [{"type": "approval_required", "threshold": 1}, {"type": "human_oversight"}],
                "budget": {"token_budget": 2000000, "cost_usd": 10.0}
            }

        return None

    # === PUBLIC API ===

    async def check_capability(self, session_policy_id: str, capability: str, resource: str,
                             context: PolicyContext, profile: Optional[str] = None) -> PolicyDecision:
        """
        Check if a capability is allowed for the given context.

        Stage 1: Always logs decision, enforcement depends on observe_mode flag.
        Stage 2: Selective enforcement - enforce only for readonly profile + internal tenants
        """
        try:
            # Evaluate policy using 4-layer composition
            decision = await self._evaluate_policy(session_policy_id, capability, resource, context)

            # Stage 2: Determine if enforcement should apply
            should_enforce = self._should_enforce_decision(profile, context.tenant)

            # Stage 2: Run legacy parallel check if configured
            legacy_decision = None
            if self.legacy_parallel:
                legacy_decision = await self._legacy_policy_check(capability, resource, context)

            # Stage 2: Compare decisions for divergence analysis
            if legacy_decision:
                await self._check_policy_divergence(decision, legacy_decision, context)

            # Update statistics
            await self._update_stats(decision, should_enforce)

            # Log decision for divergence analysis
            await self._log_decision(decision, context, should_enforce, legacy_decision)

            # Emit decision event with Stage 2 metadata
            await self.event_bus.emit("policy.decisions", {
                "session_id": context.session_id,
                "capability": capability,
                "resource": resource,
                "decision": decision.effect,
                "reason": decision.reason,
                "layer": decision.determining_layer,
                "rule_id": decision.determining_rule_id,
                "timestamp": decision.timestamp,
                "enforced": should_enforce,
                "profile": profile,
                "tenant": context.tenant,
                "stage": "stage2_progressive",
                "legacy_decision": legacy_decision.effect if legacy_decision else None
            })

            # Stage 2: Return enforced decision or log-only decision based on enforcement criteria
            if should_enforce and not self.observe_mode:
                return decision
            else:
                # Log what would happen but don't enforce
                decision_copy = PolicyDecision(
                    effect="allow" if legacy_decision and legacy_decision.effect == "allow" else decision.effect,
                    reason=f"Stage 2 - would {decision.effect} but not enforced: {decision.reason}",
                    determining_layer=decision.determining_layer,
                    determining_rule_id=decision.determining_rule_id,
                    trace=decision.trace + [f"Stage 2: enforcement={'yes' if should_enforce else 'no'}"],
                    timestamp=decision.timestamp,
                    resource=resource,
                    capability=capability
                )
                return decision_copy

        except Exception as e:
            logger.error(f"Policy check failed: {e}")

            # Safe default: deny with error trace
            return PolicyDecision(
                effect="deny",
                reason=f"Policy evaluation error: {str(e)}",
                determining_layer="error",
                determining_rule_id="error_fallback",
                trace=[f"Error during policy evaluation: {str(e)}"],
                timestamp=datetime.utcnow().isoformat(),
                resource=resource,
                capability=capability
            )

    async def _evaluate_policy(self, session_policy_id: str, capability: str, resource: str,
                             context: PolicyContext) -> PolicyDecision:
        """
        Evaluate policy using Phase 7 4-layer composition algorithm:

        1. Start from synthetic layer that denies all capabilities
        2. For each layer in order: explicit deny with reason is final
        3. Upper layers can only narrow existing allows (intersect constraints)
        4. sealed: true on a rule forbids lower layers from touching it
        5. Return (decision, determining_layer, determining_rule_id, trace)
        """

        trace = []
        trace.append("Starting 4-layer policy composition")

        # Step 1: Start with synthetic deny-all layer
        current_effect = "deny"
        current_reason = "default"
        determining_layer = "synthetic"
        determining_rule_id = "default_deny"

        trace.append(f"Layer 0 (synthetic): {capability} -> {current_effect} ({current_reason})")

        # Step 2: Process layers in order: base → profile → context → session
        layers = ["base", "profile", "context", "session"]

        for layer in layers:
            layer_result = await self._evaluate_layer(layer, session_policy_id, capability, resource, context)

            if layer_result:
                effect, reason, rule_id, constraints, sealed = layer_result

                trace.append(f"Layer {layer}: {capability} -> {effect} ({reason})")

                # Step 2a: Explicit deny is final
                if effect == "deny":
                    current_effect = "deny"
                    current_reason = reason
                    determining_layer = layer
                    determining_rule_id = rule_id
                    trace.append(f"Explicit deny from {layer}, stopping evaluation")
                    break

                # Step 2b: Allow can only narrow existing allows or override deny
                if effect == "allow":
                    if current_effect == "deny":
                        # Allow overrides deny (moving from deny -> allow)
                        current_effect = "allow"
                        current_reason = reason
                        determining_layer = layer
                        determining_rule_id = rule_id
                        trace.append(f"Allow from {layer} overrides previous deny")
                    else:
                        # Allow narrows existing allow (intersect constraints)
                        # For Stage 1, just update the determining layer
                        current_reason = reason
                        determining_layer = layer
                        determining_rule_id = rule_id
                        trace.append(f"Allow from {layer} narrows previous allow")

                # Check if rule is sealed (prevents further modification)
                if sealed:
                    trace.append(f"Rule sealed at {layer}, stopping evaluation")
                    break

        trace.append(f"Final decision: {current_effect} from {determining_layer}")

        return PolicyDecision(
            effect=current_effect,
            reason=current_reason,
            determining_layer=determining_layer,
            determining_rule_id=determining_rule_id,
            trace=trace,
            timestamp=datetime.utcnow().isoformat(),
            resource=resource,
            capability=capability
        )

    async def _evaluate_layer(self, layer: str, session_policy_id: str, capability: str,
                            resource: str, context: PolicyContext) -> Optional[Tuple[str, str, str, Dict, bool]]:
        """Evaluate a single policy layer"""

        # Get policies for this layer
        if layer == "session":
            # Session-specific policy from session_policy_id
            policy = await self._get_session_policy(session_policy_id)
        elif layer == "context":
            # Context policy based on repo/tenant
            policy = await self._get_context_policy(context)
        else:
            # Base/profile policies
            policy_id = "readonly" if layer == "profile" else "base"
            policy = self.policies.get(layer, {}).get(policy_id)

        if not policy:
            return None

        # Check capability in policy
        capabilities = policy.get("capabilities", {})
        capability_def = capabilities.get(capability)

        if not capability_def:
            # Capability not defined in this layer
            return None

        effect = capability_def.get("effect", "deny")
        reason = capability_def.get("reason", f"{layer} policy")
        rule_id = f"{layer}_{capability}"
        constraints = capability_def.get("constraints", {})
        sealed = capability_def.get("sealed", False)

        # For Stage 1, basic constraint evaluation
        if constraints and effect == "allow":
            # Evaluate constraints (simplified for Stage 1)
            if not await self._evaluate_constraints(constraints, resource, context):
                effect = "deny"
                reason = f"Constraint violation in {layer}"

        return (effect, reason, rule_id, constraints, sealed)

    async def _evaluate_constraints(self, constraints: Dict[str, Any], resource: str,
                                  context: PolicyContext) -> bool:
        """Evaluate capability constraints"""
        try:
            # Path constraints
            if "paths" in constraints:
                allowed_paths = constraints["paths"]
                # Simple path matching for Stage 1
                if "${context.repo_root}" in str(allowed_paths):
                    return True  # Allow repo access
                return False

            # Binary constraints for CLI
            if "binaries" in constraints and resource:
                allowed_binaries = constraints["binaries"]
                # Extract binary name from resource
                binary = resource.split()[0] if " " in resource else resource
                return binary in allowed_binaries

            # Default: allow if no specific constraints
            return True

        except Exception as e:
            logger.error(f"Constraint evaluation failed: {e}")
            return False

    async def _get_session_policy(self, session_policy_id: str) -> Optional[Dict[str, Any]]:
        """Get session-specific policy overrides"""
        # Stage 1: No session overrides
        return None

    async def _get_context_policy(self, context: PolicyContext) -> Optional[Dict[str, Any]]:
        """Get context-specific policy based on repo/tenant"""
        # Stage 1: No context overrides
        return None

    # === STAGE 2 METHODS ===

    def _should_enforce_decision(self, profile: Optional[str], tenant: Optional[str]) -> bool:
        """Stage 2: Determine if decision should be enforced based on profile and tenant"""

        # If no profile specified, don't enforce
        if not profile:
            return False

        # Stage 2: Only enforce for configured profiles and tenants
        profile_match = not self.enforce_profiles or profile in self.enforce_profiles
        tenant_match = not self.enforce_tenants or tenant in self.enforce_tenants

        return profile_match and tenant_match

    async def _legacy_policy_check(self, capability: str, resource: str, context: PolicyContext) -> PolicyDecision:
        """
        Stage 3: Legacy policy validator in audit-only mode

        No longer used for enforcement, only for deprecation warnings and audit logs.
        """
        try:
            # Simulate legacy policy validator behavior
            # In real implementation, this would call the existing policy-validator service

            # Simple legacy logic: allow read_code and run_cli, deny everything else
            if capability in ["read_code", "run_cli"]:
                effect = "allow"
                reason = "legacy policy allows"
            else:
                effect = "deny"
                reason = "legacy policy denies"

            return PolicyDecision(
                effect=effect,
                reason=reason,
                determining_layer="legacy_audit",
                determining_rule_id="legacy_validator_deprecated",
                trace=[f"Legacy audit check: {capability} -> {effect}", "DEPRECATED: Use new policy system"],
                timestamp=datetime.utcnow().isoformat(),
                resource=resource,
                capability=capability
            )

        except Exception as e:
            logger.error(f"Legacy audit check failed: {e}")
            return PolicyDecision(
                effect="deny",
                reason=f"Legacy audit check error: {str(e)}",
                determining_layer="legacy_error",
                determining_rule_id="legacy_error",
                trace=[f"Legacy audit error: {str(e)}", "DEPRECATED: Use new policy system"],
                timestamp=datetime.utcnow().isoformat(),
                resource=resource,
                capability=capability
            )

    async def _check_policy_divergence(self, new_decision: PolicyDecision, legacy_decision: PolicyDecision,
                                     context: PolicyContext) -> None:
        """Stage 3: Check for divergence and emit deprecation warnings"""

        if new_decision.effect != legacy_decision.effect:
            self.stats["divergence_count"] += 1

            # Stage 3: Deprecation warning for legacy/new policy disagreement
            logger.warning(f"DEPRECATION WARNING: Legacy policy divergence detected: "
                          f"new={new_decision.effect} vs legacy={legacy_decision.effect} "
                          f"for capability={new_decision.capability} session={context.session_id}. "
                          f"Legacy policy validator will be retired in Stage 4.")

            # Emit divergence event with deprecation notice
            await self.event_bus.emit("policy.divergence", {
                "session_id": context.session_id,
                "capability": new_decision.capability,
                "resource": new_decision.resource,
                "new_decision": new_decision.effect,
                "new_reason": new_decision.reason,
                "legacy_decision": legacy_decision.effect,
                "legacy_reason": legacy_decision.reason,
                "timestamp": datetime.utcnow().isoformat(),
                "stage": "stage3_general_availability",
                "deprecation_warning": "Legacy policy validator scheduled for retirement in Stage 4"
            })

    async def _update_stats(self, decision: PolicyDecision, enforced: bool = False) -> None:
        """Update decision statistics"""
        self.stats["total_decisions"] += 1
        self.stats["by_effect"][decision.effect] += 1

        layer_count = self.stats["by_layer"].get(decision.determining_layer, 0)
        self.stats["by_layer"][decision.determining_layer] = layer_count + 1

        if decision.capability:
            cap_count = self.stats["by_capability"].get(decision.capability, 0)
            self.stats["by_capability"][decision.capability] = cap_count + 1

        # Stage 1: Track would-deny count for divergence analysis
        if self.observe_mode and decision.effect == "deny":
            self.stats["would_deny_count"] += 1

        # Stage 2: Track enforcement statistics
        if not hasattr(self.stats, "enforced_decisions"):
            self.stats["enforced_decisions"] = 0
        if not hasattr(self.stats, "shadow_decisions"):
            self.stats["shadow_decisions"] = 0

        if enforced:
            self.stats["enforced_decisions"] += 1
        else:
            self.stats["shadow_decisions"] += 1

    async def _log_decision(self, decision: PolicyDecision, context: PolicyContext,
                          enforced: bool = False, legacy_decision: Optional[PolicyDecision] = None) -> None:
        """Log decision for divergence analysis"""
        try:
            log_entry = {
                "timestamp": decision.timestamp,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "capability": decision.capability,
                "resource": decision.resource,
                "effect": decision.effect,
                "reason": decision.reason,
                "determining_layer": decision.determining_layer,
                "determining_rule_id": decision.determining_rule_id,
                "observe_mode": self.observe_mode,
                "trace": decision.trace,
                # Stage 2 additions
                "enforced": enforced,
                "stage": "stage2_progressive",
                "legacy_effect": legacy_decision.effect if legacy_decision else None,
                "legacy_reason": legacy_decision.reason if legacy_decision else None,
                "tenant": context.tenant
            }

            # Store decision log (placeholder for Stage 1/2)
            # await self.store.log_policy_decision(log_entry)

        except Exception as e:
            logger.error(f"Failed to log decision: {e}")

    # === ADMIN API ===

    async def reload_policies(self) -> bool:
        """Reload policies from storage"""
        try:
            await self._load_base_policies()
            logger.info("Policies reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload policies: {e}")
            return False

    async def set_enforce_mode(self, enforce: bool) -> None:
        """Toggle between observe and enforce mode"""
        self.observe_mode = not enforce
        mode = "enforce" if enforce else "observe"
        logger.info(f"Policy enforcer switched to {mode} mode")

        await self.event_bus.emit("policy.mode_changed", {
            "mode": mode,
            "timestamp": datetime.utcnow().isoformat()
        })

    async def configure_selective_enforcement(self, profiles: List[str], tenants: List[str]) -> None:
        """Stage 2: Configure selective enforcement by profile and tenant"""
        self.enforce_profiles = set(profiles)
        self.enforce_tenants = set(tenants)

        logger.info(f"Selective enforcement configured: profiles={profiles}, tenants={tenants}")

        await self.event_bus.emit("policy.enforcement_configured", {
            "enforce_profiles": profiles,
            "enforce_tenants": tenants,
            "timestamp": datetime.utcnow().isoformat(),
            "stage": "stage2_progressive"
        })

    async def get_statistics(self) -> Dict[str, Any]:
        """Get enforcer statistics"""
        return {
            "mode": "observe" if self.observe_mode else "enforce",
            "total_decisions": self.stats["total_decisions"],
            "effect_distribution": self.stats["by_effect"],
            "layer_distribution": self.stats["by_layer"],
            "capability_distribution": self.stats["by_capability"],
            "would_deny_count": self.stats["would_deny_count"],
            "divergence_count": self.stats["divergence_count"],
            "policies_loaded": sum(len(policies) for policies in self.policies.values()),
            "stage": "stage3_general_availability",
            # Stage 3 metrics
            "enforced_decisions": self.stats.get("enforced_decisions", 0),
            "shadow_decisions": self.stats.get("shadow_decisions", 0),
            "enforce_profiles": list(self.enforce_profiles),
            "enforce_tenants": list(self.enforce_tenants),
            "legacy_mode": self.legacy_mode,
            "all_profiles_enforced": len(self.enforce_profiles) == 6
        }

    def is_healthy(self) -> bool:
        """Check enforcer health"""
        return len(self.policies["base"]) > 0 or len(self.policies["profile"]) > 0

    async def shutdown(self) -> None:
        """Shutdown policy enforcer"""
        try:
            logger.info("Policy Enforcer Service shutdown complete")
        except Exception as e:
            logger.error(f"Error during enforcer shutdown: {e}")
            raise