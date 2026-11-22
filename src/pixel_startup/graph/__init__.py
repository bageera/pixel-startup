"""G30: organizational graph — typed nodes + explicit edges, deterministic health.

No model calls anywhere in this package: layout, health and explanations are
pure functions of stored rows plus the injected `today`.
"""

from pixel_startup.graph.health import STATES, project_health

__all__ = ["STATES", "project_health"]
