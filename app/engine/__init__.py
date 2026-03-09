"""Power Interpreter - Execution Engine

v2.9.3: Model resilience patches applied at import time.
See resilience_patch.py for details on Changes #12-16.
"""

from .executor import executor  # noqa: F401

# ── v2.9.3: Apply model resilience patches ────────────────────────
# Wraps executor.execute_code() with preprocessing and recovery
# layers for smaller LLMs. Zero changes to executor.py itself.
from .resilience_patch import apply_patches as _apply_resilience
_apply_resilience(executor)
