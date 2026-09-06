# mama — Oracle to PostgreSQL migration tool
# oracle_reader.py  —  reads Oracle schema metadata via ALL_* views

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import oracledb

log = logging.getLogger("mama.oracle")


@dataclass
class OracleColumn:
    name: str
    data_type: str
    nullable: bool
    data_default: Optional[str]
    char_length: Optional[int]
    data_precision: Optional[int]
    data_scale: Optional[int]

    @property
    def full_type(self) -> str:
        """Return the full Oracle type string including length/precision."""
        t = self.data_type
        if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR") and self.char_length:
            return f"{t}({self.char_length})"
        if t in ("NUMBER",) and self.data_precision is not None:
            if self.data_scale is not None:
                return f"{t}({self.data_precision},{self.data_scale})"
            return f"{t}({self.data_precision})"
        if t.startswith("TIMESTAMP") and self.data_precision is not None:
            return f"{t}({self.data_precision})"
        if t in ("RAW",) and self.char_length:
            return f"{t}({self.char_length})"
        return t


@dataclass
class OracleConstraint:
    name: str
    type: str          # P, U, R, C
    columns: List[str]
    r_owner: Optional[str] = None
    r_table: Optional[str] = None
    r_columns: Optional[List[str]] = None
    search_condition: Optional[str] = None
    delete_rule: Optional[str] = None


@dataclass
class OracleIndex:
    name: str
    table_name: str
    unique: bool
    columns: List[str]


@dataclass
class OracleSequence:
    name: str
    min_value: int
    max_value: int
    increment_by: int
    cycle: bool
    cache_size: int
    last_number: int


@dataclass
class OracleTable:
    name: str
    columns: List[OracleColumn] = field(default_factory=list)
    constraints: List[OracleConstraint] = field(default_factory=list)
    indexes: List[OracleIndex] = field(default_factory=list)


@dataclass
class OracleView:
    name: str
    text: str


