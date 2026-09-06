#!/usr/bin/env bash
# mama.sh — interactive wrapper around migrate.py
# Usage:
#   ./mama.sh
#   ./mama.sh --from oracle --to postgres
#   ./mama.sh --from user/pass@host:1521/SERVICE --to postgresql://u:p@host:5432/db
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

CYAN='\033[0;36m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

banner() {
  echo -e "${CYAN}"
  echo '  __  __   ___    __  __   ___  '
  echo ' |  \/  | / _ \  |  \/  | / _ \ '
  echo ' | |\/| || |_| | | |\/| || |_| |'
  echo ' |_|  |_| \__,_| |_|  |_| \__,_|'
  echo ''
  echo '  Oracle → PostgreSQL  ·  interactive launcher'
  echo -e "${RESET}"
}

# ── helpers ───────────────────────────────────────────────────────────────────
ask() {
  # ask <var_name> <prompt> [default]
  local var="$1" prompt="$2" default="${3:-}"
  local hint=""
  [[ -n "$default" ]] && hint=" ${YELLOW}[${default}]${RESET}"
  echo -en "  ${CYAN}?${RESET} ${prompt}${hint}: "
  read -r input
  input="${input:-$default}"
  printf -v "$var" '%s' "$input"
}

ask_secret() {
  local var="$1" prompt="$2"
  echo -en "  ${CYAN}?${RESET} ${prompt}: "
  read -rs input
  echo
  printf -v "$var" '%s' "$input"
}

ask_yn() {
  # ask_yn <var_name> <prompt> <default_y|n>
  local var="$1" prompt="$2" default="${3:-n}"
  local hint; [[ "$default" == "y" ]] && hint="Y/n" || hint="y/N"
  echo -en "  ${CYAN}?${RESET} ${prompt} ${YELLOW}[${hint}]${RESET}: "
  read -r input
  input="${input:-$default}"
  if [[ "${input,,}" == "y" || "${input,,}" == "yes" ]]; then
    printf -v "$var" 'y'
  else
    printf -v "$var" 'n'
  fi
}

section() { echo -e "\n${BOLD}${GREEN}── $* ──${RESET}"; }
info()    { echo -e "  ${GREEN}✓${RESET} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
err()     { echo -e "  ${RED}✗${RESET} $*"; exit 1; }

# ── parse --from / --to from CLI args ─────────────────────────────────────────
RAW_FROM="" RAW_TO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) RAW_FROM="$2"; shift 2 ;;
    --to)   RAW_TO="$2";   shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--from <oracle_dsn>] [--to <pg_dsn>]"
      echo ""
      echo "  --from  Oracle connection. Accepted formats:"
      echo "            user/pass@host:port/SERVICE"
      echo "            user@host:port/SERVICE"
      echo "            host:port/SERVICE"
      echo "            oracle   (keyword — prompts for everything)"
      echo ""
      echo "  --to    PostgreSQL DSN. Accepted formats:"
      echo "            postgresql://user:pass@host:port/dbname"
      echo "            user@host:port/dbname"
      echo "            dbname"
      echo "            postgres  (keyword — prompts for everything)"
      exit 0 ;;
    *) warn "Unknown argument: $1"; shift ;;
  esac
done

# ── parse Oracle DSN ──────────────────────────────────────────────────────────
# Formats: user/pass@host:port/service  |  user@host:port/service  |  host:port/service
O_USER="" O_PASS="" O_HOST="" O_PORT="1521" O_SERVICE="" O_SID=""

parse_oracle() {
  local raw="$1"
  # Strip keyword
  [[ "$raw" == "oracle" || "$raw" == "Oracle" ]] && return

  # user/pass@host:port/service
  if [[ "$raw" =~ ^([^/@]+)/([^@]*)@([^:]+):([0-9]+)/(.+)$ ]]; then
    O_USER="${BASH_REMATCH[1]}"; O_PASS="${BASH_REMATCH[2]}"
    O_HOST="${BASH_REMATCH[3]}"; O_PORT="${BASH_REMATCH[4]}"; O_SERVICE="${BASH_REMATCH[5]}"
  # user@host:port/service
  elif [[ "$raw" =~ ^([^/@]+)@([^:]+):([0-9]+)/(.+)$ ]]; then
    O_USER="${BASH_REMATCH[1]}"; O_HOST="${BASH_REMATCH[2]}"
    O_PORT="${BASH_REMATCH[3]}"; O_SERVICE="${BASH_REMATCH[4]}"
  # host:port/service
  elif [[ "$raw" =~ ^([^:/@]+):([0-9]+)/(.+)$ ]]; then
    O_HOST="${BASH_REMATCH[1]}"; O_PORT="${BASH_REMATCH[2]}"; O_SERVICE="${BASH_REMATCH[3]}"
  # host/service
  elif [[ "$raw" =~ ^([^:/@]+)/(.+)$ ]]; then
    O_HOST="${BASH_REMATCH[1]}"; O_SERVICE="${BASH_REMATCH[2]}"
  else
    warn "Could not fully parse Oracle DSN '$raw' — will prompt for missing values"
  fi
}

