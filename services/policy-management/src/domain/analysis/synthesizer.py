from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json

from policy.case_service import get_case_service
from babyai_shared.truth.proposal_store import ProposalStore


DEFAULT_RULES_DIR = Path("policy/rules_autogen")


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    rule_type: str
    params: Dict[str, Any]
    rationale: str
    source_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "params": dict(self.params),
            "rationale": self.rationale,
            "source_reasons": list(self.source_reasons),
        }


def synthesize_from_files(
    *,
    telemetry_jsonl: str | Path,
    curriculum_report: str | Path,
    out_dir: str | Path = DEFAULT_RULES_DIR,
    proposal_store: Optional[ProposalStore] = None,
) -> Dict[str, Any]:
    telemetry_rows = _load_jsonl(Path(telemetry_jsonl))
    curriculum = _load_json(Path(curriculum_report))

    reasons = _top_failure_reasons(telemetry_rows, top_k=5)
    training_priorities = _extract_training_priorities(curriculum)

    rules = _build_rules(reasons, training_priorities)
    proposal = _build_proposal_payload(rules, curriculum)

    output = _write_proposal(proposal, out_dir)
    if proposal_store is not None:
        report_meta = curriculum.get("meta", {}) if isinstance(curriculum, dict) else {}
        case_context = report_meta if isinstance(report_meta, dict) else {}
        case_id = get_case_service().resolve_case_id(context=case_context)
        proposal_store.put_proposal(
            {
                "title": proposal["title"],
                "content": proposal["content"],
                "namespace": "policy",
                "type": "policy_rules",
                "value": proposal.get("rules", []),
            },
            namespace="policy",
            case_id=case_id,
            context_id=case_id,
        )
    return output


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _top_failure_reasons(rows: List[Dict[str, Any]], *, top_k: int) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        reasons = row.get("failure_reasons") or row.get("failure_reasons_before")
        if isinstance(reasons, list) and reasons:
            for r in reasons:
                key = str(r).strip()
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
            continue
        reason = row.get("failure_reason")
        if isinstance(reason, str) and reason.strip():
            key = reason.strip()
            counts[key] = counts.get(key, 0) + 1
            continue
        codes = row.get("error_codes")
        if isinstance(codes, list):
            for c in codes:
                key = str(c).strip()
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1

    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"reason": k, "count": v} for k, v in items[:top_k]]


def _extract_training_priorities(curriculum: Dict[str, Any]) -> List[Dict[str, Any]]:
    priorities = curriculum.get("training_priorities")
    if isinstance(priorities, list):
        return [p for p in priorities if isinstance(p, dict)]
    return []


def _build_rules(
    reasons: List[Dict[str, Any]],
    priorities: List[Dict[str, Any]],
) -> List[PolicyRule]:
    rules: List[PolicyRule] = []

    for item in reasons:
        reason = str(item.get("reason"))
        rule_type, params, rationale = _rule_from_reason(reason)
        if not rule_type:
            continue
        rules.append(
            PolicyRule(
                rule_id=_rule_id(rule_type, params),
                rule_type=rule_type,
                params=params,
                rationale=rationale,
                source_reasons=[reason],
            )
        )

    for priority in priorities[:5]:
        signal = str(priority.get("signal") or "").strip()
        bucket = str(priority.get("bucket") or "").strip()
        if not signal or not bucket:
            continue
        rule_type = "ops_signal_required"
        params = {"signal": signal, "bucket": bucket}
        rationale = f"Top missing ops signal '{signal}' for bucket {bucket}"
        rules.append(
            PolicyRule(
                rule_id=_rule_id(rule_type, params),
                rule_type=rule_type,
                params=params,
                rationale=rationale,
                source_reasons=[f"missing_ops:{signal}:{bucket}"],
            )
        )

    deduped: Dict[str, PolicyRule] = {}
    for rule in rules:
        deduped[rule.rule_id] = rule
    return list(deduped.values())


def _rule_from_reason(reason: str) -> tuple[str | None, Dict[str, Any], str]:
    r = reason.lower()
    if "ops_readiness" in r:
        return "ops_readiness_required", {"min_required": 3}, "Enforce ops readiness for distributed decisions"
    if "evidence" in r:
        return "evidence_required", {"paths": ["rationale[*].evidence_path"]}, "Require evidence paths for rationale"
    if "must_include" in r:
        return "must_include_guard", {}, "Ensure must-include requirements are satisfied"
    if "style_consistency" in r:
        return "style_consistency_guard", {}, "Ensure style/topology consistency"
    if "schema" in r:
        return "schema_strict", {}, "Enforce schema validity before evaluation"
    return None, {}, ""


def _rule_id(rule_type: str, params: Dict[str, Any]) -> str:
    payload = json.dumps({"rule_type": rule_type, "params": params}, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _build_proposal_payload(rules: List[PolicyRule], curriculum: Dict[str, Any]) -> Dict[str, Any]:
    report_meta = curriculum.get("meta", {}) if isinstance(curriculum, dict) else {}
    generated_at = report_meta.get("generated_at_utc") or "unknown"

    rules_payload = [rule.to_dict() for rule in sorted(rules, key=lambda r: r.rule_id)]
    proposal_id = sha256(
        json.dumps(rules_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "schema_version": 1,
        "proposal_id": f"policy-{proposal_id}",
        "created_at": generated_at,
        "rules": rules_payload,
        "title": "Autogenerated policy rules proposal",
        "content": json.dumps(rules_payload, indent=2, ensure_ascii=True),
    }


def _write_proposal(payload: Dict[str, Any], out_dir: str | Path) -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    proposal_id = payload.get("proposal_id", "policy-proposal")
    path = out / f"{proposal_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return {"proposal_path": str(path), "proposal_id": proposal_id}
