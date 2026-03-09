"""v2.9.3 Model Resilience Patches — Wrapper for SandboxExecutor

Wraps executor.execute() to add model-resilience layers WITHOUT
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
  - FAILSAFE: if wrapper crashes, falls back to original execute()

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
    """Parse a NameError message string and return import statement if recoverable."""
    if not error_message:
        return None

    match = re.search(r"name '(\w+)' is not defined", error_message)
    if not match:
        return None

    name = match.group(1)
    return RECOVERABLE_IMPORTS.get(name)


def apply_patches(executor_instance) -> None:
    """Apply v2.9.3 resilience patches to a SandboxExecutor instance.

    FAILSAFE: The wrapper catches ALL exceptions internally.
    If any resilience logic fails, it falls back to calling the
    original execute() directly. The patches can NEVER break
    code execution — they only enhance it.
    """
    # Guard: don't double-patch
    if getattr(executor_instance, '_resilience_patched', False):
        logger.debug("Resilience patches already applied, skipping")
        return

    # Capture the original bound method
    original_execute = executor_instance.execute

    @functools.wraps(original_execute)
    async def patched_execute(*args, **kwargs):
        """Resilience-wrapped execute (v2.9.3 Changes #12-16).

        Accepts *args/**kwargs to be compatible with ANY calling
        convention. Extracts code and session_id positionally or
        by keyword, applies resilience layers, then delegates to
        the original execute().

        FAILSAFE: If anything in the wrapper fails, we call the
        original execute() with the unmodified arguments.
        """
        # ── Extract code and session_id from args/kwargs ─────────
        # execute() signature: (code, session_id='default', timeout=None)
        # Could be called positionally or with keywords.
        try:
            # Get code (first positional arg or keyword)
            if args:
                code = args[0]
                remaining_args = args[1:]
            elif 'code' in kwargs:
                code = kwargs.pop('code')
                remaining_args = ()
            else:
                # No code argument found — just pass through
                return await original_execute(*args, **kwargs)

            # Get session_id (second positional or keyword)
            if remaining_args:
                session_id = remaining_args[0]
                remaining_args = remaining_args[1:]
            elif 'session_id' in kwargs:
                session_id = kwargs.get('session_id', 'default')
            else:
                session_id = 'default'

        except Exception as e:
            # Arg extraction failed — fall back to original
            logger.warning(f"Resilience patch: arg extraction failed ({e}), falling back")
            return await original_execute(*args, **kwargs)

        # ── Apply resilience layers (all wrapped in try/except) ───
        try:
            # Change #13: Strip markdown code fences
            code = strip_code_fences(code)

            # Change #16: Detect non-code input
            non_code_msg = detect_non_code(code)
            if non_code_msg:
                code = f"raise ValueError({repr(non_code_msg)})"

            # Change #14 Layer B: Auto-prepend missing imports
            code = auto_prepend_imports(code)

        except Exception as e:
            logger.warning(f"Resilience patch: preprocessing failed ({e}), using original code")
            # Reset code to original if preprocessing broke it
            if args:
                code = args[0]
            # Continue with execution — don't abort

        # ── Change #12: Kernel prelude (first execution per session) ─
        if session_id not in _prelude_sessions:
            _prelude_sessions.add(session_id)  # Mark BEFORE executing
            logger.info(
                f"Kernel prelude: injecting auto-imports for "
                f"session '{session_id}'"
            )
            try:
                prelude_result = await original_execute(
                    KERNEL_PRELUDE, session_id, **kwargs
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

        # ── Execute user code ─────────────────────────────────────
        # Rebuild args with the (possibly modified) code
        if args:
            new_args = (code,) + remaining_args
        else:
            kwargs['code'] = code
            new_args = ()

        try:
            result = await original_execute(*new_args, **kwargs)
        except Exception as e:
            logger.error(f"Resilience patch: execute() raised {type(e).__name__}: {e}")
            raise  # Re-raise — don't swallow execution errors

        # ── Change #14 Layer A: NameError recovery ─────────────────
        try:
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
                    if args:
                        recovery_args = (recovery_code,) + remaining_args
                    else:
                        kwargs['code'] = recovery_code
                        recovery_args = ()

                    retry_result = await original_execute(
                        *recovery_args, **kwargs
                    )
                    if retry_result.success:
                        logger.info("Smart import recovery: retry SUCCEEDED")
                        result = retry_result
                    else:
                        logger.warning(
                            f"Smart import recovery: retry FAILED: "
                            f"{retry_result.error_message}"
                        )
        except Exception as e:
            logger.warning(f"Smart import recovery: exception ({e}), returning original result")
            # Return the original failed result, don't crash

        return result

    # Apply the patch to the singleton instance
    executor_instance.execute = patched_execute
    executor_instance._resilience_patched = True

    logger.info(
        "v2.9.3 resilience patches applied: "
        "Changes #12 (prelude), #13 (fences), #14 (recovery), #16 (prose)"
    )