@dataclass
class OracleSourceObject:
    name: str
    type: str    # FUNCTION, PROCEDURE, TRIGGER, PACKAGE, PACKAGE BODY, TYPE
    lines: List[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return "".join(self.lines)


class OracleReader:
    def __init__(self, conn: oracledb.Connection, schema: str):
        self.conn = conn
        self.schema = schema.upper()

    # ── Tables ─────────────────────────────────────────────────────────────
    def tables(self, filter_list: Optional[List[str]] = None) -> List[str]:
        cur = self.conn.cursor()
        sql = "SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = :s ORDER BY TABLE_NAME"
        cur.execute(sql, s=self.schema)
        names = [r[0] for r in cur.fetchall()]
        if filter_list:
            up = {t.upper() for t in filter_list}
            names = [n for n in names if n in up]
        return names

    def columns(self, table: str) -> List[OracleColumn]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE,
                   DATA_DEFAULT, CHAR_LENGTH,
                   DATA_PRECISION, DATA_SCALE
            FROM   ALL_TAB_COLUMNS
            WHERE  OWNER = :s AND TABLE_NAME = :t
            ORDER  BY COLUMN_ID
        """, s=self.schema, t=table.upper())
        cols = []
        for row in cur.fetchall():
            name, dtype, nullable, default, cl, dp, ds = row
            # Strip trailing whitespace from default values
            if default:
                default = default.strip()
            cols.append(OracleColumn(
                name=name,
                data_type=dtype,
                nullable=(nullable == "Y"),
                data_default=default,
                char_length=cl,
                data_precision=dp,
                data_scale=ds,
            ))
        return cols

    def constraints(self, table: str) -> List[OracleConstraint]:
        cur = self.conn.cursor()
        # Fetch constraint metadata
        cur.execute("""
            SELECT c.CONSTRAINT_NAME, c.CONSTRAINT_TYPE,
                   c.R_OWNER, c.R_CONSTRAINT_NAME,
                   c.SEARCH_CONDITION, c.DELETE_RULE
            FROM   ALL_CONSTRAINTS c
            WHERE  c.OWNER = :s AND c.TABLE_NAME = :t
              AND  c.CONSTRAINT_TYPE IN ('P','U','R','C')
            ORDER  BY c.CONSTRAINT_TYPE, c.CONSTRAINT_NAME
        """, s=self.schema, t=table.upper())
        raw = cur.fetchall()

        result = []
        for cname, ctype, r_owner, r_con_name, search_cond, del_rule in raw:
            # Column list
            cur2 = self.conn.cursor()
            cur2.execute("""
                SELECT COLUMN_NAME
                FROM   ALL_CONS_COLUMNS
                WHERE  OWNER = :s AND CONSTRAINT_NAME = :c
                ORDER  BY POSITION
            """, s=self.schema, c=cname)
            cols = [r[0] for r in cur2.fetchall()]

            # Resolve FK referenced table
            r_table = None
            r_cols = None
            if ctype == "R" and r_con_name:
                cur3 = self.conn.cursor()
                cur3.execute("""
                    SELECT c.TABLE_NAME
                    FROM   ALL_CONSTRAINTS c
                    WHERE  c.OWNER = :s AND c.CONSTRAINT_NAME = :c
                """, s=(r_owner or self.schema), c=r_con_name)
                row = cur3.fetchone()
                if row:
                    r_table = row[0]
                # Referenced columns
                cur3.execute("""
                    SELECT COLUMN_NAME
                    FROM   ALL_CONS_COLUMNS
                    WHERE  OWNER = :s AND CONSTRAINT_NAME = :c
                    ORDER  BY POSITION
                """, s=(r_owner or self.schema), c=r_con_name)
                r_cols = [r[0] for r in cur3.fetchall()]

            result.append(OracleConstraint(
                name=cname,
                type=ctype,
                columns=cols,
                r_owner=r_owner,
                r_table=r_table,
                r_columns=r_cols,
                search_condition=search_cond,
                delete_rule=del_rule,
            ))
        return result

    def indexes(self, table: str) -> List[OracleIndex]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT i.INDEX_NAME, i.UNIQUENESS
            FROM   ALL_INDEXES i
            WHERE  i.OWNER = :s AND i.TABLE_NAME = :t
              AND  i.INDEX_TYPE = 'NORMAL'
        """, s=self.schema, t=table.upper())
        raw = cur.fetchall()
        result = []
        for iname, uniq in raw:
            cur2 = self.conn.cursor()
            cur2.execute("""
                SELECT COLUMN_NAME
                FROM   ALL_IND_COLUMNS
                WHERE  INDEX_OWNER = :s AND INDEX_NAME = :i
                ORDER  BY COLUMN_POSITION
            """, s=self.schema, i=iname)
            cols = [r[0] for r in cur2.fetchall()]
            result.append(OracleIndex(
                name=iname,
                table_name=table,
                unique=(uniq == "UNIQUE"),
                columns=cols,
            ))
        return result

    def sequences(self) -> List[OracleSequence]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT SEQUENCE_NAME, MIN_VALUE, MAX_VALUE,
                   INCREMENT_BY, CYCLE_FLAG, CACHE_SIZE, LAST_NUMBER
            FROM   ALL_SEQUENCES
            WHERE  SEQUENCE_OWNER = :s
            ORDER  BY SEQUENCE_NAME
        """, s=self.schema)
        result = []
        for row in cur.fetchall():
            name, minv, maxv, inc, cycle, cache, last = row
            result.append(OracleSequence(
                name=name,
                min_value=int(minv),
                max_value=int(maxv),
                increment_by=int(inc),
                cycle=(cycle == "Y"),
                cache_size=int(cache),
                last_number=int(last),
            ))
        return result

    def views(self) -> List[OracleView]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT VIEW_NAME, TEXT
            FROM   ALL_VIEWS
            WHERE  OWNER = :s
            ORDER  BY VIEW_NAME
        """, s=self.schema)
        return [OracleView(name=r[0], text=r[1]) for r in cur.fetchall()]

    def source_objects(self, obj_types: Optional[List[str]] = None) -> List[OracleSourceObject]:
        """
        Fetch PL/SQL source for functions, procedures, packages, triggers, etc.
        """
        if obj_types is None:
            obj_types = ["FUNCTION", "PROCEDURE", "TRIGGER",
                         "PACKAGE", "PACKAGE BODY", "TYPE"]
        placeholders = ",".join(f":t{i}" for i in range(len(obj_types)))
        bind = {f"t{i}": t for i, t in enumerate(obj_types)}
        bind["s"] = self.schema

        cur = self.conn.cursor()
        cur.execute(f"""
            SELECT NAME, TYPE, LINE, TEXT
            FROM   ALL_SOURCE
            WHERE  OWNER = :s AND TYPE IN ({placeholders})
            ORDER  BY NAME, TYPE, LINE
        """, bind)

        objects: Dict[str, OracleSourceObject] = {}
        for name, typ, _line, text in cur.fetchall():
            key = f"{typ}::{name}"
            if key not in objects:
                objects[key] = OracleSourceObject(name=name, type=typ)
            objects[key].lines.append(text)

        return list(objects.values())

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{self.schema}"."{table}"')
        return cur.fetchone()[0]

    def fetch_rows(self, table: str, batch_size: int = 10_000):
        """Generator that yields lists of rows in batches."""
        cur = self.conn.cursor()
        cur.arraysize = batch_size
        cur.execute(f'SELECT * FROM "{self.schema}"."{table}"')
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            yield rows

    def column_names(self, table: str) -> List[str]:
        cur = self.conn.cursor()
        cur.execute(f'SELECT * FROM "{self.schema}"."{table}" WHERE 1=0')
        return [d[0] for d in cur.description]
