from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any, Callable

from babyai.tools.result import ToolResult, duration_ms, ensure_audit_sink, log_tool_call


@dataclass(frozen=True)
class BrowserAction:
    action: str
    selector: str | None = None
    value: str | None = None
    target: str | None = None


class BrowserTool:
    def __init__(
        self,
        url: str,
        actions: list[BrowserAction | dict[str, Any]] | None = None,
        auth_ref: str | None = None,
        screenshot: bool = False,
        runtime: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        clean_url = str(url or "").strip()
        if not clean_url:
            raise ValueError("url must be non-empty")
        self.url = clean_url
        self.actions = [_to_action(item) for item in list(actions or [])]
        self.auth_ref = str(auth_ref or "").strip() or None
        self.screenshot = bool(screenshot)
        self._runtime = runtime

    def permission_level(self) -> str:
        if any(_is_submission_action(action) for action in self.actions):
            return "high"
        if self.auth_ref or any(_is_login_action(action) for action in self.actions):
            return "medium"
        return "low"

    def execute(
        self,
        *,
        project_id: str,
        domain: str,
        memory_ref: Any,
        agent_id: str | None = None,
        secrets_ref: dict[str, str] | None = None,
    ) -> ToolResult:
        sink = ensure_audit_sink(memory_ref, project_id=project_id, domain=domain)
        started = datetime.now(timezone.utc)
        permission = self.permission_level()
        token = _resolve_auth_token(auth_ref=self.auth_ref, secrets_ref=secrets_ref, memory_ref=memory_ref)

        request_payload = {
            "url": self.url,
            "actions": [action.__dict__ for action in self.actions],
            "auth_ref": self.auth_ref,
            "screenshot": bool(self.screenshot),
        }

        try:
            if callable(self._runtime):
                output = self._runtime(
                    {
                        "url": self.url,
                        "actions": [action.__dict__ for action in self.actions],
                        "auth_token": token,
                        "screenshot": bool(self.screenshot),
                    }
                )
            else:
                output = _run_with_playwright(
                    url=self.url,
                    actions=self.actions,
                    auth_token=token,
                    screenshot=self.screenshot,
                )
            finished = datetime.now(timezone.utc)
            result = ToolResult(
                tool_name="browser_tool",
                tool_type="browser",
                permission_level=permission,
                ok=True,
                output=dict(output),
                error=None,
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            result = ToolResult(
                tool_name="browser_tool",
                tool_type="browser",
                permission_level=permission,
                ok=False,
                output={"url": self.url},
                error=f"browser_error:{exc}",
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )

        log_tool_call(
            sink=sink,
            project_id=project_id,
            domain=domain,
            tool_name="browser_tool",
            tool_type="browser",
            permission_level=permission,
            request=request_payload,
            result=result,
            agent_id=agent_id,
        )
        return result


def _run_with_playwright(
    *,
    url: str,
    actions: list[BrowserAction],
    auth_token: str | None,
    screenshot: bool,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("playwright is not available") from exc

    extracted: list[str] = []
    downloads: list[str] = []
    screenshot_path: str | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers={"Authorization": f"Bearer {auth_token}"} if auth_token else None
        )
        page = context.new_page()
        page.goto(url)

        for action in actions:
            if action.action == "navigate":
                target = str(action.value or action.target or url)
                page.goto(target)
            elif action.action == "click":
                if not action.selector:
                    raise ValueError("click action requires selector")
                page.click(action.selector)
            elif action.action == "fill":
                if not action.selector:
                    raise ValueError("fill action requires selector")
                page.fill(action.selector, str(action.value or ""))
            elif action.action == "extract":
                if not action.selector:
                    raise ValueError("extract action requires selector")
                extracted.append(page.inner_text(action.selector))
            elif action.action == "download":
                if not action.selector:
                    raise ValueError("download action requires selector")
                with page.expect_download() as info:
                    page.click(action.selector)
                download = info.value
                path = Path(tempfile.mkdtemp(prefix="babyai-browser-dl-")) / download.suggested_filename
                download.save_as(path.as_posix())
                downloads.append(path.as_posix())

        if screenshot:
            path = Path(tempfile.mkdtemp(prefix="babyai-browser-shot-")) / "screenshot.png"
            page.screenshot(path=path.as_posix(), full_page=True)
            screenshot_path = path.as_posix()

        current_url = str(page.url)
        context.close()
        browser.close()

    return {
        "url": url,
        "current_url": current_url,
        "extracted": extracted,
        "downloads": downloads,
        "screenshot_path": screenshot_path,
    }


def _to_action(value: BrowserAction | dict[str, Any]) -> BrowserAction:
    if isinstance(value, BrowserAction):
        return value
    if isinstance(value, dict):
        return BrowserAction(
            action=str(value.get("action") or "").strip(),
            selector=_as_optional(value.get("selector")),
            value=_as_optional(value.get("value")),
            target=_as_optional(value.get("target")),
        )
    raise ValueError("invalid BrowserAction payload")


def _is_login_action(action: BrowserAction) -> bool:
    clean = str(action.action or "").strip().lower()
    if clean in {"login", "fill_password"}:
        return True
    selector = str(action.selector or "").lower()
    return "password" in selector or "login" in selector


def _is_submission_action(action: BrowserAction) -> bool:
    clean = str(action.action or "").strip().lower()
    if clean in {"submit", "form_submit"}:
        return True
    if clean == "click":
        return "submit" in str(action.selector or "").lower()
    return False


def _resolve_auth_token(*, auth_ref: str | None, secrets_ref: dict[str, str] | None, memory_ref: Any) -> str | None:
    clean_ref = str(auth_ref or "").strip()
    if not clean_ref:
        return None
    if isinstance(secrets_ref, dict) and clean_ref in secrets_ref:
        return str(secrets_ref[clean_ref])
    for method_name in ("resolve_secret", "get_secret"):
        method = getattr(memory_ref, method_name, None)
        if callable(method):
            token = method(clean_ref)
            if token is not None and str(token).strip():
                return str(token).strip()
    raise ValueError(f"auth_ref could not be resolved: {clean_ref}")


def _as_optional(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None
