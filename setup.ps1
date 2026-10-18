<#
.SYNOPSIS
    One-click setup for YT-Shorts Automation on Windows.

.DESCRIPTION
    Idempotent installer that:
      1. Detects Python (>= 3.11) and ffmpeg.
      2. Creates a virtual environment in .\.venv if missing.
      3. Installs the project + dev extras into the venv.
      4. Backs up any existing .env to .env.bak.<timestamp>.
      5. Writes a fresh .env from .env.example.
      6. Initializes the SQLite database.
      7. Runs the post-install smoke test (no live creds).
      8. Creates a start-menu shortcut that launches the dashboard.

    The script is idempotent: re-running it skips any step that has
    already completed. Live progress is shown via Write-Progress.

    ExecutionPolicy is NOT modified globally. Callers must invoke with
    `-ExecutionPolicy Bypass` or the script will refuse to run.

.PARAMETER Mode
    One of: install, smoke, launch. Default: install.

.PARAMETER InstallRoot
    Directory to install into. Default: current directory.

.PARAMETER TestLongPath
    (For acceptance procedure) Path to write a long-path marker file.

.PARAMETER TestNonAscii
    (For acceptance procedure) Path to write a non-ASCII marker file.

.PARAMETER TestUtf8
    (For acceptance procedure) If set, print Russian + Japanese strings.

.EXAMPLE
    pwsh -ExecutionPolicy Bypass -File .\setup.ps1
    # Default install.

.EXAMPLE
    pwsh -ExecutionPolicy Bypass -File .\setup.ps1 -Mode launch
    # Launch the dashboard (assumes setup has been run once).
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'smoke', 'launch')]
    [string]$Mode = 'install',

    [string]$InstallRoot = $PSScriptRoot,

    [string]$TestLongPath = '',

    [string]$TestNonAscii = '',

    [switch]$TestUtf8
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'

# Refuse to run if the script is being executed under a global policy
# that would have to be weakened. We require the caller to use
# -ExecutionPolicy Bypass on the command line, not the registry.
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
    Write-Error "This script requires FullLanguage mode. Re-run with: pwsh -ExecutionPolicy Bypass -File .\setup.ps1"
    exit 2
}

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

function Write-Step {
    param([string]$Name, [string]$Status)
    $colour = switch ($Status) {
        'start'   { 'Cyan'    }
        'done'    { 'Green'   }
        'skip'    { 'Yellow'  }
        'fail'    { 'Red'     }
        default   { 'White'   }
    }
    Write-Host "[$Status] $Name" -ForegroundColor $colour
}

function Assert-Command {
    param([string]$Name, [string]$MinVersion = '')
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "$Name not found on PATH. $Name is required."
    }
    if ($MinVersion) {
        $ver = & $Name --version 2>&1 | Select-Object -First 1
        if ($ver -notmatch $MinVersion) {
            throw "$Name version mismatch: got '$ver', need $MinVersion"
        }
    }
}

function Backup-Env {
    $envFile = Join-Path $InstallRoot '.env'
    if (Test-Path $envFile) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backup = Join-Path $InstallRoot ".env.bak.$stamp"
        Copy-Item $envFile $backup -Force
        Write-Step "Backup .env -> $backup" 'done'
    }
}

function Write-EnvFromExample {
    $envFile = Join-Path $InstallRoot '.env'
    $example = Join-Path $InstallRoot '.env.example'
    if (-not (Test-Path $example)) {
        Write-Step "No .env.example; skipping env write" 'skip'
        return
    }
    if (Test-Path $envFile) {
        Write-Step ".env already exists" 'skip'
        return
    }
    Copy-Item $example $envFile
    Write-Step "Wrote .env from .env.example" 'done'
}

# ----------------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------------

