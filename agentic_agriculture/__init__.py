"""Google ADK integration for the Agentic Agriculture application.

Importing this package is deliberately side-effect free.  The ADK graph is
created only when :func:`agentic_agriculture.agent.build_agent_graph` is called
or when the explicit ``agentic_agriculture.runtime`` entry point is imported.
"""

from agentic_agriculture.config import AgenticConfig

__all__ = ["AgenticConfig"]
