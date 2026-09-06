# mama — Oracle to PostgreSQL migration tool
# sql_converter.py  —  best-effort PL/SQL → PL/pgSQL rewriter

import re
from typing import List, Tuple

# ── Warnings accumulator ──────────────────────────────────────────────────────
_warnings: List[str] = []

def get_warnings() -> List[str]:
    return list(_warnings)

def clear_warnings():
    _warnings.clear()

def _warn(msg: str):
    _warnings.append(msg)

# ─────────────────────────────────────────────────────────────────────────────
#  Ordered substitution rules  (pattern, replacement, warn_msg_or_None)
# ─────────────────────────────────────────────────────────────────────────────
_RULES: List[Tuple[re.Pattern, str, str]] = []

def _r(pat: str, repl: str, warn: str = ""):
    _RULES.append((re.compile(pat, re.IGNORECASE | re.DOTALL), repl, warn))

# ── SYSDATE / SYSTIMESTAMP ────────────────────────────────────────────────────
_r(r'\bSYSTIMESTAMP\b',  'NOW()')
_r(r'\bSYSDATE\b',       'NOW()')
_r(r'\bCURRENT_TIMESTAMP\b', 'NOW()')

# ── DUAL ──────────────────────────────────────────────────────────────────────
_r(r'\bFROM\s+DUAL\b', '', 'FROM DUAL removed — verify query is still valid')

# ── NVL → COALESCE ────────────────────────────────────────────────────────────
_r(r'\bNVL\s*\(', 'COALESCE(')
_r(r'\bNVL2\s*\(([^,]+),([^,]+),([^)]+)\)',
   r'CASE WHEN \1 IS NOT NULL THEN \2 ELSE \3 END',
   'NVL2 converted to CASE — review carefully')

# ── DECODE → CASE ─────────────────────────────────────────────────────────────
# Simple DECODE(expr, v1, r1, v2, r2, default) patterns are flagged for review
def _decode_to_case(m: re.Match) -> str:
    _warn("DECODE replaced with stub CASE — manual review required")
    return "/* TODO: convert DECODE */ CASE " + m.group(1) + " END"

_r(r'\bDECODE\s*\([^)]+\)',
   '/* TODO: convert DECODE */ CASE  END',
   'DECODE conversion requires manual review')

# ── TO_DATE / TO_TIMESTAMP ────────────────────────────────────────────────────
_r(r'\bTO_DATE\s*\(',      'TO_TIMESTAMP(')
_r(r'\bTO_TIMESTAMP\s*\(', 'TO_TIMESTAMP(')

# ── TO_CHAR ───────────────────────────────────────────────────────────────────
_r(r'\bTO_CHAR\s*\(', 'TO_CHAR(')  # same name, format strings may differ

# ── TO_NUMBER ─────────────────────────────────────────────────────────────────
_r(r'\bTO_NUMBER\s*\(([^)]+)\)', r'CAST(\1 AS NUMERIC)',
   'TO_NUMBER converted to CAST — review format masks if present')

# ── SUBSTR / INSTR ────────────────────────────────────────────────────────────
_r(r'\bSUBSTR\s*\(', 'SUBSTRING(')
_r(r'\bINSTR\s*\(([^,]+),([^)]+)\)', r'POSITION(\2 IN \1)',
   'INSTR with >2 args not supported — check call sites')

# ── LPAD / RPAD / LENGTH ──────────────────────────────────────────────────────
_r(r'\bLENGTH\s*\(', 'LENGTH(')   # same in PG

# ── CONCAT ────────────────────────────────────────────────────────────────────
_r(r'\bCONCAT\s*\(([^,]+),([^)]+)\)', r'(\1 || \2)')

# ── DBMS_OUTPUT ───────────────────────────────────────────────────────────────
_r(r'\bDBMS_OUTPUT\.PUT_LINE\s*\(([^)]+)\)', r'RAISE NOTICE ''%'', \1;')
_r(r'\bDBMS_OUTPUT\.PUT\s*\(([^)]+)\)',      r'RAISE NOTICE ''%'', \1;')

# ── RAISE_APPLICATION_ERROR ───────────────────────────────────────────────────
_r(r'\bRAISE_APPLICATION_ERROR\s*\(\s*-\d+\s*,\s*([^)]+)\)',
   r'RAISE EXCEPTION ''%'', \1',
   'RAISE_APPLICATION_ERROR converted to RAISE EXCEPTION — review error codes')

# ── ROWNUM ────────────────────────────────────────────────────────────────────
_r(r'\bROWNUM\b', '/* ROWNUM → use LIMIT or ROW_NUMBER() OVER() */',
   'ROWNUM requires manual rewrite with LIMIT or window function')

# ── ROWTYPE / TYPE ────────────────────────────────────────────────────────────
_r(r'%ROWTYPE', '%ROWTYPE',
   '%ROWTYPE is supported in PL/pgSQL — verify table name spelling')
_r(r'%TYPE', '%TYPE',  # also supported
   '%TYPE is supported in PL/pgSQL — verify column reference')

# ── IS TABLE / VARRAY / OBJECT ────────────────────────────────────────────────
_r(r'\bIS\s+TABLE\s+OF\b', '/* IS TABLE OF — convert to array or temp table */',
   'PL/SQL TABLE type not supported in PL/pgSQL — manual rewrite required')
_r(r'\bIS\s+VARRAY\s*\(\d+\)\s+OF\b', '/* VARRAY — convert to array */',
   'VARRAY type not supported — manual rewrite required')

# ── EXCEPTION names ───────────────────────────────────────────────────────────
_r(r'\bNO_DATA_FOUND\b',         'NO_DATA_FOUND',  # exists in PG
   'NO_DATA_FOUND: PG raises this differently — test exception handler')
