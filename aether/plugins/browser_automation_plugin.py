"""Playwright browser automation with read-only and confirmed action modes."""

from __future__ import annotations

import json
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec
from aether.llm_provider import LLMProviderError, ManagedLLM
from aether.system_interface import SystemInterface


@dataclass
class BrowserConfig:
    headless: bool = True
    timeout_ms: int = 30_000
    max_actions: int = 8


class BrowserAutomationPlugin(AetherPlugin):
    name = "browser_automation"
    description = "Summarize web pages and carry out confirmed multi-step browser tasks."
    resource_cost = "heavy"

    def __init__(
        self,
        sysiface: SystemInterface,
        llm: ManagedLLM,
        config: Optional[BrowserConfig] = None,
    ) -> None:
        self.sysiface = sysiface
        self.llm = llm
        self.config = config or BrowserConfig()

    def _browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser automation requires Playwright. Install it and run "
                "'playwright install chromium'."
            ) from exc
        return sync_playwright()

    @staticmethod
    def _valid_http_url(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )

    @classmethod
    def _public_url_error(cls, url: str) -> Optional[str]:
        if not cls._valid_http_url(url):
            return "URL must be a credential-free http(s) URL."
        hostname = urlparse(url).hostname or ""
        try:
            addresses = {
                item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            return f"URL hostname could not be resolved: {exc}"
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            return "Read-only page summarization cannot access localhost or private network addresses."
        return None

    def _summarize_page(self, args: dict) -> ToolResult:
        url = str(args["url"]).strip()
        if error := self._public_url_error(url):
            return ToolResult(ok=False, output=error)

        with self._browser() as playwright:
            browser = playwright.chromium.launch(headless=self.config.headless)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                if error := self._public_url_error(page.url):
                    return ToolResult(ok=False, output=f"Navigation was blocked: {error}")
                title = page.title()
                text = page.locator("body").inner_text(timeout=self.config.timeout_ms)
            finally:
                browser.close()

        compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())[:24_000]
        question = args.get("question", "Summarize the main claims and useful takeaways.")
        try:
            response = self.llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": f"Page title: {title}\nURL: {url}\n\n{compact}\n\nRequest: {question}",
                    }
                ],
                tools=[],
                system=(
                    "Summarize only the supplied page text. Be concise, distinguish facts from "
                    "page claims, and mention when navigation text or missing content limits certainty."
                ),
            )
            summary = response.text.strip()
        except LLMProviderError:
            summary = compact[:2_000]
            summary = f"{title}\n\nLLM unavailable; page text preview:\n{summary}"
        return ToolResult(ok=True, output=summary, data={"url": url, "title": title})

    def _plan_actions(self, task: str, url: str, page_text: str) -> list[dict[str, Any]]:
        response = self.llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Starting URL: {url}\nTask: {task}\n\nVisible page text:\n{page_text[:16_000]}"
                    ),
                }
            ],
            tools=[],
            system=(
                "Plan a short Playwright action list. Return JSON only as {\"actions\": [...]}. "
                "Allowed actions: goto {url}, click {selector}, fill {selector,text}, "
                "fill_username {selector}, fill_password {selector}, select {selector,value}, "
                "press {selector,key}, wait {milliseconds}, extract {selector}. Prefer accessible "
                "text/role selectors. Never invent credentials and never bypass a CAPTCHA."
            ),
        )
        parsed = json.loads(response.text)
        actions = parsed.get("actions", [])
        if not isinstance(actions, list):
            raise ValueError("Planner returned an invalid action list.")
        return actions[: self.config.max_actions]

    def _do_on_site(self, args: dict) -> ToolResult:
        url = str(args["url"]).strip()
        task = str(args["task"]).strip()
        if not self._valid_http_url(url):
            return ToolResult(ok=False, output="URL must be a credential-free http(s) URL.")
        credential = None
        if args.get("credential_service") and args.get("credential_username"):
            credential = self.sysiface.get_credential(
                str(args["credential_service"]), str(args["credential_username"])
            )

        with self._browser() as playwright:
            browser = playwright.chromium.launch(headless=self.config.headless)
            outputs: list[str] = []
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                visible = page.locator("body").inner_text(timeout=self.config.timeout_ms)
                actions = self._plan_actions(task, url, visible)
                for action in actions:
                    kind = action.get("action")
                    selector = action.get("selector")
                    if kind == "goto":
                        next_url = str(action["url"])
                        if not self._valid_http_url(next_url):
                            raise ValueError("Planner proposed a non-http(s) or credential-bearing URL.")
                        page.goto(next_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    elif kind == "click":
                        page.locator(selector).first.click(timeout=self.config.timeout_ms)
                    elif kind == "fill":
                        page.locator(selector).first.fill(str(action.get("text", "")))
                    elif kind == "fill_username":
                        if not credential:
                            raise RuntimeError("The requested stored login was not found.")
                        page.locator(selector).first.fill(credential.username)
                    elif kind == "fill_password":
                        if not credential:
                            raise RuntimeError("The requested stored login was not found.")
                        page.locator(selector).first.fill(credential.secret)
                    elif kind == "select":
                        page.locator(selector).first.select_option(str(action["value"]))
                    elif kind == "press":
                        page.locator(selector).first.press(str(action["key"]))
                    elif kind == "wait":
                        page.wait_for_timeout(min(int(action.get("milliseconds", 500)), 10_000))
                    elif kind == "extract":
                        outputs.append(page.locator(selector).first.inner_text())
                    else:
                        raise ValueError(f"Unsupported browser action: {kind}")
                final_url = page.url
                title = page.title()
            finally:
                browser.close()
        details = "\n".join(outputs).strip()
        return ToolResult(
            ok=True,
            output=f"Browser task completed at {title} ({final_url})." + (f"\n{details}" if details else ""),
            data={"url": final_url, "title": title, "steps": len(actions)},
        )

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="summarize_web_page",
                description="Open a public web page in read-only mode and summarize its visible content.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "question": {"type": "string"},
                    },
                    "required": ["url"],
                },
                handler=self._summarize_page,
                requires_confirmation=False,
                resource_cost="medium",
            ),
            ToolSpec(
                name="do_browser_task",
                description=(
                    "Carry out a multi-step task on a website. Stored logins are referenced by "
                    "Credential Manager service/username; passwords are never accepted as arguments."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "task": {"type": "string"},
                        "credential_service": {"type": "string"},
                        "credential_username": {"type": "string"},
                    },
                    "required": ["url", "task"],
                },
                handler=self._do_on_site,
                requires_confirmation=True,
                resource_cost="heavy",
            ),
        ]
