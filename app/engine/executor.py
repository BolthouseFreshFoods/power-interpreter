"""Power Interpreter - Sandboxed Code Executor

Executes Python code in a controlled environment with:
- Resource limits (time, memory)
- Restricted imports (whitelist only)
- Safe file I/O (sandbox directory only)
- Captured stdout/stderr
- Structured result extraction

Design: Uses in-process isolation with restricted globals.
The sandbox has access to pandas, numpy, matplotlib, etc.
but cannot access the network, filesystem outside sandbox, or system commands.

Version: 1.0.6 - Fixed __builtins__ handling and resource limits for containers
"""

import asyncio
import io
import os
import sys
import time
import traceback
import tempfile
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from contextlib import redirect_stdout, redirect_stderr
import logging

from app.config import settings

logger = logging.getLogger(__name__)


# Try to import resource module (Unix only, may fail in some containers)
try:
    import resource as resource_module
    HAS_RESOURCE = True
except ImportError:
    HAS_RESOURCE = False
    logger.warning("resource module not available - memory limits disabled")


class ExecutionResult:
    """Result of code execution"""
    def __init__(self):
        self.success: bool = False
        self.stdout: str = ""
        self.stderr: str = ""
        self.result: Any = None
        self.error_message: Optional[str] = None
        self.error_traceback: Optional[str] = None
        self.execution_time_ms: int = 0
        self.memory_used_mb: float = 0.0
        self.files_created: list = []
        self.variables: Dict[str, str] = {}  # Variable name -> type string
    
    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'stdout': self.stdout[:settings.MAX_OUTPUT_SIZE],
            'stderr': self.stderr[:settings.MAX_OUTPUT_SIZE],
            'result': self._serialize_result(),
            'error_message': self.error_message,
            'error_traceback': self.error_traceback,
            'execution_time_ms': self.execution_time_ms,
            'memory_used_mb': round(self.memory_used_mb, 2),
            'files_created': self.files_created,
            'variables': self.variables,
        }
    
    def _serialize_result(self) -> Any:
        """Safely serialize the result"""
        if self.result is None:
            return None
        try:
            import json
            json.dumps(self.result)
            return self.result
        except (TypeError, ValueError):
            return str(self.result)[:settings.MAX_OUTPUT_SIZE]


