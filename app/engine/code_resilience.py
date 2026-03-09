"""Code Resilience Helpers — Model-Agnostic Execution

Power Interpreter v2.9.3
Changes #12, #13, #14

Makes the sandbox resilient to common mistakes from smaller LLMs
(Haiku, GPT-4o-mini, Gemini Flash, etc.) that our less technical
team members often use.

Change #12: KERNEL_PRELUDE — pre-import common modules at session start
Change #13: strip_code_fences() — remove markdown code fences
Change #14: detect_missing_import() + auto_prepend_imports() — smart recovery

Author: Kaffer AI for Timothy Escamilla
"""

import re
import logging

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #12: Kernel Auto-Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Executed ONCE when a new session namespace is created.
# Eliminates NameError on standard library modules — the #1 failure
# mode for every model during smoke testing.
#
# Uses try/except per group so one missing package doesn't block others.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KERNEL_PRELUDE = """\
# === Power Interpreter Kernel Prelude (v2.9.3) ===
# Auto-imported for every session. Models don't need to import these.

# ── Standard library ────────────────────────────────────────────────
import sys
import os
import json
import math
import re
import datetime
from datetime import datetime as dt, timedelta, date, time as dt_time
import platform
import pathlib
from pathlib import Path
import collections
from collections import Counter, defaultdict, OrderedDict
import csv
import io
import time
import random
import statistics
import itertools
import functools
import operator
import string
import textwrap
import copy
import hashlib
import base64
import struct
import uuid
import pprint

# ── Data science (try/except — not all envs have these) ────────────
try:
    import pandas as pd
except ImportError:
    pass

try:
    import numpy as np
except ImportError:
    pass

try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend for server
    import matplotlib.pyplot as plt
except ImportError:
    pass

try:
    import seaborn as sns
except ImportError:
    pass

try:
    import openpyxl
except ImportError:
    pass

try:
    import xlrd
except ImportError:
    pass

try:
    import scipy
except ImportError:
    pass
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #13: Code Fence Stripping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Smaller models frequently wrap code in markdown fences:
#   ```python\nprint('hello')\n```
# The backticks cause SyntaxError. This strips them transparently.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def strip_code_fences(code: str) -> str:
    """Strip markdown code fences that models often wrap code in.

    Handles:
        ```python\n...\n```
        ```py\n...\n```
        ```\n...\n```
        Leading/trailing whitespace

    Returns:
        Cleaned code string, or original if no fences found.
    """
    if not code or not isinstance(code, str):
        return code or ""

    original = code
    code = code.strip()

    # ── Opening fence ───────────────────────────────────────────────
    # Match ```python, ```py, ```Python, or just ``` at the start
    opening_pattern = re.compile(r'^```(?:python|py|Python)?\s*\n', re.MULTILINE)
    match = opening_pattern.match(code)
    if match:
        code = code[match.end():]

    # ── Closing fence ──────────────────────────────────────────────
    # Match ``` at the end (with optional trailing whitespace)
    if code.rstrip().endswith('```'):
        code = code.rstrip()[:-3].rstrip()

    if code != original.strip():
        logger.info(
            f"Code fence stripping: removed fences "
            f"({len(original)} -> {len(code)} chars)"
        )

    return code


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #14: Smart Import Recovery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Two-layer defense:
#   Layer A: detect_missing_import() — post-failure NameError recovery
#   Layer B: auto_prepend_imports() — pre-execution static analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Known module names -> their import statements
RECOVERABLE_IMPORTS = {
    # ── Standard library ────────────────────────────────────────────
    "sys": "import sys",
    "os": "import os",
    "json": "import json",
    "math": "import math",
    "re": "import re",
    "datetime": "import datetime",
    "dt": "from datetime import datetime as dt",
    "timedelta": "from datetime import timedelta",
    "date": "from datetime import date",
    "platform": "import platform",
    "pathlib": "import pathlib",
    "Path": "from pathlib import Path",
    "csv": "import csv",
    "io": "import io",
    "time": "import time",
    "random": "import random",
    "statistics": "import statistics",
    "collections": "import collections",
    "Counter": "from collections import Counter",
    "defaultdict": "from collections import defaultdict",
    "OrderedDict": "from collections import OrderedDict",
    "itertools": "import itertools",
    "functools": "import functools",
    "operator": "import operator",
    "string": "import string",
    "textwrap": "import textwrap",
    "copy": "import copy",
    "deepcopy": "from copy import deepcopy",
    "hashlib": "import hashlib",
    "base64": "import base64",
    "struct": "import struct",
    "uuid": "import uuid",
    "pprint": "import pprint",
    "typing": "import typing",
    "dataclasses": "import dataclasses",
    "enum": "import enum",
    "abc": "import abc",
    "urllib": "import urllib",
    "http": "import http",
    "subprocess": "import subprocess",
    "shutil": "import shutil",
    "glob": "import glob",
    "fnmatch": "import fnmatch",
    "tempfile": "import tempfile",
    "warnings": "import warnings",
    "logging": "import logging",

    # ── Data science (common aliases) ──────────────────────────────
    "pd": "import pandas as pd",
    "np": "import numpy as np",
    "plt": "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt",
    "sns": "import seaborn as sns",
    "px": "import plotly.express as px",
    "go": "import plotly.graph_objects as go",

    # ── Data science (full names) ──────────────────────────────────
    "pandas": "import pandas",
    "numpy": "import numpy",
    "matplotlib": "import matplotlib",
    "seaborn": "import seaborn",
    "scipy": "import scipy",
    "sklearn": "import sklearn",
    "plotly": "import plotly",
    "openpyxl": "import openpyxl",
    "xlrd": "import xlrd",
    "requests": "import requests",
    "bs4": "import bs4",
}


