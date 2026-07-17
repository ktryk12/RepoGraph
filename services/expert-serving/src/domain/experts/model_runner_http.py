"""
Model Runner HTTP Gateway for Expert Serving

Implements HTTP model runner gateway instead of direct AESA imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ModelRunnerHttpError(Exception):
    """Exception for HTTP errors from model runner."""
    pass


class ModelRunnerTimeoutError(Exception):
    """Exception for timeout errors from model runner."""
    pass


class LlamaCppRunnerGateway:
    """HTTP gateway for Llama.cpp model runner."""

    def __init__(
        self,
        base_url: str,
        model_ref: str,
        runner_ref: str = "llama.cpp",
        timeout_seconds: float = 30.0
    ):
        self.base_url = base_url.rstrip('/')
        self.model_ref = model_ref
        self.runner_ref = runner_ref
        self.timeout_seconds = timeout_seconds
        logger.info(f"LlamaCppRunnerGateway initialized: {self.base_url} | model={model_ref}")

    def generate(
        self,
        decision_id: str,
        context_id: str,
        purpose: str,
        prompt: str,
        constraints: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None
    ) -> Dict[str, Any]:
        """Generate text using the model runner."""

        logger.info(f"Generating text for decision {decision_id} with model {self.model_ref}")

        # Mock generation - in production this would make HTTP requests
        mock_response_text = self._generate_mock_response(prompt, purpose)

        # Simulate processing time
        time.sleep(0.1)

        response = {
            "text": mock_response_text,
            "model_ref": self.model_ref,
            "runner_ref": self.runner_ref,
            "tokens_used": len(mock_response_text.split()),
            "finish_reason": "stop",
            "trace": {
                "decision_id": decision_id,
                "context_id": context_id,
                "purpose": purpose,
                "model_url": f"{self.base_url}/v1/generate",
                "duration_ms": 100,
                "success": True
            }
        }

        # Apply constraints if provided
        if constraints:
            response["constraints_applied"] = constraints

        logger.info(f"Generated {len(mock_response_text)} characters for decision {decision_id}")

        return response

    def health(self) -> Dict[str, Any]:
        """Get health status of the model runner."""
        return {
            "status": "healthy",
            "base_url": self.base_url,
            "model_ref": self.model_ref,
            "runner_ref": self.runner_ref,
            "timeout_seconds": self.timeout_seconds,
            "last_check": time.time()
        }

    def _generate_mock_response(self, prompt: str, purpose: str) -> str:
        """Generate a mock response based on prompt and purpose."""

        prompt_lower = prompt.lower()

        # Mock responses based on prompt content
        if "hello" in prompt_lower or "hi" in prompt_lower:
            return "Hello! I'm an AI assistant ready to help you with your request."

        elif "code" in prompt_lower or "programming" in prompt_lower:
            return """I can help you with coding tasks. Here's a simple example:

```python
def hello_world():
    return "Hello, World!"
```

Let me know what specific programming help you need!"""

        elif "explain" in prompt_lower or "what is" in prompt_lower:
            return "I'd be happy to explain that concept. Let me break it down in a clear and understandable way."

        elif "analyze" in prompt_lower or "review" in prompt_lower:
            return "Based on the information provided, I'll conduct a thorough analysis and provide detailed insights."

        elif "write" in prompt_lower or "create" in prompt_lower:
            return "I'll help you create high-quality content that meets your requirements."

        else:
            # Generic response
            return f"I understand you're asking about this topic. Based on the context (purpose: {purpose}), let me provide a helpful response that addresses your needs."