[[ -n "$RAW_FROM" ]] && parse_oracle "$RAW_FROM"

# ── parse PostgreSQL DSN ───────────────────────────────────────────────────────
PG_USER="" PG_PASS="" PG_HOST="localhost" PG_PORT="5432" PG_DB="" PG_SCHEMA="public"

parse_pg() {
  local raw="$1"
  [[ "$raw" == "postgres" || "$raw" == "postgresql" ]] && return

  # Full DSN: postgresql://user:pass@host:port/dbname
  if [[ "$raw" =~ ^postgresql://([^:@]+):([^@]*)@([^:]+):([0-9]+)/(.+)$ ]]; then
    PG_USER="${BASH_REMATCH[1]}"; PG_PASS="${BASH_REMATCH[2]}"
    PG_HOST="${BASH_REMATCH[3]}"; PG_PORT="${BASH_REMATCH[4]}"; PG_DB="${BASH_REMATCH[5]}"
  # postgresql://user@host:port/dbname
  elif [[ "$raw" =~ ^postgresql://([^:@]+)@([^:]+):([0-9]+)/(.+)$ ]]; then
    PG_USER="${BASH_REMATCH[1]}"; PG_HOST="${BASH_REMATCH[2]}"
    PG_PORT="${BASH_REMATCH[3]}"; PG_DB="${BASH_REMATCH[4]}"
  # postgresql://host/dbname
  elif [[ "$raw" =~ ^postgresql://([^:/]+)/(.+)$ ]]; then
    PG_HOST="${BASH_REMATCH[1]}"; PG_DB="${BASH_REMATCH[2]}"
  # user@host:port/dbname
  elif [[ "$raw" =~ ^([^@]+)@([^:]+):([0-9]+)/(.+)$ ]]; then
    PG_USER="${BASH_REMATCH[1]}"; PG_HOST="${BASH_REMATCH[2]}"
    PG_PORT="${BASH_REMATCH[3]}"; PG_DB="${BASH_REMATCH[4]}"
  # just a dbname
  elif [[ "$raw" =~ ^[a-zA-Z0-9_]+$ ]]; then
    PG_DB="$raw"
  else
    warn "Could not fully parse PostgreSQL DSN '$raw' — will prompt for missing values"
  fi
}

[[ -n "$RAW_TO" ]] && parse_pg "$RAW_TO"

# ─────────────────────────────────────────────────────────────────────────────
banner

# ─────────────────────────────────────────────────────────────────────────────
section "Oracle source"

[[ -z "$O_HOST"    ]] && ask    O_HOST    "Host"             "localhost"
[[ -z "$O_PORT"    ]] && ask    O_PORT    "Port"             "1521"
[[ -z "$O_SERVICE" && -z "$O_SID" ]] && {
  ask O_SERVICE "Service name (or press Enter to use SID)" ""
  if [[ -z "$O_SERVICE" ]]; then
    ask O_SID "SID" "ORCL"
  fi
}
[[ -z "$O_USER"    ]] && ask    O_USER    "Username"         ""
[[ -z "$O_PASS"    ]] && ask_secret O_PASS "Password"

# Default schema = oracle user
ask O_SCHEMA "Schema to migrate" "${O_USER^^}"

# Thick mode
ask_yn THICK "Use thick mode? (requires Oracle Instant Client)" "n"

# ─────────────────────────────────────────────────────────────────────────────
section "PostgreSQL target"

[[ -z "$PG_HOST"   ]] && ask    PG_HOST   "Host"             "localhost"
[[ -z "$PG_PORT"   ]] && ask    PG_PORT   "Port"             "5432"
[[ -z "$PG_DB"     ]] && ask    PG_DB     "Database name"    ""
[[ -z "$PG_USER"   ]] && ask    PG_USER   "Username"         "postgres"
[[ -z "$PG_PASS"   ]] && ask_secret PG_PASS "Password"
[[ -z "$PG_SCHEMA" ]] && ask    PG_SCHEMA "Target schema"    "public"

# ─────────────────────────────────────────────────────────────────────────────
section "Migration options"

ask_yn SKIP_DATA      "Skip data migration (schema only)?"     "n"
ask_yn SKIP_FUNCTIONS "Skip PL/SQL functions and procedures?"  "n"
ask_yn SKIP_TRIGGERS  "Skip triggers?"                         "n"
ask_yn SKIP_VIEWS     "Skip views?"                            "n"
ask_yn USE_IDENTITY   "Use IDENTITY columns instead of sequences?" "n"
ask_yn DRY_RUN        "Dry run (print DDL, no writes)?"        "n"

ask WORKERS    "Parallel worker threads"  "4"
ask BATCH_SIZE "Batch size (rows)"        "10000"

TABLES=""
ask TABLES "Tables to migrate (comma-separated, blank = all)" ""

# ─────────────────────────────────────────────────────────────────────────────
section "Summary"

echo -e "  ${BOLD}Oracle${RESET}     ${O_USER}@${O_HOST}:${O_PORT}/${O_SERVICE:-${O_SID}}"
echo -e "  ${BOLD}Schema${RESET}     ${O_SCHEMA}"
echo -e "  ${BOLD}→ PG${RESET}       ${PG_USER}@${PG_HOST}:${PG_PORT}/${PG_DB} (schema: ${PG_SCHEMA})"
echo -e "  ${BOLD}Data${RESET}       $( [[ "$SKIP_DATA" == "y" ]] && echo "skipped" || echo "${WORKERS} workers · ${BATCH_SIZE} rows/batch" )"
echo -e "  ${BOLD}Functions${RESET}  $( [[ "$SKIP_FUNCTIONS" == "y" ]] && echo "skipped" || echo "yes" )"
echo -e "  ${BOLD}Triggers${RESET}   $( [[ "$SKIP_TRIGGERS" == "y" ]] && echo "skipped" || echo "yes" )"
echo -e "  ${BOLD}Views${RESET}      $( [[ "$SKIP_VIEWS" == "y" ]] && echo "skipped" || echo "yes" )"
[[ -n "$TABLES" ]] && echo -e "  ${BOLD}Tables${RESET}     ${TABLES}"
[[ "$DRY_RUN" == "y" ]] && echo -e "  ${YELLOW}DRY RUN — no writes${RESET}"
echo ""

ask_yn CONFIRM "Proceed with migration?" "y"
[[ "$CONFIRM" != "y" ]] && { echo "Aborted."; exit 0; }

# ─────────────────────────────────────────────────────────────────────────────
# Build pg DSN
PG_DSN="postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}"

# Build command
CMD=(python migrate.py
  --oracle-user     "$O_USER"
  --oracle-password "$O_PASS"
  --oracle-host     "$O_HOST"
  --oracle-port     "$O_PORT"
  --oracle-schema   "$O_SCHEMA"
  --pg-dsn          "$PG_DSN"
  --pg-schema       "$PG_SCHEMA"
  --workers         "$WORKERS"
  --batch-size      "$BATCH_SIZE"
)

[[ -n "$O_SERVICE"         ]] && CMD+=(--oracle-service "$O_SERVICE")
[[ -n "$O_SID"             ]] && CMD+=(--oracle-sid     "$O_SID")
[[ "$THICK" == "y"         ]] && CMD+=(--oracle-thick)
[[ "$SKIP_DATA" == "y"     ]] && CMD+=(--skip-data)
[[ "$SKIP_FUNCTIONS" == "y"]] && CMD+=(--skip-functions)
[[ "$SKIP_TRIGGERS" == "y" ]] && CMD+=(--skip-triggers)
[[ "$SKIP_VIEWS" == "y"    ]] && CMD+=(--skip-views)
[[ "$USE_IDENTITY" == "y"  ]] && CMD+=(--use-identity)
[[ "$DRY_RUN" == "y"       ]] && CMD+=(--dry-run)
[[ -n "$TABLES"             ]] && CMD+=(--tables "$TABLES")

# Run from the script directory (where migrate.py lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

section "Running mama"
"${CMD[@]}"