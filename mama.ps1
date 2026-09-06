<#
.SYNOPSIS
  mama.ps1 — interactive wrapper around migrate.py

.DESCRIPTION
  Prompts for all Oracle and PostgreSQL connection details, migration options,
  and then launches migrate.py with the assembled arguments.

.PARAMETER From
  Oracle connection string. Accepted formats:
    user/pass@host:port/SERVICE
    user@host:port/SERVICE
    host:port/SERVICE
    oracle   (keyword — prompts for everything)

.PARAMETER To
  PostgreSQL DSN. Accepted formats:
    postgresql://user:pass@host:port/dbname
    user@host:port/dbname
    dbname
    postgres  (keyword — prompts for everything)

.EXAMPLE
  .\mama.ps1
  .\mama.ps1 --From oracle --To postgres
  .\mama.ps1 --From "user/pass@db.local:1521/ORCL" --To "postgresql://pg:pw@localhost/mydb"
#>
[CmdletBinding()]
param(
    [string]$From = "",
    [string]$To   = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── colour helpers ────────────────────────────────────────────────────────────
function Write-Cyan($t)   { Write-Host $t -ForegroundColor Cyan }
function Write-Green($t)  { Write-Host $t -ForegroundColor Green }
function Write-Yellow($t) { Write-Host $t -ForegroundColor Yellow }
function Write-Red($t)    { Write-Host $t -ForegroundColor Red }

function Show-Banner {
    Write-Cyan ""
    Write-Cyan "  __  __   ___    __  __   ___  "
    Write-Cyan " |  \/  | / _ \  |  \/  | / _ \ "
    Write-Cyan " | |\/| || |_| | | |\/| || |_| |"
    Write-Cyan " |_|  |_| \__,_| |_|  |_| \__,_|"
    Write-Cyan ""
    Write-Cyan "  Oracle -> PostgreSQL  ·  interactive launcher"
    Write-Cyan ""
}

function Show-Section($title) {
    Write-Host ""
    Write-Host "── $title ──" -ForegroundColor Green -NoNewline
    Write-Host ""
}

# ── prompt helpers ────────────────────────────────────────────────────────────
function Ask-Value {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )
    $hint = if ($Default) { " [$Default]" } else { "" }
    Write-Host "  ? $Prompt$hint" -ForegroundColor Cyan -NoNewline
    Write-Host ": " -NoNewline
    $val = Read-Host
    if ([string]::IsNullOrWhiteSpace($val)) { $val = $Default }
    return $val
}

