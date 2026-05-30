# Copyright 2026 PipeShift Contributors
# SPDX-License-Identifier: Apache-2.0
"""Translate Informatica expression language to SQL (for dbt models)."""

import re
from enum import Enum
from typing import Optional


class Confidence(str, Enum):
    HIGH = "high"       # Deterministic, well-tested translation
    MEDIUM = "medium"   # Translated but may need review (analytic functions, lookups)
    LOW = "low"         # Partially translated, likely needs manual adjustment


def translate_expression(expr: str) -> str:
    """Translate an Informatica expression to equivalent SQL.

    Handles the deterministic (rule-based) translation for common functions.
    Returns SQL-compatible expression string.
    """
    if not expr or not expr.strip():
        return expr

    result = expr.strip()
    result = _translate_iif(result)
    result = _translate_decode(result)
    result = _translate_functions(result)
    return result


def score_confidence(expr: str) -> Confidence:
    """Score how confident we are in translating this expression.

    HIGH: Only uses well-tested functions (IIF, DECODE, string, date, math)
    MEDIUM: Uses analytic functions, lookups, or complex nesting
    LOW: Contains untranslatable elements (Java, custom functions, ERROR/ABORT)
    """
    if not expr or not expr.strip():
        return Confidence.HIGH

    upper = expr.upper()

    # LOW confidence indicators
    low_indicators = [
        "ERROR(", "ABORT(", "JAVA:", "SETVARIABLE(",
        "STORED_PROCEDURE", "SQL(",
    ]
    for indicator in low_indicators:
        if indicator in upper:
            return Confidence.LOW

    # MEDIUM confidence indicators
    medium_indicators = [
        ":LKP.", "MOVINGAVG(", "MOVINGSUM(", "CUME(",
        "RANK(", "PERCENTILE(",
    ]
    for indicator in medium_indicators:
        if indicator in upper:
            return Confidence.MEDIUM

    # Check for deeply nested IIF (>3 levels)
    iif_count = upper.count("IIF(")
    if iif_count > 3:
        return Confidence.MEDIUM

    return Confidence.HIGH


