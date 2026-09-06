# mama — Oracle to PostgreSQL migration tool
# type_mapper.py  —  Oracle → PostgreSQL data-type translation
import re
from typing import Tuple

# Static mapping for simple / parameterised Oracle types.
# Each entry: (regex-pattern, replacement-template)
# Templates may use \1, \2 … for captured groups from the pattern.
_TYPE_MAP = [
    # ── Numeric ──────────────────────────────────────────────────────────────
    (r"NUMBER\s*\(\s*(\d+)\s*,\s*0\s*\)",      r"NUMERIC(\1,0)"),    # exact integer
    (r"NUMBER\s*\(\s*(\d+)\s*\)",               r"NUMERIC(\1)"),
    (r"NUMBER\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",  r"NUMERIC(\1,\2)"),
    (r"NUMBER\b",                                r"NUMERIC"),
    (r"INTEGER\b",                               r"BIGINT"),
    (r"INT\b",                                   r"INTEGER"),
    (r"SMALLINT\b",                              r"SMALLINT"),
    (r"FLOAT\s*\(\s*\d+\s*\)",                  r"DOUBLE PRECISION"),
    (r"FLOAT\b",                                 r"DOUBLE PRECISION"),
    (r"BINARY_FLOAT\b",                          r"REAL"),
    (r"BINARY_DOUBLE\b",                         r"DOUBLE PRECISION"),
    # ── Character ─────────────────────────────────────────────────────────────
    (r"VARCHAR2\s*\(\s*(\d+)\s*(?:BYTE|CHAR)?\s*\)", r"VARCHAR(\1)"),
    (r"NVARCHAR2\s*\(\s*(\d+)\s*\)",            r"VARCHAR(\1)"),
    (r"CHAR\s*\(\s*(\d+)\s*(?:BYTE|CHAR)?\s*\)",r"CHAR(\1)"),
    (r"NCHAR\s*\(\s*(\d+)\s*\)",                r"CHAR(\1)"),
    (r"CLOB\b",                                  r"TEXT"),
    (r"NCLOB\b",                                 r"TEXT"),
    (r"LONG\b",                                  r"TEXT"),
    (r"VARCHAR\s*\(\s*(\d+)\s*\)",               r"VARCHAR(\1)"),
    # ── Binary ────────────────────────────────────────────────────────────────
    (r"BLOB\b",                                  r"BYTEA"),
    (r"RAW\s*\(\s*(\d+)\s*\)",                  r"BYTEA"),
    (r"LONG\s+RAW\b",                            r"BYTEA"),
    # ── Date / Time ───────────────────────────────────────────────────────────
    (r"TIMESTAMP\s*\(\s*(\d+)\s*\)\s*WITH\s+LOCAL\s+TIME\s+ZONE", r"TIMESTAMPTZ"),
    (r"TIMESTAMP\s*\(\s*(\d+)\s*\)\s*WITH\s+TIME\s+ZONE",         r"TIMESTAMPTZ"),
    (r"TIMESTAMP\s*WITH\s+LOCAL\s+TIME\s+ZONE",                    r"TIMESTAMPTZ"),
    (r"TIMESTAMP\s*WITH\s+TIME\s+ZONE",                            r"TIMESTAMPTZ"),
    (r"TIMESTAMP\s*\(\s*(\d+)\s*\)",            r"TIMESTAMP(\1)"),
    (r"TIMESTAMP\b",                             r"TIMESTAMP"),
    (r"DATE\b",                                  r"TIMESTAMP"),  # Oracle DATE has time part
    (r"INTERVAL\s+YEAR\s*\(\s*\d+\s*\)\s+TO\s+MONTH", r"INTERVAL"),
    (r"INTERVAL\s+DAY\s*\(\s*\d+\s*\)\s+TO\s+SECOND\s*\(\s*\d+\s*\)", r"INTERVAL"),
    # ── Misc ──────────────────────────────────────────────────────────────────
    (r"XMLTYPE\b",                               r"XML"),
    (r"ROWID\b",                                 r"TEXT"),
    (r"UROWID\s*(?:\(\s*\d+\s*\))?",           r"TEXT"),
    (r"SDO_GEOMETRY\b",                          r"geometry"),   # requires PostGIS
]

# Compile once
_COMPILED = [(re.compile(pattern, re.IGNORECASE), repl) for pattern, repl in _TYPE_MAP]


def map_type(oracle_type: str) -> Tuple[str, bool]:
    """
    Convert an Oracle column type string to its PostgreSQL equivalent.

    Returns:
        (pg_type, warned) — warned=True when the type required a lossy mapping
        or was not recognised.
    """
    t = oracle_type.strip()
    for pattern, repl in _COMPILED:
        new_t = pattern.sub(repl, t)
        if new_t != t:
            return new_t, False
    # Unknown — pass through and warn
    return t, True
