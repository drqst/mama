# mama 🐘

> Oracle → PostgreSQL migration tool

`mama` migrates an entire Oracle schema to PostgreSQL in one command, including:

| What | Details |
|---|---|
| **Tables** | Columns, NOT NULL, defaults |
| **Constraints** | PK, FK, UNIQUE, CHECK |
| **Indexes** | B-tree indexes (NORMAL type) |
| **Sequences** | Converted to PG sequences (or IDENTITY) |
| **Views** | Best-effort SQL rewrite |
| **Functions & Procedures** | PL/SQL → PL/pgSQL converter |
| **Triggers** | `:NEW`/`:OLD` → `NEW`/`OLD`, wrapped in functions |
| **Packages** | Split into individual functions (flagged for review) |
| **Data** | Parallel bulk copy with progress bars |

---

## Install

```bash
pip install -r requirements.txt
```

> Requires Python 3.10+.  
> **No Oracle Instant Client needed** — uses `python-oracledb` in thin mode by default.

---

## Usage

```bash
python migrate.py \
  --oracle-user HR \
  --oracle-password secret \
  --oracle-host db.example.com \
  --oracle-port 1521 \
  --oracle-service ORCL \
  --oracle-schema HR \
  --pg-dsn "postgresql://pguser:pgpass@localhost:5432/mydb" \
  --pg-schema public
```

### Common flags

| Flag | Default | Purpose |
|---|---|---|
| `--tables A,B,C` | *(all)* | Migrate only these tables |
| `--skip-data` | off | Schema only, no rows |
| `--skip-functions` | off | Skip PL/SQL objects |
| `--skip-triggers` | off | Skip triggers |
| `--skip-views` | off | Skip views |
| `--dry-run` | off | Print DDL without executing |
| `--workers N` | 4 | Parallel threads for data copy |
| `--batch-size N` | 10000 | Rows per INSERT batch |
| `--use-identity` | off | IDENTITY columns instead of sequences |
| `--oracle-thick` | off | Thick mode (needs Instant Client) |

---

## Output files

| File | Purpose |
|---|---|
| `migration.log` | Full timestamped log of every operation |
| `conversion_report.md` | Per-object status: ✅ ok / ⚠️ needs review / ❌ failed |

---

## Type mapping

| Oracle | PostgreSQL |
|---|---|
| `NUMBER(p,0)` | `NUMERIC(p,0)` |
| `NUMBER(p,s)` | `NUMERIC(p,s)` |
| `NUMBER` | `NUMERIC` |
| `VARCHAR2(n)` | `VARCHAR(n)` |
| `CLOB` / `LONG` | `TEXT` |
| `BLOB` / `RAW(n)` | `BYTEA` |
| `DATE` | `TIMESTAMP` |
| `TIMESTAMP WITH TIME ZONE` | `TIMESTAMPTZ` |
| `FLOAT` / `BINARY_DOUBLE` | `DOUBLE PRECISION` |
| `XMLTYPE` | `XML` |

---

## PL/SQL conversions

mama auto-converts these common patterns:

| Oracle | PostgreSQL |
|---|---|
| `SYSDATE` | `NOW()` |
| `NVL(a,b)` | `COALESCE(a,b)` |
| `NVL2(x,a,b)` | `CASE WHEN x IS NOT NULL THEN a ELSE b END` |
| `DBMS_OUTPUT.PUT_LINE(x)` | `RAISE NOTICE '%', x` |
| `RAISE_APPLICATION_ERROR(-n, msg)` | `RAISE EXCEPTION '%', msg` |
| `:NEW.col` / `:OLD.col` | `NEW.col` / `OLD.col` |
| `seq.NEXTVAL` | `nextval('seq')` |
| `FROM DUAL` | *(removed)* |
| `EXECUTE IMMEDIATE` | `EXECUTE` |
| `SUBSTR` | `SUBSTRING` |
| `TO_DATE(…)` | `TO_TIMESTAMP(…)` |
| `PRAGMA …` | *(removed with comment)* |
| `COMMIT`/`ROLLBACK` inside function | *(commented out — not allowed in PG functions)* |

Anything that cannot be auto-converted gets a `/* TODO: … */` comment and is listed in `conversion_report.md`.

---

## Architecture

```
migrate.py          CLI entry point & orchestrator
config.py           Config dataclass
oracle_reader.py    Introspects Oracle via ALL_* views
type_mapper.py      Oracle → PostgreSQL type mapping
sql_converter.py    PL/SQL → PL/pgSQL text rewriter
pg_writer.py        Generates & executes DDL on PostgreSQL
data_migrator.py    Parallel bulk data copy
```

---

## Limitations

- **Packages with object types, BULK COLLECT, FORALL** are flagged for manual review.
- **Bitmap indexes, domain indexes, IOTs** are skipped.
- **Oracle Spatial (SDO_GEOMETRY)** maps to `geometry` — requires PostGIS.
- **Partitioned tables** are created as plain tables.
- **Synonyms and database links** are not migrated.
