# mama — Oracle to PostgreSQL migration tool
# config.py  —  connection + tuning configuration
from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class Config:
    # ── Oracle ────────────────────────────────────────────────────────────────
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_host: str = "localhost"
    oracle_port: int = 1521
    oracle_service: str = ""          # service name (preferred) …
    oracle_sid: str = ""              # … or SID
    oracle_schema: str = ""           # schema to migrate (defaults to oracle_user)
    oracle_thick_mode: bool = False   # True = needs Oracle Instant Client

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    pg_dsn: str = ""                  # postgresql://user:pass@host:5432/db

    # ── Scope ─────────────────────────────────────────────────────────────────
    tables: List[str] = field(default_factory=list)   # empty = all tables
    skip_data: bool = False
    skip_functions: bool = False
    skip_triggers: bool = False
    skip_views: bool = False
    dry_run: bool = False

    # ── Tuning ────────────────────────────────────────────────────────────────
    batch_size: int = 10_000
    workers: int = 4
    use_identity: bool = False        # True → GENERATED ALWAYS AS IDENTITY instead of sequences
