"""v2.9.3 Model Resilience Patches — Wrapper for SandboxExecutor

Wraps executor.execute() to add model-resilience layers WITHOUT
modifying the 80KB executor.py file directly.

Changes applied:
  #12 — Kernel auto-imports (KERNEL_PRELUDE, runs once per session)
  #13 — Code fence stripping (```python ... ``` -> clean code)
  #14 — Smart import recovery (Layer A: retry, Layer B: static analysis)
  #16 — Non-code input detection (prose -> actionable error)

v2.9.3a FIXES:
  - Accept **kwargs to absorb extra caller args (e.g. context)
  - Don't pass **kwargs to original execute (it doesn't accept them)
  - Simplified prelude: only 'import X' patterns, no 'from X import Y'
    to avoid _preprocess_code's destructive from-import rewriting
  - Safety net: entire wrapper in try/except → falls through to
    original execute if ANYTHING in the patch fails. Patch can
    NEVER cause a 500 Internal Server Error.

Author: MCA for Timothy Escamilla / Bolthouse Fresh Foods
"""

import functools
import logging
import re

# Use RELATIVE imports to avoid circular import when __init__.py is loading
from .code_resilience import (
    KERNEL_PRELUDE,
    strip_code_fences,
    auto_prepend_imports,
    detect_non_code,
    RECOVERABLE_IMPORTS,
)

logger = logging.getLogger(__name__)

# Track which sessions have had the prelude injected
_prelude_sessions: set = set()


def _detect_missing_import_from_message(error_message: str) -> str | None:
    """Parse a NameError message string and return import statement if recoverable.

    Args:
        error_message: The error message string (e.g. "name 'pd' is not defined")

    Returns:
        Import statement string, or None if not recoverable.
    """
    if not error_message:
        return None

    match = re.search(r"name '(\w+)' is not defined", error_message)
    if not match:
        return None

    name = match.group(1)
    import_stmt = RECOVERABLE_IMPORTS.get(name)

    if import_stmt:
        logger.info(
            f"Smart import recovery: '{name}' not defined "
            f"-> will inject '{import_stmt}'"
        )

    return import_stmt


def apply_patches(executor_instance) -> None:
    """Apply v2.9.3 resilience patches to a SandboxExecutor instance.

    Monkey-patches execute() with a wrapper that adds preprocessing
    and recovery. Safe to call multiple times (idempotent).

    Args:
        executor_instance: The SandboxExecutor singleton from executor.py.
    """
    # Guard: don't double-patch
    if getattr(executor_instance, '_resilience_patched', False):
        logger.debug("Resilience patches already applied, skipping")
        return

    # Capture the original bound method
    original_execute = executor_instance.execute

    @functools.wraps(original_execute)
    async def patched_execute(
        code: str,
        session_id: str = "default",
        timeout: int | None = None,
        **kwargs,  # Absorb extra args callers may pass (e.g. context)
    ):
        """Resilience-wrapped execute (v2.9.3 Changes #12-16).

        SAFETY GUARANTEE: This wrapper can NEVER cause a 500 error.
        If anything in the resilience logic fails, it falls through
        to the original execute() unchanged.
        """
        # Keep a copy of original code for safety-net fallback
        original_code = code

        try:
            # ── Change #13: Strip markdown code fences ──────────────
            code = strip_code_fences(code)

            # ── Change #16: Detect non-code input ───────────────────
            non_code_msg = detect_non_code(code)
            if non_code_msg:
                code = f"raise ValueError({repr(non_code_msg)})"

            # ── Change #14 Layer B: Auto-prepend missing imports ────
            code = auto_prepend_imports(code)

            # ── Change #12: Kernel prelude (once per session) ───────
            if session_id not in _prelude_sessions:
                # Mark BEFORE execution to prevent retry loops
                _prelude_sessions.add(session_id)
                try:
                    prelude_result = await original_execute(
                        KERNEL_PRELUDE, session_id
                    )
                    if prelude_result.success:
                        logger.info("Kernel prelude: auto-imports succeeded")
                    else:
                        logger.warning(
                            f"Kernel prelude: partial failure (non-fatal): "
                            f"{prelude_result.error_message}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Kernel prelude: exception (non-fatal): {e}"
                    )

            # ── Execute user code ───────────────────────────────────
            # Pass only (code, session_id, timeout) — the original
            # execute() does NOT accept **kwargs
            result = await original_execute(code, session_id, timeout)

            # ── Change #14 Layer A: NameError recovery ──────────────
            if (
                not result.success
                and result.error_message
                and "is not defined" in result.error_message
            ):
                import_stmt = _detect_missing_import_from_message(
                    result.error_message
                )
                if import_stmt:
                    logger.info(
                        f"Smart import recovery: retrying with "
                        f"'{import_stmt}'"
                    )
                    recovery_code = import_stmt + "\n" + code
                    result = await original_execute(
                        recovery_code, session_id, timeout
                    )
                    if result.success:
                        logger.info(
                            "Smart import recovery: retry SUCCEEDED"
                        )
                    else:
                        logger.warning(
                            f"Smart import recovery: retry FAILED: "
                            f"{result.error_message}"
                        )

            return result

        except Exception as e:
            # ── SAFETY NET ──────────────────────────────────────────
            # If ANYTHING in the resilience logic crashes, fall through
            # to the original execute. This guarantees the patch can
            # NEVER cause a 500 Internal Server Error.
            logger.error(
                f"v2.9.3 resilience patch error (falling through "
                f"to original execute): {e}"
            )
            try:
                return await original_execute(
                    original_code, session_id, timeout
                )
            except Exception:
                # If even the fallback fails, re-raise for caller
                raise

    # Apply the patch
    executor_instance.execute = patched_execute
    executor_instance._resilience_patched = True

    logger.info(
        "v2.9.3 resilience patches applied: "
        "Changes #12 (prelude), #13 (fences), #14 (recovery), #16 (prose)"
    )
