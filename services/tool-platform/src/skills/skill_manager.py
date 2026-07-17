"""
Skill Manager Module

Consolidated from services/skill-runtime/main.py
Provides skill registration, execution, and feedback management.
"""

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class SkillManager:
    """Skill registry and execution service"""

    def __init__(self, store, event_bus=None, tool_runtime=None):
        self.store = store
        self.event_bus = event_bus
        self.tool_runtime = tool_runtime

        # Built-in skill types (expanded with consolidated skills)
        self.skill_types = {
            "code_generation": "Code generation and programming skills",
            "analysis": "Data and code analysis skills",
            "testing": "Testing and quality assurance skills",
            "documentation": "Documentation generation skills",
            "refactoring": "Code refactoring skills",
            "security": "Security analysis skills",
            "debugging": "Debugging and troubleshooting skills",
            "planning": "Planning and strategy skills",
            "learning": "Learning and adaptation skills",
            "support": "Support and guidance skills",
            "review": "Code and process review skills",
            "media": "Media processing and editing skills"
        }

    async def initialize(self) -> None:
        """Initialize skill manager"""
        await self._register_built_in_skills()
        logger.info("Skill manager initialized")

    async def _register_built_in_skills(self) -> None:
        """Register built-in skills (consolidated from skills/ directory)"""
        built_in_skills = [
            # Original built-in skills
            {
                "skill_id": "python_code_generation",
                "skill_name": "Python Code Generator",
                "skill_type": "code_generation",
                "dependencies": ["git_apply", "run_tests"],
                "manifest": {
                    "description": "Generate Python code based on requirements",
                    "input_schema": {"type": "object", "properties": {"requirements": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"code": {"type": "string"}}},
                    "context_requirements": ["repository", "dependencies"]
                }
            },
            {
                "skill_id": "security_analysis",
                "skill_name": "Security Analyzer",
                "skill_type": "security",
                "dependencies": ["security_scan", "lint"],
                "manifest": {
                    "description": "Analyze code for security vulnerabilities",
                    "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"vulnerabilities": {"type": "array"}}},
                    "context_requirements": ["repository"]
                }
            },

            # Skills consolidated from skills/ directory
            {
                "skill_id": "autoplan",
                "skill_name": "Automatic Planning",
                "skill_type": "planning",
                "dependencies": ["repo_reader", "search_local_index"],
                "manifest": {
                    "description": "Automatically generate implementation plans for features",
                    "input_schema": {"type": "object", "properties": {"feature_request": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"plan": {"type": "object"}}},
                    "context_requirements": ["repository", "dependencies"]
                }
            },
            {
                "skill_id": "cso",
                "skill_name": "Chief Security Officer",
                "skill_type": "security",
                "dependencies": ["security_scan", "evidence"],
                "manifest": {
                    "description": "Executive security assessment and governance",
                    "input_schema": {"type": "object", "properties": {"assessment_target": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"security_report": {"type": "object"}}},
                    "context_requirements": ["repository", "security_context"]
                }
            },
            {
                "skill_id": "document_release",
                "skill_name": "Document Release",
                "skill_type": "documentation",
                "dependencies": ["repo_reader", "git_apply"],
                "manifest": {
                    "description": "Generate and manage release documentation",
                    "input_schema": {"type": "object", "properties": {"release_version": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"documentation": {"type": "array"}}},
                    "context_requirements": ["repository", "git_history"]
                }
            },
            {
                "skill_id": "investigate",
                "skill_name": "Investigation",
                "skill_type": "analysis",
                "dependencies": ["search_local_index", "evidence", "analytics_collector"],
                "manifest": {
                    "description": "Investigate issues and gather evidence",
                    "input_schema": {"type": "object", "properties": {"investigation_target": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"findings": {"type": "object"}}},
                    "context_requirements": ["repository", "logs"]
                }
            },
            {
                "skill_id": "learn",
                "skill_name": "Learning & Adaptation",
                "skill_type": "learning",
                "dependencies": ["analytics_collector", "evidence"],
                "manifest": {
                    "description": "Learn from experiences and adapt behavior",
                    "input_schema": {"type": "object", "properties": {"learning_context": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"insights": {"type": "object"}}},
                    "context_requirements": ["historical_data", "feedback"]
                }
            },
            {
                "skill_id": "office_hours",
                "skill_name": "Office Hours",
                "skill_type": "support",
                "dependencies": ["repo_reader", "search_local_index"],
                "manifest": {
                    "description": "Provide interactive support and guidance",
                    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"response": {"type": "string"}}},
                    "context_requirements": ["repository", "documentation"]
                }
            },
            {
                "skill_id": "plan_ceo_review",
                "skill_name": "CEO Review Planning",
                "skill_type": "planning",
                "dependencies": ["analytics_collector", "repo_reader"],
                "manifest": {
                    "description": "Plan and prepare CEO review materials",
                    "input_schema": {"type": "object", "properties": {"review_period": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"review_plan": {"type": "object"}}},
                    "context_requirements": ["metrics", "progress"]
                }
            },
            {
                "skill_id": "plan_eng_review",
                "skill_name": "Engineering Review Planning",
                "skill_type": "planning",
                "dependencies": ["run_tests", "security_scan", "lint"],
                "manifest": {
                    "description": "Plan and prepare engineering review materials",
                    "input_schema": {"type": "object", "properties": {"review_scope": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"eng_review_plan": {"type": "object"}}},
                    "context_requirements": ["repository", "test_results"]
                }
            },
            {
                "skill_id": "retro",
                "skill_name": "Retrospective",
                "skill_type": "analysis",
                "dependencies": ["analytics_collector", "evidence"],
                "manifest": {
                    "description": "Conduct retrospective analysis and learning",
                    "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"retrospective": {"type": "object"}}},
                    "context_requirements": ["historical_data", "metrics"]
                }
            },
            {
                "skill_id": "review",
                "skill_name": "Code Review",
                "skill_type": "review",
                "dependencies": ["lint", "security_scan", "review_miner"],
                "manifest": {
                    "description": "Perform comprehensive code reviews",
                    "input_schema": {"type": "object", "properties": {"code_change": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"review_feedback": {"type": "object"}}},
                    "context_requirements": ["repository", "coding_standards"]
                }
            },
            {
                "skill_id": "video_edit",
                "skill_name": "Video Editing",
                "skill_type": "media",
                "dependencies": ["firecrawl_client"],
                "manifest": {
                    "description": "Edit and process video content",
                    "input_schema": {"type": "object", "properties": {"video_input": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"edited_video": {"type": "string"}}},
                    "context_requirements": ["media_assets"]
                }
            },
            {
                "skill_id": "video_import",
                "skill_name": "Video Import",
                "skill_type": "media",
                "dependencies": ["firecrawl_client"],
                "manifest": {
                    "description": "Import and process video files",
                    "input_schema": {"type": "object", "properties": {"source_path": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"imported_video": {"type": "object"}}},
                    "context_requirements": ["file_system"]
                }
            },
            {
                "skill_id": "video_scene_detect",
                "skill_name": "Video Scene Detection",
                "skill_type": "media",
                "dependencies": ["analytics_collector"],
                "manifest": {
                    "description": "Detect and analyze video scenes",
                    "input_schema": {"type": "object", "properties": {"video_file": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"scenes": {"type": "array"}}},
                    "context_requirements": ["video_content"]
                }
            },
            {
                "skill_id": "voice_overlay",
                "skill_name": "Voice Overlay",
                "skill_type": "media",
                "dependencies": ["firecrawl_client"],
                "manifest": {
                    "description": "Add voice overlays to media content",
                    "input_schema": {"type": "object", "properties": {"media_input": {"type": "string"}, "voice_script": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"enhanced_media": {"type": "string"}}},
                    "context_requirements": ["media_assets", "audio_processing"]
                }
            }
        ]

        for skill_data in built_in_skills:
            try:
                existing = await self.store.get_skill(skill_data["skill_id"])
                if not existing:
                    await self.store.create_skill(
                        skill_id=skill_data["skill_id"],
                        skill_name=skill_data["skill_name"],
                        skill_type=skill_data["skill_type"],
                        skill_manifest=skill_data["manifest"],
                        dependencies=skill_data["dependencies"],
                        metadata={"source": "built_in"}
                    )
                    logger.debug(f"Registered built-in skill: {skill_data['skill_id']}")

            except Exception as e:
                logger.warning(f"Failed to register built-in skill {skill_data['skill_id']}: {e}")

    async def register_skill(self, skill_id: str, skill_name: str,
                           skill_type: str, skill_manifest: Dict,
                           dependencies: List[str], version: str = "1.0",
                           metadata: Optional[Dict] = None) -> None:
        """Register a new skill"""
        try:
            # Validate dependencies
            validation_result = await self._validate_dependencies(dependencies)
            if not validation_result["valid"]:
                raise ValueError(f"Invalid dependencies: {validation_result['missing_tools']}")

            # Enhanced metadata
            enhanced_metadata = {
                "registered_by": "system",
                "registration_timestamp": datetime.utcnow().isoformat(),
                "dependency_validation": validation_result,
                **(metadata or {})
            }

            # Store skill
            await self.store.create_skill(
                skill_id=skill_id,
                skill_name=skill_name,
                skill_type=skill_type,
                skill_manifest=skill_manifest,
                dependencies=dependencies,
                version=version,
                metadata=enhanced_metadata
            )

            # Publish registration event
            if self.event_bus:
                self.event_bus.publish_skill_registered(skill_id, {
                    "skill_name": skill_name,
                    "skill_type": skill_type,
                    "dependencies": dependencies
                })

            logger.info(f"Skill registered: {skill_id} ({skill_type})")

        except Exception as e:
            logger.error(f"Failed to register skill {skill_id}: {e}")
            raise

    async def _validate_dependencies(self, dependencies: List[str]) -> Dict:
        """Validate skill dependencies"""
        validation_result = {
            "valid": True,
            "missing_tools": [],
            "total_dependencies": len(dependencies)
        }

        for tool_id in dependencies:
            if self.tool_runtime:
                tool = await self.tool_runtime.store.get_tool(tool_id)
                if not tool or not tool.get("enabled", True):
                    validation_result["missing_tools"].append(tool_id)
                    validation_result["valid"] = False

        return validation_result

    async def get_skill(self, skill_id: str) -> Optional[Dict]:
        """Get skill definition by ID"""
        try:
            return await self.store.get_skill(skill_id)
        except Exception as e:
            logger.error(f"Failed to get skill {skill_id}: {e}")
            return None

    async def list_skills(self, enabled_only: bool = True) -> List[Dict]:
        """List available skills"""
        try:
            return await self.store.list_skills(enabled_only)
        except Exception as e:
            logger.error(f"Failed to list skills: {e}")
            return []

    async def execute_skill(self, skill_id: str, input_data: Dict,
                          context_pack: Optional[Dict] = None) -> Dict:
        """Execute a skill"""
        try:
            execution_id = f"skill_exec_{uuid4().hex[:12]}"

            # Get skill definition
            skill = await self.get_skill(skill_id)
            if not skill:
                raise ValueError(f"Skill not found: {skill_id}")

            # Create execution record
            await self.store.create_skill_execution(
                execution_id=execution_id,
                skill_id=skill_id,
                context_pack=context_pack or {},
                input_data=input_data
            )

            # Execute skill
            start_time = datetime.utcnow()
            result = await self._execute_skill_internal(skill, input_data, context_pack)
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Update execution record
            await self.store.update_skill_execution(
                execution_id=execution_id,
                execution_state="completed",
                output_data=result,
                duration_ms=duration_ms
            )

            # Publish execution event
            if self.event_bus:
                self.event_bus.publish_skill_executed(skill_id, {
                    "execution_id": execution_id,
                    "duration_ms": duration_ms
                })

            return {
                "execution_id": execution_id,
                "status": "completed",
                "result": result,
                "duration_ms": duration_ms
            }

        except Exception as e:
            logger.error(f"Skill execution failed for {skill_id}: {e}")
            raise

    async def _execute_skill_internal(self, skill: Dict, input_data: Dict,
                                    context_pack: Optional[Dict]) -> Dict:
        """Internal skill execution logic"""
        # Simulate skill execution
        await asyncio.sleep(0.5)

        return {
            "skill_id": skill["skill_id"],
            "processed": True,
            "output": f"Skill {skill['skill_name']} executed successfully",
            "tools_used": skill["dependencies"],
            "timestamp": datetime.utcnow().isoformat()
        }

    async def submit_feedback(self, execution_id: str, feedback: Dict) -> None:
        """Submit feedback for skill execution"""
        try:
            await self.store.add_skill_feedback(execution_id, feedback)

            # Publish feedback event
            if self.event_bus:
                self.event_bus.publish_skill_feedback(execution_id, feedback)

            logger.info(f"Feedback submitted for execution: {execution_id}")

        except Exception as e:
            logger.error(f"Failed to submit feedback for {execution_id}: {e}")
            raise

    async def get_execution(self, execution_id: str) -> Optional[Dict]:
        """Get skill execution details"""
        # Would query from store
        return {"execution_id": execution_id, "status": "completed"}

    async def validate_dependencies(self, skill_id: str) -> Dict:
        """Validate skill dependencies"""
        skill = await self.get_skill(skill_id)
        if not skill:
            return {"valid": False, "error": "Skill not found"}

        dependencies = skill.get("dependencies", [])
        return await self._validate_dependencies(dependencies)

    async def get_performance_metrics(self, skill_id: str) -> Dict:
        """Get skill performance metrics"""
        if not self.store:
            return {}

        metrics = await self.store.get_performance_metrics("skill", skill_id)
        return {"metrics": metrics}

    def is_healthy(self) -> bool:
        return True

    async def shutdown(self) -> None:
        logger.info("Skill manager shutdown complete")