function Ask-Secret {
    param([string]$Prompt)
    Write-Host "  ? $Prompt" -ForegroundColor Cyan -NoNewline
    Write-Host ": " -NoNewline
    $ss = Read-Host -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Ask-YesNo {
    param(
        [string]$Prompt,
        [bool]$Default = $false
    )
    $hint = if ($Default) { "Y/n" } else { "y/N" }
    Write-Host "  ? $Prompt [$hint]" -ForegroundColor Cyan -NoNewline
    Write-Host ": " -NoNewline
    $val = (Read-Host).Trim().ToLower()
    if ([string]::IsNullOrWhiteSpace($val)) { return $Default }
    return ($val -eq "y" -or $val -eq "yes")
}

# ── Oracle DSN parser ──────────────────────────────────────────────────────────
function Parse-OracleDsn {
    param([string]$Raw)
    $o = @{
        User    = ""
        Pass    = ""
        Host    = ""
        Port    = "1521"
        Service = ""
        Sid     = ""
    }
    if ([string]::IsNullOrWhiteSpace($Raw) -or
        $Raw -eq "oracle" -or $Raw -eq "Oracle") { return $o }

    # user/pass@host:port/service
    if ($Raw -match '^([^/@]+)/([^@]*)@([^:]+):(\d+)/(.+)$') {
        $o.User = $Matches[1]; $o.Pass = $Matches[2]
        $o.Host = $Matches[3]; $o.Port = $Matches[4]; $o.Service = $Matches[5]
    }
    # user@host:port/service
    elseif ($Raw -match '^([^/@]+)@([^:]+):(\d+)/(.+)$') {
        $o.User = $Matches[1]; $o.Host = $Matches[2]
        $o.Port = $Matches[3]; $o.Service = $Matches[4]
    }
    # host:port/service
    elseif ($Raw -match '^([^:/@]+):(\d+)/(.+)$') {
        $o.Host = $Matches[1]; $o.Port = $Matches[2]; $o.Service = $Matches[3]
    }
    # host/service
    elseif ($Raw -match '^([^:/@]+)/(.+)$') {
        $o.Host = $Matches[1]; $o.Service = $Matches[2]
    }
    else {
        Write-Yellow "  ⚠  Could not fully parse Oracle DSN '$Raw' — will prompt for missing values"
    }
    return $o
}

# ── PostgreSQL DSN parser ──────────────────────────────────────────────────────
function Parse-PgDsn {
    param([string]$Raw)
    $p = @{
        User   = ""
        Pass   = ""
        Host   = "localhost"
        Port   = "5432"
        Db     = ""
        Schema = "public"
    }
    if ([string]::IsNullOrWhiteSpace($Raw) -or
        $Raw -eq "postgres" -or $Raw -eq "postgresql") { return $p }

    # postgresql://user:pass@host:port/dbname
    if ($Raw -match '^postgresql://([^:@]+):([^@]*)@([^:]+):(\d+)/(.+)$') {
        $p.User = $Matches[1]; $p.Pass = $Matches[2]
        $p.Host = $Matches[3]; $p.Port = $Matches[4]; $p.Db = $Matches[5]
    }
    # postgresql://user@host:port/dbname
    elseif ($Raw -match '^postgresql://([^:@]+)@([^:]+):(\d+)/(.+)$') {
        $p.User = $Matches[1]; $p.Host = $Matches[2]
        $p.Port = $Matches[3]; $p.Db   = $Matches[4]
    }
    # postgresql://host/dbname
    elseif ($Raw -match '^postgresql://([^:/]+)/(.+)$') {
        $p.Host = $Matches[1]; $p.Db = $Matches[2]
    }
    # user@host:port/dbname
    elseif ($Raw -match '^([^@]+)@([^:]+):(\d+)/(.+)$') {
        $p.User = $Matches[1]; $p.Host = $Matches[2]
        $p.Port = $Matches[3]; $p.Db   = $Matches[4]
    }
    # plain dbname
    elseif ($Raw -match '^[a-zA-Z0-9_]+$') {
        $p.Db = $Raw
    }
    else {
        Write-Yellow "  ⚠  Could not fully parse PostgreSQL DSN '$Raw' — will prompt for missing values"
    }
    return $p
}

# ── MAIN ──────────────────────────────────────────────────────────────────────
Show-Banner

$ora = Parse-OracleDsn $From
$pg  = Parse-PgDsn     $To

# ── Oracle prompts ────────────────────────────────────────────────────────────
Show-Section "Oracle source"

if ([string]::IsNullOrEmpty($ora.Host))    { $ora.Host    = Ask-Value "Host"                 "localhost" }
if ([string]::IsNullOrEmpty($ora.Port))    { $ora.Port    = Ask-Value "Port"                 "1521"      }
if ([string]::IsNullOrEmpty($ora.Service) -and [string]::IsNullOrEmpty($ora.Sid)) {
    $svcOrSid = Ask-Value "Service name (or press Enter to use SID instead)" ""
    if ([string]::IsNullOrEmpty($svcOrSid)) {
        $ora.Sid = Ask-Value "SID" "ORCL"
    } else {
        $ora.Service = $svcOrSid
    }
}
if ([string]::IsNullOrEmpty($ora.User))    { $ora.User    = Ask-Value "Username"              ""          }
if ([string]::IsNullOrEmpty($ora.Pass))    { $ora.Pass    = Ask-Secret "Password"                         }

$defaultSchema = $ora.User.ToUpper()
$oraSchema = Ask-Value "Schema to migrate" $defaultSchema
$thickMode = Ask-YesNo "Use thick mode? (requires Oracle Instant Client)" $false

# ── PostgreSQL prompts ────────────────────────────────────────────────────────
Show-Section "PostgreSQL target"

if ([string]::IsNullOrEmpty($pg.Host))   { $pg.Host   = Ask-Value "Host"            "localhost" }
if ([string]::IsNullOrEmpty($pg.Port))   { $pg.Port   = Ask-Value "Port"            "5432"      }
if ([string]::IsNullOrEmpty($pg.Db))     { $pg.Db     = Ask-Value "Database name"   ""          }
if ([string]::IsNullOrEmpty($pg.User))   { $pg.User   = Ask-Value "Username"        "postgres"  }
if ([string]::IsNullOrEmpty($pg.Pass))   { $pg.Pass   = Ask-Secret "Password"                   }
$pg.Schema = Ask-Value "Target schema" $pg.Schema

# ── Migration options ─────────────────────────────────────────────────────────
Show-Section "Migration options"

$skipData      = Ask-YesNo "Skip data migration (schema only)?"             $false
$skipFunctions = Ask-YesNo "Skip PL/SQL functions and procedures?"          $false
$skipTriggers  = Ask-YesNo "Skip triggers?"                                 $false
$skipViews     = Ask-YesNo "Skip views?"                                    $false
$useIdentity   = Ask-YesNo "Use IDENTITY columns instead of sequences?"     $false
$dryRun        = Ask-YesNo "Dry run (print DDL, no writes)?"               $false
$workers       = Ask-Value "Parallel worker threads"                         "4"
$batchSize     = Ask-Value "Batch size (rows)"                              "10000"
$tables        = Ask-Value "Tables to migrate (comma-separated, blank=all)" ""

# ── Summary ───────────────────────────────────────────────────────────────────
Show-Section "Summary"

$connName = if ($ora.Service) { $ora.Service } else { $ora.Sid }
Write-Host "  Oracle     $($ora.User)@$($ora.Host):$($ora.Port)/$connName" -ForegroundColor White
Write-Host "  Schema     $oraSchema"                                        -ForegroundColor White
Write-Host "  -> PG      $($pg.User)@$($pg.Host):$($pg.Port)/$($pg.Db) (schema: $($pg.Schema))" -ForegroundColor White
$dataLabel = if ($skipData) { "skipped" } else { "$workers workers · $batchSize rows/batch" }
Write-Host "  Data       $dataLabel"  -ForegroundColor White
Write-Host "  Functions  $(if ($skipFunctions) { 'skipped' } else { 'yes' })"  -ForegroundColor White
Write-Host "  Triggers   $(if ($skipTriggers)  { 'skipped' } else { 'yes' })"  -ForegroundColor White
Write-Host "  Views      $(if ($skipViews)     { 'skipped' } else { 'yes' })"  -ForegroundColor White
if ($tables) { Write-Host "  Tables     $tables" -ForegroundColor White }
if ($dryRun) { Write-Yellow "  DRY RUN — no writes" }
Write-Host ""

$go = Ask-YesNo "Proceed with migration?" $true
if (-not $go) { Write-Host "Aborted." -ForegroundColor Red; exit 0 }

# ── Build command ─────────────────────────────────────────────────────────────
$pgDsn = "postgresql://$($pg.User):$($pg.Pass)@$($pg.Host):$($pg.Port)/$($pg.Db)"

$args_list = @(
    "migrate.py",
    "--oracle-user",     $ora.User,
    "--oracle-password", $ora.Pass,
    "--oracle-host",     $ora.Host,
    "--oracle-port",     $ora.Port,
    "--oracle-schema",   $oraSchema,
    "--pg-dsn",          $pgDsn,
    "--pg-schema",       $pg.Schema,
    "--workers",         $workers,
    "--batch-size",      $batchSize
)

if ($ora.Service)  { $args_list += @("--oracle-service", $ora.Service) }
if ($ora.Sid)      { $args_list += @("--oracle-sid",     $ora.Sid) }
if ($thickMode)    { $args_list += "--oracle-thick" }
if ($skipData)     { $args_list += "--skip-data" }
if ($skipFunctions){ $args_list += "--skip-functions" }
if ($skipTriggers) { $args_list += "--skip-triggers" }
if ($skipViews)    { $args_list += "--skip-views" }
if ($useIdentity)  { $args_list += "--use-identity" }
if ($dryRun)       { $args_list += "--dry-run" }
if ($tables)       { $args_list += @("--tables", $tables) }

# ── Run ───────────────────────────────────────────────────────────────────────
Show-Section "Running mama"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

& python @args_list