"""v2.9.3 Model Resilience Patches — Wrapper for SandboxExecutor

Wraps executor.execute_code() to add model-resilience layers WITHOUT
modifying the 80KB executor.py file directly.

Changes applied:
  #12 — Kernel auto-imports (KERNEL_PRELUDE, runs once per session)
  #13 — Code fence stripping (```python ... ``` -> clean code)
  #14 — Smart import recovery (Layer A: retry, Layer B: static analysis)
  #16 — Non-code input detection (prose -> actionable error)

Architecture:
  - apply_patches() monkey-patches the executor singleton
  - Called from app/engine/__init__.py at import time
  - executor.py stays untouched — zero risk to the 80KB execution engine
  - Idempotent: safe to call multiple times

Author: MCA for Timothy Escamilla / Bolthouse Fresh Foods
"""

import functools
import logging
import re

from app.engine.code_resilience import (
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

    Unlike detect_missing_import() which takes an Exception object, this works
    with the string stored in ExecutionResult.error_message after execution.

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

    Monkey-patches execute_code() to add preprocessing, prelude injection,
    and post-failure recovery. Safe to call multiple times (idempotent).

    The original execute_code is captured via closure and called for all
    actual execution. This wrapper only adds pre/post processing layers.

    Args:
        executor_instance: The SandboxExecutor singleton from executor.py.
    """
    # Guard: don't double-patch
    if getattr(executor_instance, '_resilience_patched', False):
        logger.debug("Resilience patches already applied, skipping")
        return

    # Capture the original bound method
    original_execute = executor_instance.execute_code

    @functools.wraps(original_execute)
    async def patched_execute_code(
        code: str,
        session_id: str = "default",
        *args,
        **kwargs
    ):
        """Resilience-wrapped execute_code (v2.9.3 Changes #12-16)."""

        # ── Change #13: Strip markdown code fences ──────────────────
        code = strip_code_fences(code)

        # ── Change #16: Detect non-code input ──────────────────────
        non_code_msg = detect_non_code(code)
        if non_code_msg:
            # Convert to ValueError so it flows through normal error handling
            # and the model gets actionable guidance to self-correct
            code = f"raise ValueError({repr(non_code_msg)})"

        # ── Change #14 Layer B: Auto-prepend missing imports ───────
        code = auto_prepend_imports(code)

        # ── Change #12: Kernel prelude (first execution per session) ─
        if session_id not in _prelude_sessions:
            logger.info(
                f"Kernel prelude: injecting auto-imports for "
                f"session '{session_id}'"
            )
            try:
                prelude_result = await original_execute(
                    KERNEL_PRELUDE, session_id, *args, **kwargs
                )
                if prelude_result.success:
                    logger.info("Kernel prelude: auto-imports succeeded")
                else:
                    logger.warning(
                        f"Kernel prelude: partial failure (non-fatal): "
                        f"{prelude_result.error_message}"
                    )
            except Exception as e:
                logger.warning(f"Kernel prelude: exception (non-fatal): {e}")
            finally:
                # Mark as done even on failure — don't retry every call
                _prelude_sessions.add(session_id)

        # ── Execute user code ─────────────────────────────────────
        result = await original_execute(code, session_id, *args, **kwargs)

        # ── Change #14 Layer A: NameError recovery ─────────────────
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
                    recovery_code, session_id, *args, **kwargs
                )
                if result.success:
                    logger.info("Smart import recovery: retry SUCCEEDED")
                else:
                    logger.warning(
                        f"Smart import recovery: retry FAILED: "
                        f"{result.error_message}"
                    )

        return result

    # Apply the patch to the singleton instance
    executor_instance.execute_code = patched_execute_code
    executor_instance._resilience_patched = True

    logger.info(
        "v2.9.3 resilience patches applied: "
        "Changes #12 (prelude), #13 (fences), #14 (recovery), #16 (prose)"
    )
