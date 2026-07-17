"""
skill_runtime/executor/prompt_assembler.py - Assemble the final babyAI prompt from
skill metadata and structured retrieval context.
"""
from __future__ import annotations

from typing import Any, Dict, List

from skill_runtime.context.context_builder import ContextPack
from skill_runtime.loader.skill_loader import SkillManifest


def assemble(
    manifest: SkillManifest,
    context: ContextPack,
    user_input: str,
    parameters: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    params = parameters or {}
    symbols_str = "\n".join(f"  - {symbol}" for symbol in context.repo_symbols[:8]) or "  (ingen)"
    policies_str = "\n".join(f"  - {policy}" for policy in context.policy_refs[:3]) or "  (ingen)"
    context_blocks_str = _render_context_blocks(context.prompt_pack)
    verification_plan_str = _render_verification_plan(context.verification_plan)
    artifact_refs_str = _render_artifacts(context.recent_artifacts)
    memory_refs_str = _render_memory_refs(context)
    task_family = str(context.extra.get("task_family", "") or "general").strip()
    prompt_owner = str(context.extra.get("prompt_assembly_owner", "") or "babyai").strip()
    source_mode = str(context.extra.get("source_mode", "") or "shared_retrieval").strip()
    preamble = str(context.prompt_pack.get("preamble", "") or "").strip()
    objective = str(context.prompt_pack.get("objective", "") or user_input).strip()

    system = f"""Du er babyAI's {manifest.name} skill (v{manifest.version}).

## Skill-beskrivelse
{manifest.description}

## Prompt-ejerskab
- final prompt assembly owner: {prompt_owner}
- retrieval source mode: {source_mode}
- task family: {task_family}

## Relevante kode-symboler (fra RepoGraph)
{symbols_str}

## Aktive policies
{policies_str}

## RepoGraph retrieval preamble
{preamble or "(ingen)"}

## RepoGraph objective
{objective}

## Retrieval context blocks
{context_blocks_str}

## Verification plan
{verification_plan_str}

## Task memory / refs
{memory_refs_str}

## Relevante artifacts
{artifact_refs_str}

## babyAI konventioner du SKAL overholde
- artifact-writer er eneste skrive-vej
- ECB-gating for ports/adapters
- Policies valideres af policy-validator
- Flager direkte database-skrivninger udenom artifact-writer
- Flager Kafka-producers uden schema-validering

## Skill-instruktion
{manifest.body}

## Output-format
Svar praecist og struktureret. Max {manifest.expert_routing.get('max_tokens', 1500)} tokens."""

    if params:
        param_str = "\n".join(f"  {key}: {value}" for key, value in params.items())
        user_msg = f"Parametre:\n{param_str}\n\nAnmodning:\n{user_input}"
    elif context.prompt and str(context.extra.get("prompt_assembly_owner", "") or "") != "babyai":
        user_msg = context.prompt
    else:
        user_msg = f"Anmodning:\n{user_input}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


def _render_context_blocks(prompt_pack: Dict[str, Any]) -> str:
    blocks = prompt_pack.get("context_blocks", []) if isinstance(prompt_pack, dict) else []
    rendered: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label", "") or "").strip()
        content = str(block.get("content", "") or "").strip()
        if not content:
            continue
        rendered.append(f"### {label}\n{content}" if label else content)
    return "\n\n".join(rendered) or "(ingen)"


def _render_verification_plan(plan: Dict[str, Any]) -> str:
    if not isinstance(plan, dict) or not plan:
        return "(ingen)"
    lines: list[str] = []
    for test in plan.get("tests", []) or []:
        text = str(test or "").strip()
        if text:
            lines.append(f"- test: {text}")
    for key in ("lint", "typecheck", "static_analysis"):
        if key in plan:
            lines.append(f"- {key}: {bool(plan.get(key))}")
    return "\n".join(lines) or "(ingen)"


def _render_artifacts(artifacts: List[Dict[str, Any]]) -> str:
    if not artifacts:
        return "(ingen)"
    rendered: list[str] = []
    for artifact in artifacts[:3]:
        if not isinstance(artifact, dict):
            continue
        path = str(artifact.get("path", "") or "").strip()
        if path:
            rendered.append(f"- {path}")
    return "\n".join(rendered) or "(ingen)"


def _render_memory_refs(context: ContextPack) -> str:
    refs = [str(ref).strip() for ref in context.extra.get("task_memory_refs", []) or [] if str(ref).strip()]
    snippets = [str(snippet).strip() for snippet in context.memory_snippets[:3] if str(snippet).strip()]
    rendered = [f"- ref: {ref}" for ref in refs]
    rendered.extend(f"- memory: {snippet}" for snippet in snippets)
    return "\n".join(rendered) or "(ingen)"
