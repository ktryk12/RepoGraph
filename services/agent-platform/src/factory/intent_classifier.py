"""
Phase 7 Intent Classifier

Hybrid classifier: regex → fine-tuned classifier → LLM fallback
Stage 1: Focus on regex heuristics + LLM fallback for robust classification
"""

import logging
import re
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import hashlib

from .state_machine import IntentClassification, ComplexityLevel, ProfileType

logger = logging.getLogger(__name__)


class IntentClassifier:
    """
    Intent classifier implementing Phase 7 hybrid approach:

    1. Regex fast-path for common patterns
    2. Fine-tuned classifier (ModernBERT) - Stage 2+
    3. LLM structured output fallback for confidence < 0.5

    Stage 1: Regex heuristics + LLM fallback for shadow deployment
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

        # Classification statistics
        self.stats = {
            "total_classifications": 0,
            "by_method": {"regex": 0, "ml": 0, "llm": 0},
            "by_profile": {profile.value: 0 for profile in ProfileType},
            "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
            "daily_count": {}
        }

        # Regex patterns for fast-path classification
        self._build_regex_patterns()

        logger.info("Intent Classifier initialized for Stage 1")

    async def initialize(self) -> None:
        """Initialize classifier components"""
        try:
            # Stage 1: Only regex + LLM
            # Stage 2+: Initialize ModernBERT classifier here

            logger.info("Intent Classifier initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize intent classifier: {e}")
            raise

    def _build_regex_patterns(self) -> None:
        """Build regex patterns for fast-path classification"""

        # Readonly patterns (codebase analysis, review, etc.)
        self.readonly_patterns = [
            r'\b(analys[ise]r?|review|examine|inspect|audit|assess|evaluate)\b.*\b(codebase|code|repo|repository|project)\b',
            r'\b(what|how|why|explain|describe|show|tell)\b.*\b(code|function|class|method|api)\b',
            r'\b(tech debt|technical debt|security risks?|vulnerabilities|architecture)\b',
            r'\b(read|view|look at|check|find|search|grep)\b',
            r'\b(documentation|docs|readme|comments)\b',
        ]

        # Research patterns (browsing, learning, information gathering)
        self.research_patterns = [
            r'\b(research|investigate|explore|learn about|find information)\b',
            r'\b(browse|search|lookup|google|web search)\b',
            r'\b(what is|how does|tell me about)\b',
            r'\b(examples?|tutorials?|guides?|best practices)\b',
        ]

        # Editor patterns (small code changes, fixes)
        self.editor_patterns = [
            r'\b(fix|repair|correct|update|modify|change|edit)\b.*\b(bug|error|issue|function|line)\b',
            r'\b(add|insert|append|prepend)\b.*\b(line|comment|import|function)\b',
            r'\b(remove|delete|clean up)\b.*\b(dead code|unused|deprecated)\b',
            r'\b(refactor|rename|extract)\b.*\b(variable|function|method|class)\b',
        ]

        # Builder patterns (significant development, new features)
        self.builder_patterns = [
            r'\b(build|create|develop|implement|add|write)\b.*\b(feature|service|component|module|api)\b',
            r'\b(new|fresh|from scratch)\b',
            r'\b(integrate|connect|setup|configure)\b.*\b(service|database|api|system)\b',
            r'\b(deployment|deploy|infrastructure|docker|kubernetes)\b',
        ]

        # Operator patterns (system operations, monitoring)
        self.operator_patterns = [
            r'\b(deploy|start|stop|restart|scale|monitor)\b',
            r'\b(logs?|metrics|health|status|performance)\b',
            r'\b(troubleshoot|debug|diagnose)\b.*\b(production|system|service)\b',
            r'\b(backup|restore|migrate|upgrade)\b',
        ]

        # Admin assisted patterns (sensitive operations)
        self.admin_patterns = [
            r'\b(delete|remove|drop)\b.*\b(database|table|production|critical)\b',
            r'\b(admin|root|sudo|privileged)\b',
            r'\b(security|credentials|secrets|keys|tokens)\b',
            r'\b(production|prod)\b.*\b(change|modify|update|delete)\b',
        ]

        # Compile patterns for efficiency
        self.compiled_patterns = {
            ProfileType.READONLY: [re.compile(p, re.IGNORECASE) for p in self.readonly_patterns],
            ProfileType.RESEARCH: [re.compile(p, re.IGNORECASE) for p in self.research_patterns],
            ProfileType.EDITOR: [re.compile(p, re.IGNORECASE) for p in self.editor_patterns],
            ProfileType.BUILDER: [re.compile(p, re.IGNORECASE) for p in self.builder_patterns],
            ProfileType.OPERATOR: [re.compile(p, re.IGNORECASE) for p in self.operator_patterns],
            ProfileType.ADMIN_ASSISTED: [re.compile(p, re.IGNORECASE) for p in self.admin_patterns],
        }

    async def classify_intent(self, user_prompt: str, caller_context: Dict[str, Any]) -> IntentClassification:
        """
        Classify user intent using hybrid approach

        Returns IntentClassification with profile, complexity, confidence
        """
        try:
            # Track statistics
            self.stats["total_classifications"] += 1
            today = datetime.utcnow().date().isoformat()
            self.stats["daily_count"][today] = self.stats["daily_count"].get(today, 0) + 1

            # Step 1: Try regex fast-path
            regex_result = self._classify_with_regex(user_prompt)

            if regex_result and regex_result.confidence > 0.85:
                self.stats["by_method"]["regex"] += 1
                self.stats["by_profile"][regex_result.profile] += 1
                self._update_confidence_stats(regex_result.confidence)
                return regex_result

            # Step 2: ML classifier (Stage 2+)
            # ml_result = await self._classify_with_ml(user_prompt)

            # Step 3: LLM fallback for low confidence
            if not regex_result or regex_result.confidence < 0.5:
                llm_result = await self._classify_with_llm(user_prompt, caller_context)
                self.stats["by_method"]["llm"] += 1
                self.stats["by_profile"][llm_result.profile] += 1
                self._update_confidence_stats(llm_result.confidence)
                return llm_result

            # Use regex result as fallback
            self.stats["by_method"]["regex"] += 1
            self.stats["by_profile"][regex_result.profile] += 1
            self._update_confidence_stats(regex_result.confidence)
            return regex_result

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")

            # Safe fallback to readonly
            return IntentClassification(
                profile=ProfileType.READONLY.value,
                complexity=ComplexityLevel.STANDARD,
                confidence=0.0,
                requires_clarification=True,
                clarification_question="I couldn't understand your request. Could you clarify what you'd like me to help with?"
            )

    def _classify_with_regex(self, prompt: str) -> Optional[IntentClassification]:
        """Fast-path regex classification"""
        try:
            scores = {}

            # Check each profile's patterns
            for profile, patterns in self.compiled_patterns.items():
                score = 0
                matches = 0

                for pattern in patterns:
                    if pattern.search(prompt):
                        matches += 1
                        score += 1

                if matches > 0:
                    # Normalize by number of patterns to avoid bias
                    scores[profile] = score / len(patterns)

            if not scores:
                return None

            # Get highest scoring profile
            best_profile = max(scores, key=scores.get)
            best_score = scores[best_profile]

            # Determine complexity based on prompt length and keywords
            complexity = self._determine_complexity(prompt)

            # Calculate confidence based on match strength and uniqueness
            confidence = min(0.95, best_score * 0.8 + 0.1)  # Cap at 95% for regex

            # Adjust confidence if multiple profiles score similarly
            sorted_scores = sorted(scores.values(), reverse=True)
            if len(sorted_scores) > 1 and sorted_scores[0] - sorted_scores[1] < 0.2:
                confidence *= 0.7  # Reduce confidence for ambiguous cases

            return IntentClassification(
                profile=best_profile.value,
                complexity=complexity,
                confidence=confidence,
                requires_clarification=confidence < 0.5,
                clarification_question=None if confidence >= 0.5 else self._generate_clarification_question(prompt),
                detected_capabilities=self._extract_capabilities_from_prompt(prompt)
            )

        except Exception as e:
            logger.error(f"Regex classification failed: {e}")
            return None

    def _determine_complexity(self, prompt: str) -> ComplexityLevel:
        """Determine task complexity from prompt characteristics"""

        # Complexity indicators
        complexity_keywords = {
            ComplexityLevel.TRIVIAL: [
                r'\b(simple|quick|small|tiny|minor|single)\b',
                r'\b(one|1)\b.*\b(line|file|function)\b'
            ],
            ComplexityLevel.COMPLEX: [
                r'\b(complex|complicated|comprehensive|full|complete|entire)\b',
                r'\b(system|platform|architecture|infrastructure)\b',
                r'\b(multiple|many|several|all)\b.*\b(services|components|modules)\b',
                r'\b(from scratch|new project|full implementation)\b'
            ]
        }

        # Check for complexity indicators
        for complexity, keywords in complexity_keywords.items():
            for keyword in keywords:
                if re.search(keyword, prompt, re.IGNORECASE):
                    return complexity

        # Default to standard
        return ComplexityLevel.STANDARD

    def _extract_capabilities_from_prompt(self, prompt: str) -> List[str]:
        """Extract required capabilities from prompt"""
        capabilities = []

        capability_patterns = {
            "read_code": r'\b(read|view|analyze|examine|inspect)\b.*\b(code|file|repository)\b',
            "write_code": r'\b(write|create|build|implement|develop|code)\b',
            "run_cli": r'\b(run|execute|command|cli|script|terminal)\b',
            "browse_web": r'\b(browse|search|web|internet|google|lookup)\b',
            "spawn_agents": r'\b(delegate|spawn|create|multiple|parallel)\b.*\b(agents?|tasks?)\b',
            "access_sensitive_data": r'\b(database|credentials|secrets|production|sensitive)\b'
        }

        for capability, pattern in capability_patterns.items():
            if re.search(pattern, prompt, re.IGNORECASE):
                capabilities.append(capability)

        return capabilities

    def _generate_clarification_question(self, prompt: str) -> str:
        """Generate clarification question for low-confidence classifications"""

        # Simple clarification based on prompt ambiguity
        if len(prompt.split()) < 5:
            return "Could you provide more details about what you'd like me to help you with?"

        if "code" in prompt.lower():
            return "Are you looking to read and analyze code, or do you need me to write or modify code?"

        return "Could you clarify whether you need me to analyze existing code or create new functionality?"

    async def _classify_with_llm(self, prompt: str, context: Dict[str, Any]) -> IntentClassification:
        """LLM-based classification for ambiguous cases"""
        try:
            # This would integrate with the LLM service for structured output
            # For Stage 1, return a reasonable default based on context

            # Analyze context for hints
            has_repo = context.get("repo_root") is not None

            # Safe defaults based on context
            if has_repo and any(word in prompt.lower() for word in ["analyze", "review", "examine", "look"]):
                profile = ProfileType.READONLY.value
                confidence = 0.6
            else:
                profile = ProfileType.READONLY.value
                confidence = 0.3

            return IntentClassification(
                profile=profile,
                complexity=ComplexityLevel.STANDARD,
                confidence=confidence,
                requires_clarification=confidence < 0.5,
                clarification_question=self._generate_clarification_question(prompt) if confidence < 0.5 else None
            )

        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
            return IntentClassification(
                profile=ProfileType.READONLY.value,
                complexity=ComplexityLevel.STANDARD,
                confidence=0.0,
                requires_clarification=True,
                clarification_question="I had trouble understanding your request. Could you rephrase it?"
            )

    def _update_confidence_stats(self, confidence: float) -> None:
        """Update confidence distribution statistics"""
        if confidence >= 0.8:
            self.stats["confidence_distribution"]["high"] += 1
        elif confidence >= 0.5:
            self.stats["confidence_distribution"]["medium"] += 1
        else:
            self.stats["confidence_distribution"]["low"] += 1

    async def get_statistics(self) -> Dict[str, Any]:
        """Get classifier statistics"""
        return {
            "total_classifications": self.stats["total_classifications"],
            "classification_methods": self.stats["by_method"],
            "profile_distribution": self.stats["by_profile"],
            "confidence_distribution": self.stats["confidence_distribution"],
            "today": self.stats["daily_count"].get(datetime.utcnow().date().isoformat(), 0)
        }

    async def shutdown(self) -> None:
        """Shutdown classifier"""
        try:
            logger.info("Intent Classifier shutdown complete")
        except Exception as e:
            logger.error(f"Error during classifier shutdown: {e}")
            raise