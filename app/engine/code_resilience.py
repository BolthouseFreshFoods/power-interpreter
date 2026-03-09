"""Code Resilience Helpers — Model-Agnostic Execution

Power Interpreter v2.9.3a
Changes #12, #13, #14, #16

Makes the sandbox resilient to common mistakes from smaller LLMs
(Haiku, GPT-4o-mini, Gemini Flash, etc.) that our less technical
team members often use.

IMPORTANT — KERNEL_PRELUDE rules:
  1. Only 'import X' and 'import X as Y' patterns
  2. NO 'from X import Y' — _preprocess_code rewrites these
     destructively (see v2.8.4 datetime bug)
  3. Only modules in executor.py's ALLOWED_IMPORTS whitelist
  4. sys, os, platform are NOT included (sandbox blocks them)
  5. Each import wrapped in its own try/except

Author: MCA for Timothy Escamilla / Bolthouse Fresh Foods
"""

import re
import logging

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #12: Kernel Auto-Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Executed ONCE per new session via resilience_patch.py.
#
# CRITICAL: Only uses 'import X' patterns. Never 'from X import Y'.
# The sandbox's _preprocess_code rewrites 'from' imports in ways that
# can corrupt the persistent namespace (see v2.8.4 datetime bug).
#
# Each import has its OWN try/except so one failure doesn't cascade.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KERNEL_PRELUDE = """\
# === Power Interpreter Kernel Prelude (v2.9.3a) ===
# Auto-imported for every new session.
# Only 'import X' patterns — no 'from X import Y' to avoid
# _preprocess_code rewriting issues.

# ── Math & Statistics ────────────────────────────
try:
    import math
except:
    pass
try:
    import statistics
except:
    pass
try:
    import random
except:
    pass
try:
    import decimal
except:
    pass

# ── Text & Data Formats ─────────────────────────
try:
    import string
except:
    pass
try:
    import textwrap
except:
    pass
try:
    import json
except:
    pass
try:
    import csv
except:
    pass
try:
    import re
except:
    pass

# ── Collections & Functional ────────────────────
try:
    import collections
except:
    pass
try:
    import itertools
except:
    pass
try:
    import functools
except:
    pass
try:
    import operator
except:
    pass

# ── Date & Time ─────────────────────────────────
try:
    import datetime
except:
    pass
try:
    import time
except:
    pass

# ── I/O & Utilities ─────────────────────────────
try:
    import io
except:
    pass
try:
    import pathlib
except:
    pass
try:
    import copy
except:
    pass
try:
    import pprint
except:
    pass
try:
    import struct
except:
    pass
try:
    import base64
except:
    pass
try:
    import hashlib
except:
    pass
try:
    import uuid
except:
    pass
try:
    import html
except:
    pass
try:
    import typing
except:
    pass

# ── Data Science ────────────────────────────────
try:
    import pandas as pd
except:
    pass
try:
    import numpy as np
except:
    pass
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except:
    pass
try:
    import seaborn as sns
except:
    pass
try:
    import openpyxl
except:
    pass
try:
    import xlrd
except:
    pass
try:
    import scipy
except:
    pass
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #13: Code Fence Stripping
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

    # ── Opening fence ───────────────────────────────────────────
    opening_pattern = re.compile(r'^```(?:python|py|Python)?\s*\n', re.MULTILINE)
    match = opening_pattern.match(code)
    if match:
        code = code[match.end():]

    # ── Closing fence ───────────────────────────────────────────
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