def detect_missing_import(error: Exception) -> str | None:
    """If error is a NameError for a known module, return its import statement.

    Used as post-failure recovery: if exec() raises NameError for 'pd',
    this returns 'import pandas as pd' so the caller can inject it and retry.

    Args:
        error: The exception from code execution.

    Returns:
        Import statement string, or None if not recoverable.
    """
    if not isinstance(error, NameError):
        return None

    msg = str(error)
    # Extract name from "name 'xyz' is not defined"
    match = re.search(r"name '(\w+)' is not defined", msg)
    if not match:
        return None

    name = match.group(1)
    import_stmt = RECOVERABLE_IMPORTS.get(name)

    if import_stmt:
        logger.info(
            f"Smart import recovery: '{name}' not defined "
            f"-> will inject '{import_stmt}'"
        )
    else:
        logger.debug(f"Smart import recovery: '{name}' not in recovery map")

    return import_stmt


def auto_prepend_imports(code: str) -> str:
    """Pre-execution static analysis: detect module usage and prepend imports.

    Scans code for common patterns like 'pd.read_csv' or 'np.array' where
    the model forgot to include the import statement. Prepends the required
    imports automatically.

    This is Layer B (proactive). Layer A (detect_missing_import) is reactive.

    Args:
        code: The user's code string.

    Returns:
        Code with missing imports prepended, or original if none needed.
    """
    if not code:
        return code

    # Collect existing imports in the code
    existing_imports = set()
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            existing_imports.add(stripped)

    # Alias patterns: look for 'alias.' usage without corresponding import
    ALIAS_PATTERNS = {
        'pd.':  ('pandas', 'import pandas as pd'),
        'np.':  ('numpy', 'import numpy as np'),
        'plt.': ('matplotlib', "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt"),
        'sns.': ('seaborn', 'import seaborn as sns'),
        'px.':  ('plotly', 'import plotly.express as px'),
        'go.':  ('plotly', 'import plotly.graph_objects as go'),
    }

    prepends = []
    for pattern, (lib_name, import_stmt) in ALIAS_PATTERNS.items():
        if pattern in code:
            # Check if already imported
            already = any(lib_name in imp for imp in existing_imports)
            if not already and import_stmt not in prepends:
                prepends.append(import_stmt)

    if prepends:
        prepend_block = '\n'.join(prepends)
        logger.info(
            f"Auto-prepend imports: adding {len(prepends)} imports: "
            f"{prepends}"
        )
        return prepend_block + '\n\n' + code

    return code


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #16: Input Parameter Healing (bonus, lightweight)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_non_code(code: str) -> str | None:
    """Detect if input is natural language instead of Python code.

    Smaller models sometimes put a description in the code parameter
    instead of actual Python. This returns an actionable error message
    so the model can self-correct.

    Args:
        code: The supposed Python code string.

    Returns:
        Error message string if input looks like prose, None if it looks like code.
    """
    if not code or len(code.strip()) < 10:
        return None

    code_lower = code.lower().strip()

    # Strong code indicators
    code_indicators = [
        'import ', 'from ', 'print(', 'def ', 'class ',
        'for ', 'while ', 'if ', 'return ', 'with ',
        ' = ', '()', '[]', '{}', '.read', '.write',
        'async ', 'await ', 'try:', 'except',
        'lambda ', 'yield ', 'raise ',
    ]

    # Strong prose indicators
    prose_indicators = [
        'please ', 'can you ', 'i want ', 'help me ',
        'create a ', 'write a ', 'make a ', 'build a ',
        'show me ', 'give me ', 'i need ', 'could you ',
        'would you ', 'let me ', 'i\'d like ',
    ]

    has_code = any(ind in code_lower for ind in code_indicators)
    has_prose = any(ind in code_lower for ind in prose_indicators)

    # Only flag if it looks like prose AND doesn't look like code
    if has_prose and not has_code:
        return (
            "The 'code' parameter appears to contain a natural language description, "
            "not executable Python code. Please provide actual Python code.\n"
            "Example: print('Hello World')\n"
            "If you need to analyze data: df = pd.read_csv('file.csv'); print(df.describe())"
        )

    return None
