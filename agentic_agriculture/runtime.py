"""Explicit Google ADK discovery entry point.

This module is intentionally separate from the package root: importing it is
the opt-in action that constructs the local agent graph. Construction performs
no Gemini request and opens no MCP connection.
"""

from agentic_agriculture.agent import get_runtime_graph

graph = get_runtime_graph()
root_agent = graph.root_agent
app = graph.app

__all__ = ["app", "root_agent"]