def _translate_iif(expr: str) -> str:
    """Recursively translate IIF(cond, true_val, false_val) → CASE WHEN ... END."""
    pattern = re.compile(r'\bIIF\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) != 3:
        return expr

    cond, true_val, false_val = args
    # Recursively translate nested IIFs in each part
    cond = _translate_iif(cond.strip())
    true_val = _translate_iif(true_val.strip())
    false_val = _translate_iif(false_val.strip())

    # Translate functions within each part
    cond = _translate_functions(cond)
    true_val = _translate_functions(true_val)
    false_val = _translate_functions(false_val)

    replacement = f"CASE WHEN {cond} THEN {true_val} ELSE {false_val} END"

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    result = expr[:start] + replacement + expr[close_pos + 1:]
    # Continue translating any remaining IIFs
    return _translate_iif(result)


def _translate_decode(expr: str) -> str:
    """Translate DECODE(val, match1, result1, ..., default) → CASE val WHEN ... END."""
    pattern = re.compile(r'\bDECODE\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) < 3:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    value = args[0].strip()
    pairs = args[1:]

    # Check if DECODE(TRUE, ...) pattern (acts like IF/ELSEIF)
    if value.upper() == 'TRUE':
        parts = ["CASE"]
        i = 0
        while i + 1 < len(pairs):
            cond = _translate_functions(pairs[i].strip())
            result = _translate_functions(pairs[i + 1].strip())
            parts.append(f"WHEN {cond} THEN {result}")
            i += 2
        if i < len(pairs):
            parts.append(f"ELSE {_translate_functions(pairs[i].strip())}")
        parts.append("END")
        replacement = " ".join(parts)
    else:
        parts = [f"CASE {_translate_functions(value)}"]
        i = 0
        while i + 1 < len(pairs):
            match_val = _translate_functions(pairs[i].strip())
            result = _translate_functions(pairs[i + 1].strip())
            parts.append(f"WHEN {match_val} THEN {result}")
            i += 2
        if i < len(pairs):
            parts.append(f"ELSE {_translate_functions(pairs[i].strip())}")
        parts.append("END")
        replacement = " ".join(parts)

    result = expr[:start] + replacement + expr[close_pos + 1:]
    return _translate_decode(result)


def _translate_functions(expr: str) -> str:
    """Apply function-level translations.

    Uses a combination of regex for simple renames and paren-aware parsing
    for functions that need argument restructuring.
    """
    result = expr

    # Handle functions that need paren-aware arg parsing first
    result = _translate_isnull(result)
    result = _translate_nvl(result)
    result = _translate_to_date(result)
    result = _translate_to_char(result)
    result = _translate_lookup_expr(result)

    # Handle $$PARAM variables → dbt var()
    result = _translate_variables(result)

    # Simple regex replacements for functions that are safe to rename
    for pattern, replacement in _SIMPLE_RULES:
        result = pattern.sub(replacement, result)

    return result


def _translate_to_date(expr: str) -> str:
    """Translate TO_DATE(string, format) with Informatica format → SQL format."""
    pattern = re.compile(r'\bTO_DATE\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) < 2:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    value = args[0].strip()
    fmt = args[1].strip()
    sql_fmt = _convert_date_format(fmt)
    replacement = f"TO_DATE({value}, {sql_fmt})"
    return expr[:start] + replacement + expr[close_pos + 1:]


def _translate_to_char(expr: str) -> str:
    """Translate TO_CHAR(date, format) with Informatica format → SQL format."""
    pattern = re.compile(r'\bTO_CHAR\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) < 2:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    value = args[0].strip()
    fmt = args[1].strip()
    sql_fmt = _convert_date_format(fmt)
    replacement = f"TO_CHAR({value}, {sql_fmt})"
    return expr[:start] + replacement + expr[close_pos + 1:]


# Informatica date format tokens → SQL (Redshift/Snowflake compatible) tokens
_DATE_FORMAT_MAP = {
    'YYYY': 'YYYY',
    'YY': 'YY',
    'MM': 'MM',
    'MON': 'MON',
    'MONTH': 'MONTH',
    'DD': 'DD',
    'DY': 'DY',
    'DAY': 'DAY',
    'HH24': 'HH24',
    'HH12': 'HH12',
    'HH': 'HH24',
    'MI': 'MI',
    'SS': 'SS',
    'MS': 'MS',
    'US': 'US',
    'AM': 'AM',
    'PM': 'PM',
    'D': 'D',
    'DDD': 'DDD',
    'J': 'J',
    'RR': 'YY',  # Informatica RR (2-digit year with pivot) → YY
    'RRRR': 'YYYY',  # Informatica RRRR → YYYY
    'W': 'W',
    'WW': 'WW',
    'Q': 'Q',
    'SSSSS': 'SSSSS',
}


def _convert_date_format(fmt: str) -> str:
    """Convert Informatica date format string to SQL-compatible format.

    Handles quoted format strings like 'YYYY-MM-DD' and returns them
    with any Informatica-specific tokens replaced.
    """
    # If not a quoted string, return as-is
    if not fmt.startswith("'") or not fmt.endswith("'"):
        return fmt

    inner = fmt[1:-1]

    # Replace tokens using regex word boundaries to avoid partial matches
    # Process longest tokens first to prevent HH matching inside HH24
    result = inner
    for infa_token, sql_token in sorted(_DATE_FORMAT_MAP.items(), key=lambda x: -len(x[0])):
        if infa_token != sql_token:  # Only replace if actually different
            # Use word-boundary-like matching: replace only if not part of a longer token
            result = re.sub(r'\b' + re.escape(infa_token) + r'\b', sql_token, result)

    return f"'{result}'"


def _translate_variables(expr: str) -> str:
    """Translate Informatica $$PARAM_NAME variables to dbt var('param_name').

    Informatica uses:
    - $$PARAM_NAME for mapping parameters
    - $PMFOLDER, $PMSESSIONNAME etc. for built-in variables
    """
    # $$PARAM_NAME → {{ var('param_name') }}
    result = re.sub(
        r'\$\$([A-Za-z_][A-Za-z0-9_]*)',
        lambda m: "{{ var('" + m.group(1).lower() + "') }}",
        expr,
    )
    return result


def _translate_lookup_expr(expr: str) -> str:
    """Translate :LKP.LOOKUP_NAME(port) → lookup reference comment.

    Informatica unconnected lookups use :LKP.LOOKUP_NAME(condition_port) syntax.
    In dbt, these become subqueries or macro calls.
    """
    pattern = re.compile(r':LKP\.([A-Za-z_][A-Za-z0-9_]*)\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    lookup_name = match.group(1).lower()
    args = _split_function_args(expr, match.end() - 1)
    if args is None:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    port = args[0].strip() if args else ""
    # Generate a subquery-style lookup (can be refined to use a macro)
    replacement = f"(SELECT result FROM {{{{ ref('{lookup_name}') }}}} WHERE key = {port} LIMIT 1)"
    return expr[:start] + replacement + expr[close_pos + 1:]


def _translate_isnull(expr: str) -> str:
    """Translate ISNULL(expr) → (expr IS NULL), handling nested parens."""
    pattern = re.compile(r'\bISNULL\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) != 1:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    inner = args[0].strip()
    replacement = f"({inner} IS NULL)"
    result = expr[:start] + replacement + expr[close_pos + 1:]
    return _translate_isnull(result)


def _translate_nvl(expr: str) -> str:
    """Translate NVL(expr, default) → COALESCE(expr, default), handling nested parens."""
    pattern = re.compile(r'\bNVL\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr

    start = match.start()
    args = _split_function_args(expr, match.end() - 1)
    if args is None or len(args) < 2:
        return expr

    close_pos = _find_closing_paren(expr, match.end() - 1)
    if close_pos is None:
        return expr

    # NVL2 has 3 args: NVL2(check, not_null_val, null_val)
    if len(args) == 3:
        check = args[0].strip()
        not_null_val = args[1].strip()
        null_val = args[2].strip()
        replacement = f"CASE WHEN {check} IS NOT NULL THEN {not_null_val} ELSE {null_val} END"
    else:
        arg_str = ", ".join(a.strip() for a in args)
        replacement = f"COALESCE({arg_str})"

    result = expr[:start] + replacement + expr[close_pos + 1:]
    return _translate_nvl(result)


# Simple regex rules: only for functions where a direct rename is semantically correct
_SIMPLE_RULES = [
    # String functions
    (re.compile(r'\bLTRIM\s*\(\s*RTRIM\s*\(\s*([^()]+?)\s*\)\s*\)', re.IGNORECASE), r'TRIM(\1)'),
    (re.compile(r'\bRTRIM\s*\(\s*LTRIM\s*\(\s*([^()]+?)\s*\)\s*\)', re.IGNORECASE), r'TRIM(\1)'),
    (re.compile(r'\bLTRIM\s*\(', re.IGNORECASE), 'LTRIM('),
    (re.compile(r'\bRTRIM\s*\(', re.IGNORECASE), 'RTRIM('),
    (re.compile(r'\bSUBSTR\s*\(', re.IGNORECASE), 'SUBSTRING('),
    # Date functions
    (re.compile(r'\bSYSDATE\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    (re.compile(r'\bSESSSTARTTIME\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    (re.compile(r"\bADD_TO_DATE\s*\(\s*([^,]+?)\s*,\s*'DD'\s*,\s*([^()]+?)\s*\)", re.IGNORECASE),
     r'DATEADD(day, \2, \1)'),
    (re.compile(r"\bADD_TO_DATE\s*\(\s*([^,]+?)\s*,\s*'MM'\s*,\s*([^()]+?)\s*\)", re.IGNORECASE),
     r'DATEADD(month, \2, \1)'),
    (re.compile(r"\bADD_TO_DATE\s*\(\s*([^,]+?)\s*,\s*'YY'\s*,\s*([^()]+?)\s*\)", re.IGNORECASE),
     r'DATEADD(year, \2, \1)'),
    (re.compile(r"\bADD_TO_DATE\s*\(\s*([^,]+?)\s*,\s*'HH'\s*,\s*([^()]+?)\s*\)", re.IGNORECASE),
     r'DATEADD(hour, \2, \1)'),
    # Type conversion
    (re.compile(r'\bTO_INTEGER\s*\(\s*([^()]+?)\s*\)', re.IGNORECASE), r'CAST(\1 AS INTEGER)'),
    (re.compile(r'\bTO_BIGINT\s*\(\s*([^()]+?)\s*\)', re.IGNORECASE), r'CAST(\1 AS BIGINT)'),
    (re.compile(r'\bTO_FLOAT\s*\(\s*([^()]+?)\s*\)', re.IGNORECASE), r'CAST(\1 AS FLOAT)'),
    (re.compile(r'\bTO_DECIMAL\s*\(\s*([^()]+?)\s*\)', re.IGNORECASE), r'CAST(\1 AS DECIMAL)'),
    # Regex
    (re.compile(r'\bREG_REPLACE\s*\(', re.IGNORECASE), 'REGEXP_REPLACE('),
    (re.compile(r'\bREG_MATCH\s*\(', re.IGNORECASE), 'REGEXP_LIKE('),
    # INSTR(string, search) → STRPOS(string, search) [not POSITION which has different syntax]
    (re.compile(r'\bINSTR\s*\(', re.IGNORECASE), 'STRPOS('),
    # Tier 2: Analytic/aggregate functions
    (re.compile(r'\bCUME\s*\(', re.IGNORECASE), 'SUM('),  # CUME is running sum → SUM() OVER()
    (re.compile(r'\bMOVINGAVG\s*\(', re.IGNORECASE), 'AVG('),  # needs OVER() clause added by generator
    (re.compile(r'\bMOVINGSUM\s*\(', re.IGNORECASE), 'SUM('),  # needs OVER() clause added by generator
]


def _split_function_args(expr: str, open_paren_pos: int) -> Optional[list]:
    """Split function arguments respecting nested parentheses and quotes.

    Informatica uses '' (doubled single-quote) for escaping, not backslash.
    """
    depth = 0
    args = []
    current = []
    in_quote = False
    i = open_paren_pos + 1  # skip the opening paren

    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            if in_quote:
                # Check for escaped quote '' (doubled)
                if i + 1 < len(expr) and expr[i + 1] == "'":
                    current.append("'")
                    current.append("'")
                    i += 2
                    continue
                else:
                    in_quote = False
                    current.append(ch)
            else:
                in_quote = True
                current.append(ch)
        elif in_quote:
            current.append(ch)
        elif ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            if depth == 0:
                args.append(''.join(current))
                return args
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    return None


def _find_closing_paren(expr: str, open_paren_pos: int) -> Optional[int]:
    """Find the position of the closing parenthesis matching the one at open_paren_pos."""
    depth = 0
    in_quote = False
    i = open_paren_pos

    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            if in_quote:
                if i + 1 < len(expr) and expr[i + 1] == "'":
                    i += 2
                    continue
                else:
                    in_quote = False
            else:
                in_quote = True
        elif not in_quote:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None
