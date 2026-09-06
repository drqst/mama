# mama — Oracle to PostgreSQL migration tool
# pg_writer.py  —  DDL generation and execution on PostgreSQL

from __future__ import annotations
import logging
from typing import List, Optional, TYPE_CHECKING
import psycopg2
import psycopg2.extras

from type_mapper import map_type
from sql_converter import convert, get_warnings, clear_warnings

if TYPE_CHECKING:
    from oracle_reader import (
        OracleTable, OracleColumn, OracleConstraint,
        OracleIndex, OracleSequence, OracleView, OracleSourceObject
    )

log = logging.getLogger("mama.pg")


class PgWriter:
    def __init__(self, conn: psycopg2.extensions.connection,
                 target_schema: str = "public",
                 use_identity: bool = False,
                 dry_run: bool = False):
        self.conn = conn
        self.schema = target_schema
        self.use_identity = use_identity
        self.dry_run = dry_run
        self._warnings: List[str] = []

    @property
    def warnings(self) -> List[str]:
        return list(self._warnings)

    def _exec(self, sql: str, params=None):
        if self.dry_run:
            log.info("[DRY-RUN] %s", sql[:200])
            return
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
        self.conn.commit()

    def _warn(self, msg: str):
        self._warnings.append(msg)
        log.warning(msg)

    # ── Schema ─────────────────────────────────────────────────────────────────
    def ensure_schema(self):
        if self.schema != "public":
            self._exec(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')

    # ── Sequences ──────────────────────────────────────────────────────────────
    def create_sequence(self, seq: "OracleSequence") -> str:
        parts = [
            f'CREATE SEQUENCE IF NOT EXISTS "{self.schema}"."{seq.name.lower()}"',
            f"    INCREMENT BY {seq.increment_by}",
            f"    MINVALUE {seq.min_value}",
            f"    MAXVALUE {seq.max_value}",
            f"    START WITH {seq.last_number}",
            f"    CACHE {max(seq.cache_size, 1)}",
            "    CYCLE" if seq.cycle else "    NO CYCLE",
        ]
        ddl = "\n".join(parts) + ";"
        self._exec(ddl)
        return ddl

    # ── Tables ─────────────────────────────────────────────────────────────────
    def _column_ddl(self, col: "OracleColumn") -> str:
        pg_type, warned = map_type(col.full_type)
        if warned:
            self._warn(f"Unknown type '{col.full_type}' for column '{col.name}' — kept as-is")

        parts = [f'    "{col.name.lower()}" {pg_type}']

        if col.data_default:
            default = col.data_default
            # Rewrite NEXTVAL references
            import re
            default = re.sub(
                r'"?(\w+)"?\.NEXTVAL',
                lambda m: f"nextval('{self.schema}.{m.group(1).lower()}')",
                default,
                flags=re.IGNORECASE,
            )
            if self.use_identity and re.search(r'nextval', default, re.IGNORECASE):
                parts[0] = parts[0].rstrip()
                return parts[0] + " GENERATED ALWAYS AS IDENTITY"
            parts.append(f"DEFAULT {default}")

        if not col.nullable:
            parts.append("NOT NULL")

        return " ".join(parts)

    def create_table(self, table: "OracleTable") -> str:
        col_defs = [self._column_ddl(c) for c in table.columns]

        # Inline PRIMARY KEY only
        pk_cons = [c for c in table.constraints if c.type == "P"]
        for pk in pk_cons:
            pk_cols = ", ".join(f'"{c.lower()}"' for c in pk.columns)
            col_defs.append(f'    CONSTRAINT "{pk.name.lower()}" PRIMARY KEY ({pk_cols})')

        ddl = (
            f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{table.name.lower()}" (\n'
            + ",\n".join(col_defs)
            + "\n);"
        )
        self._exec(ddl)
        return ddl

    def add_constraints(self, table: "OracleTable"):
        """Add UNIQUE, CHECK, and FOREIGN KEY constraints after all tables exist."""
        for con in table.constraints:
            if con.type == "P":
                continue  # already inlined

            cols = ", ".join(f'"{c.lower()}"' for c in con.columns)
            tbl = f'"{self.schema}"."{table.name.lower()}"'
            cname = f'"{con.name.lower()}"'

            if con.type == "U":
                ddl = f"ALTER TABLE {tbl} ADD CONSTRAINT {cname} UNIQUE ({cols});"
            elif con.type == "C":
                if con.search_condition:
                    # Skip system-generated NOT NULL checks
                    cond = con.search_condition.strip()
                    if "IS NOT NULL" in cond.upper():
                        continue
                    ddl = f"ALTER TABLE {tbl} ADD CONSTRAINT {cname} CHECK ({cond});"
                else:
                    continue
            elif con.type == "R":
                if not con.r_table:
                    self._warn(f"FK {con.name}: referenced table not found — skipped")
                    continue
                ref_cols = ", ".join(f'"{c.lower()}"' for c in (con.r_columns or []))
                ref_tbl = f'"{self.schema}"."{con.r_table.lower()}"'
                on_delete = ""
                if con.delete_rule and con.delete_rule != "NO ACTION":
                    on_delete = f" ON DELETE {con.delete_rule}"
                ddl = (
                    f"ALTER TABLE {tbl} ADD CONSTRAINT {cname}\n"
                    f"    FOREIGN KEY ({cols})\n"
                    f"    REFERENCES {ref_tbl} ({ref_cols}){on_delete};"
                )
            else:
                continue

            try:
                self._exec(ddl)
            except Exception as exc:
                self._warn(f"Constraint {con.name} failed: {exc}")

    def create_index(self, idx: "OracleIndex"):
        cols = ", ".join(f'"{c.lower()}"' for c in idx.columns)
        tbl = f'"{self.schema}"."{idx.table_name.lower()}"'
        uniq = "UNIQUE " if idx.unique else ""
        ddl = (
            f'CREATE {uniq}INDEX IF NOT EXISTS "{idx.name.lower()}"\n'
            f'    ON {tbl} ({cols});'
        )
        try:
            self._exec(ddl)
        except Exception as exc:
            self._warn(f"Index {idx.name} failed: {exc}")

    # ── Views ──────────────────────────────────────────────────────────────────
    def create_view(self, view: "OracleView") -> str:
        clear_warnings()
        pg_text = convert(view.text, object_name=view.name)
        for w in get_warnings():
            self._warn(f"View {view.name}: {w}")

        ddl = (
            f'CREATE OR REPLACE VIEW "{self.schema}"."{view.name.lower()}" AS\n'
            + pg_text.strip()
            + ";"
        )
        try:
            self._exec(ddl)
        except Exception as exc:
            self._warn(f"View {view.name} failed to create: {exc}")
            ddl = f"-- FAILED: {exc}\n" + ddl
        return ddl

    # ── PL/SQL objects ─────────────────────────────────────────────────────────
    def create_source_object(self, obj: "OracleSourceObject") -> str:
        clear_warnings()
        pg_src = convert(obj.source, object_name=obj.name)
        for w in get_warnings():
            self._warn(f"{obj.type} {obj.name}: {w}")

        # Prefix schema into CREATE OR REPLACE FUNCTION|PROCEDURE
        import re
        pg_src = re.sub(
            r'(CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE))\s+(\w+)',
            rf'\1 "{self.schema}".\2',
            pg_src,
            count=1,
            flags=re.IGNORECASE,
        )

        try:
            self._exec(pg_src)
        except Exception as exc:
            self._warn(f"{obj.type} {obj.name} failed to create: {exc}")
            pg_src = f"-- FAILED: {exc}\n" + pg_src

        return pg_src