_r(r'\bTOO_MANY_ROWS\b',        'TOO_MANY_ROWS')
_r(r'\bDUP_VAL_ON_INDEX\b',     'unique_violation',
   'DUP_VAL_ON_INDEX → unique_violation in PostgreSQL')
_r(r'\bINVALID_NUMBER\b',       'invalid_text_representation',
   'INVALID_NUMBER → invalid_text_representation in PostgreSQL')

# ── COMMIT / ROLLBACK in functions ────────────────────────────────────────────
_r(r'\bCOMMIT\b', '-- COMMIT (not allowed inside PG functions — remove or use procedures)',
   'COMMIT inside function is not allowed in PostgreSQL')
_r(r'\bROLLBACK\b', '-- ROLLBACK (not allowed inside PG functions — remove or use procedures)',
   'ROLLBACK inside function is not allowed in PostgreSQL')

# ── PL/SQL IS/AS → PL/pgSQL AS $$ ────────────────────────────────────────────
# Handled separately in convert_function_header()

# ── :NEW / :OLD → NEW / OLD  (triggers) ──────────────────────────────────────
_r(r':NEW\.', 'NEW.')
_r(r':OLD\.', 'OLD.')

# ── PRAGMA ────────────────────────────────────────────────────────────────────
_r(r'\bPRAGMA\s+\w+[^;]*;', '-- PRAGMA removed (not supported in PostgreSQL)',
   'PRAGMA directive removed')

# ── EXECUTE IMMEDIATE ─────────────────────────────────────────────────────────
_r(r'\bEXECUTE\s+IMMEDIATE\b', 'EXECUTE',
   'EXECUTE IMMEDIATE → EXECUTE; verify USING/INTO clauses')

# ── FORALL ────────────────────────────────────────────────────────────────────
_r(r'\bFORALL\b', '/* FORALL — convert to FOREACH or set-based INSERT/UPDATE */',
   'FORALL not supported in PL/pgSQL — manual rewrite required')

# ── BULK COLLECT ─────────────────────────────────────────────────────────────
_r(r'\bBULK\s+COLLECT\s+INTO\b', '/* BULK COLLECT INTO — use SELECT INTO array */',
   'BULK COLLECT not supported in PL/pgSQL — use arrays or RETURN QUERY')

# ── Oracle sequence.NEXTVAL → nextval() ───────────────────────────────────────
_r(r'(\w+)\.NEXTVAL', r"nextval('\1')")
_r(r'(\w+)\.CURRVAL', r"currval('\1')")

# ── PACKAGE header strip ──────────────────────────────────────────────────────
_r(r'CREATE\s+OR\s+REPLACE\s+PACKAGE\s+BODY\s+\w+\s+(IS|AS)',
   '-- Package body split into individual functions below',
   'Package body must be split into individual PostgreSQL functions manually')

# ─────────────────────────────────────────────────────────────────────────────
def _convert_function_header(src: str) -> str:
    """
    Rewrite CREATE OR REPLACE FUNCTION/PROCEDURE declaration.
    Oracle:
        CREATE OR REPLACE FUNCTION foo(x NUMBER) RETURN VARCHAR2 IS
    PostgreSQL:
        CREATE OR REPLACE FUNCTION foo(x NUMERIC) RETURNS VARCHAR AS $$
    """
    from type_mapper import map_type

    # PROCEDURE → FUNCTION returning void
    src = re.sub(
        r'\bCREATE\s+OR\s+REPLACE\s+PROCEDURE\b',
        'CREATE OR REPLACE FUNCTION',
        src, flags=re.IGNORECASE
    )

    # Move RETURN <type> to RETURNS <type>
    def _fix_return(m: re.Match) -> str:
        ret_type = m.group(1).strip()
        pg_type, warned = map_type(ret_type)
        if warned:
            _warn(f"Unknown return type '{ret_type}' — kept as-is")
        return f') RETURNS {pg_type} AS $$\nDECLARE'

    src = re.sub(
        r'\)\s+RETURN\s+([\w\s\(\),]+?)\s+(IS|AS)\b',
        _fix_return,
        src,
        flags=re.IGNORECASE
    )

    # IS/AS without RETURN → procedure (void)
    src = re.sub(
        r'\)\s+(IS|AS)\b(?!\s*\$\$)',
        ') RETURNS void AS $$\nDECLARE',
        src,
        flags=re.IGNORECASE
    )

    # End of function: replace final END [name]; with END; $$ LANGUAGE plpgsql;
    src = re.sub(
        r'\bEND\s+\w+\s*;',
        'END;\n$$ LANGUAGE plpgsql;',
        src,
        flags=re.IGNORECASE
    )
    # Bare END;
    src = re.sub(
        r'\bEND\s*;\s*$',
        'END;\n$$ LANGUAGE plpgsql;',
        src,
        flags=re.IGNORECASE | re.MULTILINE
    )

    return src


def convert(oracle_sql: str, object_name: str = "") -> str:
    """
    Convert Oracle PL/SQL source to PL/pgSQL.

    Parameters
    ----------
    oracle_sql   : Raw PL/SQL source text.
    object_name  : Object name used in warning messages.

    Returns
    -------
    Best-effort PL/pgSQL source.
    """
    src = oracle_sql

    # Fix function/procedure header first (needs special parsing)
    src = _convert_function_header(src)

    # Apply substitution rules in order
    for pattern, repl, warn in _RULES:
        new_src = pattern.sub(repl, src)
        if new_src != src and warn:
            _warn(f"[{object_name}] {warn}")
        src = new_src

    return src