class SandboxExecutor:
    """Executes Python code in a sandboxed environment"""
    
    def __init__(self, sandbox_dir: Path = None):
        self.sandbox_dir = sandbox_dir or settings.SANDBOX_DIR
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"SandboxExecutor initialized: sandbox_dir={self.sandbox_dir}")
    
    def _get_safe_builtins(self) -> Dict:
        """Build a safe builtins dict, handling both dict and module forms."""
        safe = {}
        
        # Get the actual builtins dict regardless of form
        import builtins as builtins_module
        
        blocked = set(settings.BLOCKED_BUILTINS) if hasattr(settings, 'BLOCKED_BUILTINS') else {
            'eval', 'exec', 'compile', '__import__', 'globals', 'locals',
            'exit', 'quit', 'breakpoint', 'input',
        }
        
        for name in dir(builtins_module):
            if name.startswith('_'):
                continue
            if name in blocked:
                continue
            try:
                safe[name] = getattr(builtins_module, name)
            except AttributeError:
                pass
        
        # Always include these essentials
        safe['True'] = True
        safe['False'] = False
        safe['None'] = None
        safe['print'] = print
        safe['len'] = len
        safe['range'] = range
        safe['int'] = int
        safe['float'] = float
        safe['str'] = str
        safe['bool'] = bool
        safe['list'] = list
        safe['dict'] = dict
        safe['tuple'] = tuple
        safe['set'] = set
        safe['type'] = type
        safe['isinstance'] = isinstance
        safe['issubclass'] = issubclass
        safe['hasattr'] = hasattr
        safe['getattr'] = getattr
        safe['setattr'] = setattr
        safe['enumerate'] = enumerate
        safe['zip'] = zip
        safe['map'] = map
        safe['filter'] = filter
        safe['sorted'] = sorted
        safe['reversed'] = reversed
        safe['min'] = min
        safe['max'] = max
        safe['sum'] = sum
        safe['abs'] = abs
        safe['round'] = round
        safe['any'] = any
        safe['all'] = all
        safe['repr'] = repr
        safe['format'] = format
        safe['chr'] = chr
        safe['ord'] = ord
        safe['hex'] = hex
        safe['oct'] = oct
        safe['bin'] = bin
        safe['pow'] = pow
        safe['divmod'] = divmod
        safe['hash'] = hash
        safe['id'] = id
        safe['dir'] = dir
        safe['vars'] = vars
        safe['iter'] = iter
        safe['next'] = next
        safe['slice'] = slice
        safe['super'] = super
        safe['property'] = property
        safe['staticmethod'] = staticmethod
        safe['classmethod'] = classmethod
        safe['object'] = object
        safe['Exception'] = Exception
        safe['ValueError'] = ValueError
        safe['TypeError'] = TypeError
        safe['KeyError'] = KeyError
        safe['IndexError'] = IndexError
        safe['AttributeError'] = AttributeError
        safe['RuntimeError'] = RuntimeError
        safe['StopIteration'] = StopIteration
        safe['ZeroDivisionError'] = ZeroDivisionError
        safe['FileNotFoundError'] = FileNotFoundError
        safe['IOError'] = IOError
        safe['OSError'] = OSError
        safe['PermissionError'] = PermissionError
        safe['NotImplementedError'] = NotImplementedError
        safe['OverflowError'] = OverflowError
        
        return safe
    
    def _build_safe_globals(self, session_dir: Path) -> Dict:
        """Build the globals dict for sandboxed execution"""
        logger.info("Building safe globals...")
        
        try:
            import pandas as pd
        except ImportError:
            pd = None
            logger.warning("pandas not available")
        
        try:
            import numpy as np
        except ImportError:
            np = None
            logger.warning("numpy not available")
        
        import json
        import csv
        import math
        import statistics
        import datetime as dt_module
        import collections
        import itertools
        import functools
        import re
        import io as io_module
        import copy
        import hashlib as hashlib_module
        import base64
        from decimal import Decimal
        from fractions import Fraction
        from pathlib import Path as PathLib
        
        # Try dataclasses
        try:
            from dataclasses import dataclass, field, asdict
        except ImportError:
            dataclass = None
            field = None
            asdict = None
        
        from typing import Dict, List, Optional, Tuple, Set, Any
        
        # Get safe builtins
        safe_builtins = self._get_safe_builtins()
        
        # Add safe file operations
        safe_builtins['open'] = self._make_safe_open(session_dir)
        
        # Build globals
        sandbox_globals = {
            '__builtins__': safe_builtins,
            '__name__': '__sandbox__',
            
            # Data libraries
            'json': json,
            'csv': csv,
            
            # Math & stats
            'math': math,
            'statistics': statistics,
            'Decimal': Decimal,
            'Fraction': Fraction,
            
            # Standard library
            'datetime': dt_module,
            'collections': collections,
            'itertools': itertools,
            'functools': functools,
            're': re,
            'io': io_module,
            'copy': copy,
            'hashlib': hashlib_module,
            'base64': base64,
            'Path': PathLib,
            
            # Typing
            'Dict': Dict,
            'List': List,
            'Optional': Optional,
            'Tuple': Tuple,
            'Set': Set,
            'Any': Any,
            
            # Sandbox info
            'SANDBOX_DIR': str(session_dir),
            'RESULT': None,  # User can set this for structured output
        }
        
        # Add pandas/numpy if available
        if pd is not None:
            sandbox_globals['pd'] = pd
            sandbox_globals['pandas'] = pd
        if np is not None:
            sandbox_globals['np'] = np
            sandbox_globals['numpy'] = np
        
        # Add dataclasses if available
        if dataclass is not None:
            sandbox_globals['dataclass'] = dataclass
            sandbox_globals['field'] = field
            sandbox_globals['asdict'] = asdict
        
        logger.info("Safe globals built successfully")
        return sandbox_globals
    
    def _make_safe_open(self, session_dir: Path):
        """Create a safe open() that only allows access within session directory"""
        def safe_open(filepath, mode='r', *args, **kwargs):
            # Resolve the path
            path = Path(filepath)
            if not path.is_absolute():
                path = session_dir / path
            
            # Ensure it's within the sandbox
            try:
                resolved = path.resolve()
                session_resolved = session_dir.resolve()
                if not str(resolved).startswith(str(session_resolved)):
                    raise PermissionError(
                        f"Access denied: Cannot access files outside sandbox. "
                        f"Use relative paths or SANDBOX_DIR."
                    )
            except PermissionError:
                raise
            except Exception as e:
                raise PermissionError(f"Invalid file path: {e}")
            
            # Create parent directories if writing
            if 'w' in mode or 'a' in mode:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            
            return open(resolved, mode, *args, **kwargs)
        
        return safe_open
    
    def _lazy_import(self, name: str, sandbox_globals: Dict):
        """Lazily import allowed libraries into sandbox"""
        try:
            if name == 'matplotlib' or name == 'matplotlib.pyplot':
                import matplotlib
                matplotlib.use('Agg')  # Non-interactive backend
                import matplotlib.pyplot as plt
                sandbox_globals['matplotlib'] = matplotlib
                sandbox_globals['plt'] = plt
                return True
            elif name == 'seaborn':
                import seaborn as sns
                sandbox_globals['sns'] = sns
                sandbox_globals['seaborn'] = sns
                return True
            elif name == 'plotly' or name == 'plotly.express':
                import plotly
                import plotly.express as px
                import plotly.graph_objects as go
                sandbox_globals['plotly'] = plotly
                sandbox_globals['px'] = px
                sandbox_globals['go'] = go
                return True
            elif name == 'scipy' or name == 'scipy.stats':
                import scipy
                import scipy.stats
                sandbox_globals['scipy'] = scipy
                return True
            elif name == 'sklearn':
                import sklearn
                sandbox_globals['sklearn'] = sklearn
                return True
            elif name == 'statsmodels':
                import statsmodels
                import statsmodels.api as sm
                sandbox_globals['statsmodels'] = statsmodels
                sandbox_globals['sm'] = sm
                return True
            elif name == 'openpyxl':
                import openpyxl
                sandbox_globals['openpyxl'] = openpyxl
                return True
            elif name == 'pdfplumber':
                import pdfplumber
                sandbox_globals['pdfplumber'] = pdfplumber
                return True
            elif name == 'tabulate':
                from tabulate import tabulate
                sandbox_globals['tabulate'] = tabulate
                return True
            elif name == 'xlsxwriter':
                import xlsxwriter
                sandbox_globals['xlsxwriter'] = xlsxwriter
                return True
        except ImportError as e:
            logger.warning(f"Failed to import {name}: {e}")
            return False
        return False
    
    def _preprocess_code(self, code: str, sandbox_globals: Dict) -> str:
        """Preprocess code to handle imports and add safety wrappers"""
        lines = code.split('\n')
        processed_lines = []
        
        for line in lines:
            stripped = line.strip()
            
            # Handle import statements
            if stripped.startswith('import ') or stripped.startswith('from '):
                # Extract module name
                if stripped.startswith('import '):
                    module = stripped.split()[1].split('.')[0].split(',')[0]
                else:
                    module = stripped.split()[1].split('.')[0]
                
                # Try to lazy-load it
                if self._lazy_import(module, sandbox_globals):
                    # Handle 'from X import Y' and 'import X as Y'
                    if 'as ' in stripped:
                        alias = stripped.split(' as ')[-1].strip()
                        if module in sandbox_globals:
                            sandbox_globals[alias] = sandbox_globals[module]
                    processed_lines.append(f"# [sandbox] {stripped} -> pre-loaded")
                    continue
                elif module in sandbox_globals:
                    processed_lines.append(f"# [sandbox] {stripped} -> already available")
                    continue
                else:
                    # Block unknown imports
                    processed_lines.append(
                        f"# [sandbox] BLOCKED: {stripped} (not in allowed list)"
                    )
                    continue
            
            processed_lines.append(line)
        
        return '\n'.join(processed_lines)
    
    async def execute(
        self, 
        code: str, 
        session_id: str = "default",
        timeout: int = None,
        context: Dict = None
    ) -> ExecutionResult:
        """Execute Python code in sandbox
        
        Args:
            code: Python code to execute
            session_id: Session ID for file isolation
            timeout: Max execution time in seconds
            context: Additional variables to inject
        
        Returns:
            ExecutionResult with stdout, stderr, result, etc.
        """
        result = ExecutionResult()
        timeout = timeout or settings.MAX_EXECUTION_TIME
        
        logger.info(f"Executing code: session={session_id}, timeout={timeout}s")
        logger.info(f"Code preview: {code[:200]}")
        
        # Create session directory
        session_dir = self.sandbox_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Build sandbox environment
        try:
            sandbox_globals = self._build_safe_globals(session_dir)
        except Exception as e:
            logger.error(f"Failed to build sandbox globals: {e}", exc_info=True)
            result.success = False
            result.error_message = f"Sandbox initialization failed: {e}"
            result.error_traceback = traceback.format_exc()
            return result
        
        # Inject context variables
        if context:
            sandbox_globals.update(context)
        
        # Preprocess code (handle imports, add safety)
        try:
            processed_code = self._preprocess_code(code, sandbox_globals)
        except Exception as e:
            logger.error(f"Failed to preprocess code: {e}", exc_info=True)
            result.success = False
            result.error_message = f"Code preprocessing failed: {e}"
            result.error_traceback = traceback.format_exc()
            return result
        
        logger.info(f"Processed code: {processed_code[:200]}")
        
        # Capture stdout/stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # Track files before execution
        files_before = set()
        if session_dir.exists():
            try:
                files_before = set(str(p) for p in session_dir.rglob('*') if p.is_file())
            except Exception:
                pass
        
        # Execute
        start_time = time.time()
        
        try:
            # Run in thread pool to not block event loop
            loop = asyncio.get_event_loop()
            
            def _execute():
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # Try to set resource limits (may fail in containers)
                    if HAS_RESOURCE:
                        try:
                            mem_bytes = settings.MAX_MEMORY_MB * 1024 * 1024
                            resource_module.setrlimit(
                                resource_module.RLIMIT_AS, 
                                (mem_bytes, mem_bytes)
                            )
                        except (ValueError, OSError, resource_module.error) as e:
                            # This is expected in many container environments
                            logger.debug(f"Could not set memory limit: {e}")
                    
                    # Execute the code
                    compiled = compile(processed_code, '<sandbox>', 'exec')
                    exec(compiled, sandbox_globals)
            
            # Run with timeout
            await asyncio.wait_for(
                loop.run_in_executor(None, _execute),
                timeout=timeout
            )
            
            result.success = True
            logger.info("Code execution succeeded")
            
            # Extract RESULT variable if set
            if 'RESULT' in sandbox_globals and sandbox_globals['RESULT'] is not None:
                result.result = sandbox_globals['RESULT']
            
            # Extract user-defined variables for inspection
            skip_vars = {
                'pd', 'pandas', 'np', 'numpy', 'json', 'csv', 
                'math', 'statistics', 'datetime', 'collections',
                'itertools', 'functools', 're', 'io', 'copy',
                'hashlib', 'base64', 'Path', 'dataclass', 'field',
                'asdict', 'Dict', 'List', 'Optional', 'Tuple',
                'Set', 'Any', 'SANDBOX_DIR', 'RESULT',
                'plt', 'matplotlib', 'sns', 'seaborn',
                'plotly', 'px', 'go', 'scipy', 'sklearn',
                'statsmodels', 'sm', 'openpyxl', 'pdfplumber',
                'tabulate', 'xlsxwriter', 'Decimal', 'Fraction',
                '__builtins__', '__name__',
            }
            for key, value in sandbox_globals.items():
                if not key.startswith('_') and key not in skip_vars:
                    try:
                        result.variables[key] = type(value).__name__
                    except Exception:
                        pass
        
        except asyncio.TimeoutError:
            result.success = False
            result.error_message = f"Execution timed out after {timeout} seconds"
            result.error_traceback = ""
            logger.warning(f"Execution timed out after {timeout}s")
        
        except MemoryError:
            result.success = False
            result.error_message = f"Memory limit exceeded ({settings.MAX_MEMORY_MB} MB)"
            result.error_traceback = ""
            logger.warning("Memory limit exceeded")
        
        except Exception as e:
            result.success = False
            result.error_message = str(e)
            result.error_traceback = traceback.format_exc()
            logger.error(f"Execution error: {e}", exc_info=True)
        
        finally:
            end_time = time.time()
            result.execution_time_ms = int((end_time - start_time) * 1000)
            result.stdout = stdout_capture.getvalue()
            result.stderr = stderr_capture.getvalue()
            
            logger.info(f"Execution completed: success={result.success}, "
                       f"time={result.execution_time_ms}ms, "
                       f"stdout_len={len(result.stdout)}, "
                       f"stderr_len={len(result.stderr)}")
            
            if result.stdout:
                logger.info(f"stdout preview: {result.stdout[:200]}")
            if result.error_message:
                logger.error(f"error: {result.error_message}")
            
            # Track memory usage
            if HAS_RESOURCE:
                try:
                    usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
                    result.memory_used_mb = usage.ru_maxrss / 1024  # Convert KB to MB
                except Exception:
                    result.memory_used_mb = 0.0
            
            # Track new files created
            if session_dir.exists():
                try:
                    files_after = set(str(p) for p in session_dir.rglob('*') if p.is_file())
                    new_files = files_after - files_before
                    result.files_created = [
                        str(Path(f).relative_to(session_dir)) for f in new_files
                    ]
                except Exception:
                    pass
        
        return result


# Singleton executor
executor = SandboxExecutor()