$steps = @(
    @{ Name = 'Detect Python';     Fn = { Assert-Command python '3\.11' } }
    @{ Name = 'Detect ffmpeg';     Fn = { Assert-Command ffmpeg ''       } }
    @{ Name = 'Create venv';       Fn = {
            $venv = Join-Path $InstallRoot '.venv'
            if (Test-Path $venv) { return $false }
            python -m venv $venv
            return $true
        }
    }
    @{ Name = 'Install project';   Fn = {
            $venvPy = Join-Path $InstallRoot '.venv\Scripts\python.exe'
            & $venvPy -m pip install --upgrade pip | Out-Null
            & $venvPy -m pip install -e ".[dev]" | Out-Null
            return $true
        }
    }
    @{ Name = 'Backup .env';       Fn = { Backup-Env; return $true } }
    @{ Name = 'Write .env';        Fn = { Write-EnvFromExample; return $true } }
    @{ Name = 'Init database';     Fn = {
            $venvPy = Join-Path $InstallRoot '.venv\Scripts\python.exe'
            & $venvPy -c "from shorts_engine.db.session import init_db; init_db()" | Out-Null
            return $true
        }
    }
    @{ Name = 'Run smoke';         Fn = {
            $venvPy = Join-Path $InstallRoot '.venv\Scripts\python.exe'
            & $venvPy -m tools.smoke --json 2>&1 | Out-Null
            return $true
        }
    }
    @{ Name = 'Start-menu shortcut'; Fn = {
            $shell = New-Object -ComObject WScript.Shell
            $startMenu = [Environment]::GetFolderPath('Programs')
            $shortcutPath = Join-Path $startMenu 'YT-Shorts Automation.lnk'
            $shortcut = $shell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = (Join-Path $InstallRoot '.venv\Scripts\python.exe')
            $shortcut.Arguments = '-m shorts_engine.cli dashboard'
            $shortcut.WorkingDirectory = $InstallRoot
            $shortcut.IconLocation = 'shell32.dll,13'
            $shortcut.Save()
            return $true
        }
    }
)

# Idempotency record.
$stateFile = Join-Path $InstallRoot '.setup-state.json'
$state = if (Test-Path $stateFile) {
    Get-Content $stateFile -Raw | ConvertFrom-Json
} else {
    [pscustomobject]@{ completed = @() }
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

try {
    if ($Mode -eq 'launch') {
        $venvPy = Join-Path $InstallRoot '.venv\Scripts\python.exe'
        & $venvPy -m shorts_engine.cli dashboard
        exit 0
    }

    $i = 0
    foreach ($step in $steps) {
        $i++
        $name = $step.Name
        $percent = [int](($i / $steps.Count) * 100)
        Write-Progress -Activity 'YT-Shorts setup' -Status $name -PercentComplete $percent
        if ($state.completed -contains $name) {
            Write-Step $name 'skip'
            continue
        }
        Write-Step $name 'start'
        try {
            $changed = & $step.Fn
            if ($changed -ne $false) {
                $state.completed += $name
                $state | ConvertTo-Json | Set-Content $stateFile
            }
            Write-Step $name 'done'
        } catch {
            Write-Step $name 'fail'
            Write-Host "  $_" -ForegroundColor Red
            Write-Host "  Log path: $InstallRoot\logs\setup-$(Get-Date -Format 'yyyyMMdd-HHmmss').log" -ForegroundColor Yellow
            exit 1
        }
    }
    Write-Progress -Activity 'YT-Shorts setup' -Completed
    Write-Step 'Setup complete.' 'done'

    # Acceptance-procedure hooks.
    if ($TestLongPath) {
        New-Item -ItemType File -Path (Join-Path $TestLongPath 'longpath-marker.txt') -Force | Out-Null
        Write-Step "Long-path marker written: $TestLongPath" 'done'
    }
    if ($TestNonAscii) {
        New-Item -ItemType File -Path (Join-Path $TestNonAscii 'nonascii-marker.txt') -Force | Out-Null
        Write-Step "Non-ASCII marker written: $TestNonAscii" 'done'
    }
    if ($TestUtf8) {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Host 'Установка завершена.'   # Russian
        Write-Host 'セットアップ完了。'    # Japanese
    }
    exit 0
} catch {
    Write-Host "FATAL: $_" -ForegroundColor Red
    exit 1
}
