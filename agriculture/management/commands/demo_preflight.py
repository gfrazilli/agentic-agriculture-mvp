"""Django command for a safe, read-only demonstration preflight."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from agriculture.preflight import PreflightReport, run_demo_preflight


class Command(BaseCommand):
    help = (
        "Validate demo configuration and optionally probe the deployed web, ADK and MCP services."
    )
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--json", action="store_true", dest="json_output")
        parser.add_argument("--web-url", help="Public web service origin to probe.")
        parser.add_argument("--agent-url", help="Private Google ADK service origin to probe.")
        parser.add_argument("--mcp-url", help="Private MCP service origin or /mcp URL to probe.")
        parser.add_argument(
            "--agent-audience",
            help="Optional Cloud Run token audience for the private ADK service.",
        )
        parser.add_argument(
            "--mcp-audience",
            help="Optional Cloud Run token audience for the private MCP service.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=10.0,
            help="Per-request HTTP timeout in seconds (greater than 0, at most 60).",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        report = run_demo_preflight(
            web_url=options.get("web_url"),
            agent_url=options.get("agent_url"),
            mcp_url=options.get("mcp_url"),
            agent_audience=options.get("agent_audience"),
            mcp_audience=options.get("mcp_audience"),
            timeout=options["timeout"],
        )
        if options["json_output"]:
            self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            self._write_human_report(report)
        if not report.ok:
            raise CommandError("Demo preflight found one or more required failures.")

    def _write_human_report(self, report: PreflightReport) -> None:
        self.stdout.write(f"Agentic Agriculture demo preflight ({report.environment})")
        styles = {
            "pass": ("PASS", self.style.SUCCESS),
            "warning": ("WARN", self.style.WARNING),
            "fail": ("FAIL", self.style.ERROR),
        }
        for check in report.checks:
            label, style = styles[check.status]
            self.stdout.write(style(f"[{label}] {check.id}: {check.message}"))
        summary = report.summary
        self.stdout.write(
            "Summary: "
            f"{summary['passed']} passed, {summary['warnings']} warnings, "
            f"{summary['failures']} failures."
        )