# Known names -> import statements (sandbox-allowed modules ONLY)
RECOVERABLE_IMPORTS = {
    # ── Standard library (in ALLOWED_IMPORTS) ────────────────────
    "math": "import math",
    "statistics": "import statistics",
    "random": "import random",
    "string": "import string",
    "textwrap": "import textwrap",
    "json": "import json",
    "csv": "import csv",
    "re": "import re",
    "collections": "import collections",
    "Counter": "import collections; Counter = collections.Counter",
    "defaultdict": "import collections; defaultdict = collections.defaultdict",
    "OrderedDict": "import collections; OrderedDict = collections.OrderedDict",
    "itertools": "import itertools",
    "functools": "import functools",
    "operator": "import operator",
    "decimal": "import decimal",
    "Decimal": "import decimal; Decimal = decimal.Decimal",
    "fractions": "import fractions",
    "datetime": "import datetime",
    "timedelta": "import datetime; timedelta = datetime.timedelta",
    "date": "import datetime; date = datetime.date",
    "time": "import time",
    "io": "import io",
    "pathlib": "import pathlib",
    "Path": "import pathlib; Path = pathlib.Path",
    "copy": "import copy",
    "deepcopy": "import copy; deepcopy = copy.deepcopy",
    "pprint": "import pprint",
    "typing": "import typing",
    "struct": "import struct",
    "base64": "import base64",
    "hashlib": "import hashlib",
    "uuid": "import uuid",
    "html": "import html",
    "enum": "import enum",
    "abc": "import abc",
    "dataclasses": "import dataclasses",

    # ── Data science (common aliases) ────────────────────────────
    "pd": "import pandas as pd",
    "np": "import numpy as np",
    "plt": "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt",
    "sns": "import seaborn as sns",
    "px": "import plotly.express as px",
    "go": "import plotly.graph_objects as go",

    # ── Data science (full names) ────────────────────────────────
    "pandas": "import pandas",
    "numpy": "import numpy",
    "matplotlib": "import matplotlib",
    "seaborn": "import seaborn",
    "scipy": "import scipy",
    "sklearn": "import sklearn",
    "openpyxl": "import openpyxl",
    "xlrd": "import xlrd",
    "plotly": "import plotly",
}


def detect_missing_import(error: Exception) -> str | None:
    """If error is a NameError for a known module, return its import statement."""
    if not isinstance(error, NameError):
        return None

    msg = str(error)
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

    Scans code for patterns like 'pd.read_csv' or 'np.array' where the
    model forgot to include the import. Prepends the required imports.
    """
    if not code:
        return code

    existing_imports = set()
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            existing_imports.add(stripped)

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
            already = any(lib_name in imp for imp in existing_imports)
            if not already and import_stmt not in prepends:
                prepends.append(import_stmt)

    if prepends:
        prepend_block = '\n'.join(prepends)
        logger.info(
            f"Auto-prepend imports: adding {len(prepends)} imports: {prepends}"
        )
        return prepend_block + '\n\n' + code

    return code


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Change #16: Input Parameter Healing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_non_code(code: str) -> str | None:
    """Detect if input is natural language instead of Python code."""
    if not code or len(code.strip()) < 10:
        return None

    code_lower = code.lower().strip()

    code_indicators = [
        'import ', 'from ', 'print(', 'def ', 'class ',
        'for ', 'while ', 'if ', 'return ', 'with ',
        ' = ', '()', '[]', '{}', '.read', '.write',
        'async ', 'await ', 'try:', 'except',
        'lambda ', 'yield ', 'raise ',
    ]

    prose_indicators = [
        'please ', 'can you ', 'i want ', 'help me ',
        'create a ', 'write a ', 'make a ', 'build a ',
        'show me ', 'give me ', 'i need ', 'could you ',
        'would you ', 'let me ', "i'd like ",
    ]

    has_code = any(ind in code_lower for ind in code_indicators)
    has_prose = any(ind in code_lower for ind in prose_indicators)

    if has_prose and not has_code:
        return (
            "The 'code' parameter appears to contain a natural language description, "
            "not executable Python code. Please provide actual Python code.\n"
            "Example: print('Hello World')\n"
            "If you need to analyze data: df = pd.read_csv('file.csv'); print(df.describe())"
        )

    return None
