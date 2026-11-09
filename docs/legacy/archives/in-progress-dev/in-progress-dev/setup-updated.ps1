#Requires -Version 5.1
<#
.SYNOPSIS
    YouTube Shorts Automation Engine - industrial-grade Windows setup (v3.2)

.DESCRIPTION
    Idempotent, self-healing installer. Safe to re-run at any time.
      * Detects / installs Python (python.org -> winget -> NuGet fallback chain)
      * Installs the Microsoft Visual C++ runtime that PyTorch / CTranslate2 need
      * Installs FFmpeg (multiple mirrors, verified, local to the project)
      * Builds the .venv, installs dependencies with retries + per-package fallback
      * Repairs insecure placeholder secrets in .env (with backup)
      * Initialises the database, verifies every import, and self-heals failures
      * Starts the real dashboard once as a smoke test
      * Shows live progress for everything (spinner, elapsed time, network speed)

.PARAMETER GPU            Force CUDA build of PyTorch even if no NVIDIA GPU is detected.
.PARAMETER CudaIndex      Override CUDA wheel index tag, e.g. cu126.
.PARAMETER SkipTests      Skip import verification and the server smoke test.
.PARAMETER NoServer       Skip the live server start test (import-only smoke test instead).
.PARAMETER NoBrowser      Do not open the browser when offering to start the dashboard.
.PARAMETER NonInteractive Never prompt; use safe defaults (for CI / unattended installs).
.PARAMETER Force          Re-run dependency installation even if nothing changed.
.PARAMETER ForceVenv      Delete and recreate .venv.
.PARAMETER Repair         Shortcut for -Force -ForceVenv (full clean reinstall of the Python environment).
.PARAMETER VerifyOnly     Health check only: reports problems, installs/changes nothing.
.PARAMETER RotateSecrets  Replace template-default secrets in an existing .env with fresh random ones (backup kept).
.PARAMETER PipIndexUrl    Custom PyPI index/mirror URL (e.g. a corporate mirror).
.PARAMETER Proxy          HTTP(S) proxy URL, e.g. http://proxy.corp:8080

.NOTES
    Exit codes: 0 = success, 1 = fatal error (setup aborted), 2 = finished with errors.
    Full log  : storage\logs\setup.log
#>

[CmdletBinding()]
param(
    [switch]$GPU,
    [string]$CudaIndex = '',
    [switch]$SkipTests,
    [switch]$NoBrowser,
    [switch]$NoServer,
    [switch]$NonInteractive,
    [switch]$Force,
    [switch]$ForceVenv,
    [switch]$Repair,
    [switch]$VerifyOnly,
    [switch]$RotateSecrets,
    [string]$PipIndexUrl = '',
    [string]$Proxy = ''
)

if ($Repair) { $Force = [switch]$true; $ForceVenv = [switch]$true }

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# =============================================================================
#  CONSTANTS
# =============================================================================
$SCRIPT_VERSION  = '3.2'
$TOTAL_STEPS     = 13
$PY_MIN          = [version]'3.11'      # minimum supported
$PY_MAX_MINOR    = 12                   # numpy<2 has no wheels for 3.13+
$PY_PREFERRED    = 11
$PY_INSTALL_VERS = @('3.11.6', '3.11.9')
$VC_MIN_VERSION  = [version]'14.36'
$VC_REDIST_URLS  = @('https://aka.ms/vs/17/release/vc_redist.x64.exe',
                     'https://download.visualstudio.microsoft.com/download/pr/vc_redist.x64.exe')
$MIN_FREE_GB     = 6

$PROJECT_ROOT = $PSScriptRoot
if (-not $PROJECT_ROOT) { $PROJECT_ROOT = (Get-Location).Path }

$VENV_DIR      = Join-Path $PROJECT_ROOT '.venv'
$VENV_PYTHON   = Join-Path $VENV_DIR 'Scripts\python.exe'
$REQ_FILE      = Join-Path $PROJECT_ROOT 'requirements.txt'
$ENV_FILE      = Join-Path $PROJECT_ROOT '.env'
$PYPROJECT     = Join-Path $PROJECT_ROOT 'pyproject.toml'
$SETUP_ENV_PY  = Join-Path $PROJECT_ROOT 'setup_environment.py'
$RUN_PY        = Join-Path $PROJECT_ROOT 'run.py'
$FFMPEG_BIN    = Join-Path $PROJECT_ROOT 'tools\ffmpeg\bin'
$RUNTIME_CFG   = Join-Path $PROJECT_ROOT 'storage\config\runtime_settings.json'
$DL_DIR        = Join-Path $PROJECT_ROOT 'tools\_downloads'
$TMP_DIR       = Join-Path $PROJECT_ROOT 'tools\_tmp'
$LOCAL_PY_DIR  = Join-Path $PROJECT_ROOT 'tools\python'
$LAUNCH_DIR    = Join-Path $PROJECT_ROOT 'tools\launchers'
$DEPS_MARKER   = Join-Path $VENV_DIR '.setup_deps.sha256'

$script:LOG_DIR  = Join-Path $PROJECT_ROOT 'storage\logs'
$script:LOG_FILE = Join-Path $script:LOG_DIR 'setup.log'

# Placeholder secrets shipped in .env.example (must never reach production)
$PLACEHOLDER_SECRETS = @(
    'yt_shorts_dev_master_secret_key_32_bytes_min!',
    'yt_shorts_dev_dashboard_session_secret_32_bytes!',
    'yt_shorts_kdf_salt_2026_secure!',
    'yt_shorts_local_master_secret_key_32_bytes!',
    'yt_shorts_local_session_secret_key_32_bytes!'
)

# =============================================================================
#  SCRIPT STATE
# =============================================================================
$script:Warnings     = New-Object System.Collections.ArrayList
$script:Errors       = New-Object System.Collections.ArrayList
$script:StepResults  = New-Object System.Collections.ArrayList
$script:CurrentStep  = $null
$script:StepNum      = 0
$script:PythonExe    = $null
$script:PythonVer    = $null
$script:T0           = Get-Date
$script:LogReady     = $false
$script:StatusActive = $false
$script:IsRedirected = $false
$script:Interactive  = $true
$script:IsAdmin      = $false
$script:Online       = $true
$script:RebootNeeded = $false
$script:VenvOk       = $false
$script:DashPort     = 8000
$script:TestPort     = 8000
$script:SpinIdx      = 0
$script:GpuTorch     = $false
$script:PipStats     = @{ Collected = 0 }
$script:Utf8NoBom    = New-Object System.Text.UTF8Encoding($false)
$script:ApiKeyStatus = @()
$script:NoInstall    = [bool]$VerifyOnly
$script:VcTried      = $false
$script:EnvBackedUp  = $false
$script:PipExtra     = @()
$script:ConsoleModeOrig = $null
$script:GuardsOn     = $false

try { $script:IsRedirected = [Console]::IsOutputRedirected } catch { $script:IsRedirected = $false }
$script:Interactive = -not $NonInteractive
try { if ([Console]::IsInputRedirected) { $script:Interactive = $false } } catch { }

# =============================================================================
#  FORMAT HELPERS
# =============================================================================
function Format-Elapsed {
    param([TimeSpan]$Span)
    return ('{0:00}:{1:00}' -f [int][Math]::Floor($Span.TotalMinutes), $Span.Seconds)
}

function Format-Size {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
    return ('{0:N0} B' -f $Bytes)
}

function Format-Rate {
    param([double]$BytesPerSec)
    return ((Format-Size $BytesPerSec) + '/s')
}

function New-Bar {
    param([double]$Fraction, [int]$Width = 20)
    if ($Fraction -lt 0) { $Fraction = 0 }
    if ($Fraction -gt 1) { $Fraction = 1 }
    $f = [int][Math]::Floor($Fraction * $Width)
    return ('#' * $f) + ('-' * ($Width - $f))
}

function Get-ConsoleWidth {
    try { return [Math]::Max(40, $Host.UI.RawUI.WindowSize.Width - 1) } catch { return 100 }
}

function Limit-Text {
    param([string]$Text, [int]$Max)
    if ($Text.Length -le $Max) { return $Text }
    return $Text.Substring(0, [Math]::Max(1, $Max - 3)) + '...'
}

# =============================================================================
#  LOGGING & CONSOLE OUTPUT
# =============================================================================
function Protect-Secrets {
    <# Masks API keys / secrets in anything that reaches the screen or the log. #>
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $t = $Text
    $t = [regex]::Replace($t, 'gsk_[A-Za-z0-9]{16,}', 'gsk_***REDACTED***')
    $t = [regex]::Replace($t, 'sk-or-v1-[A-Za-z0-9]{16,}', 'sk-or-v1-***REDACTED***')
    $t = [regex]::Replace($t, 'sk-[A-Za-z0-9_\-]{24,}', 'sk-***REDACTED***')
    $t = [regex]::Replace($t, 'AIza[0-9A-Za-z_\-]{20,}', 'AIza***REDACTED***')
    $t = [regex]::Replace($t, 'AQ\.[A-Za-z0-9_\-]{20,}', 'AQ.***REDACTED***')
    $t = [regex]::Replace($t, '(?i)((?:api[_-]?keys?|secret|token|password|passwd|encryption[_-]?key|salt)[A-Za-z_]*["'']?\s*[=:]\s*["'']?\[?["'']?)([^\s"'',\]]{6,})', '${1}***REDACTED***')
    return $t
}

function Write-Log {
    param([string]$Message)
    if (-not $script:LogReady) { return }
    $Message = Protect-Secrets $Message
    try {
        $ts = Get-Date -Format 'HH:mm:ss'
        [System.IO.File]::AppendAllText($script:LOG_FILE, "[$ts] $Message`r`n", $script:Utf8NoBom)
    } catch { }
}

function Clear-StatusLine {
    if ($script:StatusActive) {
        try { [Console]::Write("`r" + (' ' * (Get-ConsoleWidth)) + "`r") } catch { }
        $script:StatusActive = $false
    }
}

function Show-StatusLine {
    param([string]$Text)
    if ($script:IsRedirected) { return }
    $w = Get-ConsoleWidth
    $t = Limit-Text $Text ($w - 1)
    try { [Console]::Write("`r" + $t.PadRight($w - 1)) } catch { return }
    $script:StatusActive = $true
}

function Set-WindowTitleSafe {
    param([string]$Text)
    try { $Host.UI.RawUI.WindowTitle = $Text } catch { }
}

function Out-Line {
    param([string]$Text, [string]$Color = 'Gray', [switch]$NoLog)
    $Text = Protect-Secrets $Text
    Clear-StatusLine
    Write-Host $Text -ForegroundColor $Color
    if (-not $NoLog) { Write-Log $Text }
}

function Write-OK   { param([string]$M) Out-Line "  [OK]   $M" 'Green' }
function Write-Info { param([string]$M) Out-Line "         $M" 'Gray' }
function Write-Dim  { param([string]$M) Out-Line "         $M" 'DarkGray' }
function Write-Warn {
    param([string]$M)
    Out-Line "  [WARN] $M" 'Yellow'
    [void]$script:Warnings.Add($M)
}
function Write-Err {
    param([string]$M)
    Out-Line "  [ERR]  $M" 'Red'
    [void]$script:Errors.Add($M)
}
function Write-Hint {
    param([string]$M)
    Out-Line "  [HINT] $M" 'Cyan'
}

function Write-Fatal {
    param([string]$Message, [string]$Hint = '')
    Out-Line ''
    Out-Line "  [FATAL] $Message" 'Red'
    if ($Hint) { Out-Line "  [HINT]  $Hint" 'Yellow' }
    [void]$script:Errors.Add($Message)
    throw "SETUP_FATAL::$Message"
}

function Initialize-Logging {
    try {
        if (-not (Test-Path -LiteralPath $script:LOG_DIR)) {
            New-Item -ItemType Directory -Path $script:LOG_DIR -Force | Out-Null
        }
    } catch {
        $script:LOG_DIR  = Join-Path $env:TEMP 'yt-shorts-setup'
        $script:LOG_FILE = Join-Path $script:LOG_DIR 'setup.log'
        New-Item -ItemType Directory -Path $script:LOG_DIR -Force | Out-Null
    }
    try {
        if (Test-Path -LiteralPath $script:LOG_FILE) {
            $stamp = (Get-Item -LiteralPath $script:LOG_FILE).LastWriteTime.ToString('yyyyMMdd-HHmmss')
            Move-Item -LiteralPath $script:LOG_FILE -Destination (Join-Path $script:LOG_DIR "setup.$stamp.log") -Force
        }
        $old = @(Get-ChildItem -LiteralPath $script:LOG_DIR -Filter 'setup.*.log' -File -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -Skip 5)
        foreach ($f in $old) { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue }
    } catch { }

    $header = @"
================================================================================
  YouTube Shorts Automation Engine - Setup Log
  Script Version : $SCRIPT_VERSION
  Started        : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
  Project Root   : $PROJECT_ROOT
  Parameters     : GPU=$GPU CudaIndex=$CudaIndex SkipTests=$SkipTests NoBrowser=$NoBrowser NoServer=$NoServer NonInteractive=$NonInteractive Force=$Force ForceVenv=$ForceVenv VerifyOnly=$VerifyOnly PipIndexUrl=$PipIndexUrl ProxySet=$([bool]$Proxy)
  PowerShell     : $($PSVersionTable.PSVersion)
================================================================================
"@
    try {
        [System.IO.File]::WriteAllText($script:LOG_FILE, $header + "`r`n", $script:Utf8NoBom)
        $script:LogReady = $true
    } catch {
        $script:LogReady = $false
    }
}

function Show-Banner {
    try { Clear-Host } catch { }
    Out-Line '' 'Cyan' -NoLog
    Out-Line ('=' * 78) 'Cyan' -NoLog
    Out-Line "      YouTube Shorts Automation Engine  -  Complete Setup  (v$SCRIPT_VERSION)" 'Cyan' -NoLog
    Out-Line ('=' * 78) 'Cyan' -NoLog
    Out-Line "  Project  : $PROJECT_ROOT" 'Gray' -NoLog
    Out-Line "  Log file : $($script:LOG_FILE)" 'Gray' -NoLog
    Out-Line '  Safe to re-run at any time. Live progress is shown for every operation.' 'Gray' -NoLog
    if ($GPU)            { Out-Line '  Mode     : CUDA/GPU build of PyTorch forced' 'Yellow' -NoLog }
    if ($NonInteractive) { Out-Line '  Mode     : non-interactive (no prompts)' 'Yellow' -NoLog }
    if ($SkipTests)      { Out-Line '  Tests    : skipped' 'Yellow' -NoLog }
    if ($VerifyOnly)     { Out-Line '  Mode     : VERIFY ONLY - nothing will be installed or modified' 'Yellow' -NoLog }
    if ($Repair)         { Out-Line '  Mode     : REPAIR - clean reinstall of the Python environment' 'Yellow' -NoLog }
    Out-Line ('=' * 78) 'Cyan' -NoLog
    Out-Line '  Plan: 1 Pre-flight > 2 Python > 3 C++ runtime > 4 FFmpeg > 5 venv > 6 packages' 'DarkGray' -NoLog
    Out-Line '        > 7 .env > 8 folders > 9 database > 10 port > 11 verify+repair > 12 live test' 'DarkGray' -NoLog
    Out-Line '  First run: usually 10-40 min (mostly downloads). Re-runs: about 1 minute.' 'DarkGray' -NoLog
    Out-Line '  You may minimise this window - the title bar / taskbar shows live progress.' 'DarkGray' -NoLog
    Out-Line ('=' * 78) 'Cyan' -NoLog
}

# =============================================================================
#  STEP TRACKING
# =============================================================================
function Complete-Step {
    if ($null -eq $script:CurrentStep) { return }
    $s = $script:CurrentStep
    $dur = (Get-Date) - $s.Start
    $status = 'OK'
    if ($script:Warnings.Count -gt $s.W) { $status = 'WARN' }
    if ($script:Errors.Count   -gt $s.E) { $status = 'FAIL' }
    [void]$script:StepResults.Add([pscustomobject]@{ Num = $s.Num; Name = $s.Name; Status = $status; Seconds = [int]$dur.TotalSeconds })
    $col = 'Green'; if ($status -eq 'WARN') { $col = 'Yellow' }; if ($status -eq 'FAIL') { $col = 'Red' }
    Out-Line ("         -> step finished: {0} in {1}" -f $status, (Format-Elapsed $dur)) $col
    $script:CurrentStep = $null
}

function Write-Step {
    param([string]$Title)
    Complete-Step
    $script:StepNum++
    $n = $script:StepNum
    $frac = ($n - 1) / [double]$TOTAL_STEPS
    Out-Line '' 'Gray' -NoLog
    Out-Line ("[{0,2}/{1}] {2}" -f $n, $TOTAL_STEPS, $Title) 'Cyan'
    Out-Line ("        [{0}] {1,3}% overall   total elapsed {2}" -f (New-Bar $frac), [int]($frac * 100), (Format-Elapsed ((Get-Date) - $script:T0))) 'DarkGray' -NoLog
    Out-Line ('-' * 70) 'DarkGray' -NoLog
    $script:CurrentStep = @{ Name = $Title; Start = Get-Date; W = $script:Warnings.Count; E = $script:Errors.Count; Num = $n }
    Set-WindowTitleSafe ("Shorts Engine Setup [{0}/{1}] {2}" -f $n, $TOTAL_STEPS, $Title)
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action, [switch]$Critical)
    Write-Step $Name
    try {
        & $Action
    } catch {
        $m = $_.Exception.Message
        if ($m -like 'SETUP_FATAL::*') { throw }
        Write-Err "Unexpected error in step '$Name': $m"
        Write-Log ($_ | Out-String)
        Write-Log $_.ScriptStackTrace
        if ($Critical) { throw "SETUP_FATAL::$m" }
    }
}

# =============================================================================
#  INTERACTION
# =============================================================================
function Read-YesNo {
    param([string]$Prompt, [bool]$Default = $false, [int]$TimeoutSec = 60)
    $defTxt = 'no'; $hint = '[y/N]'
    if ($Default) { $defTxt = 'yes'; $hint = '[Y/n]' }
    if (-not $script:Interactive) {
        Write-Info "$Prompt -> auto-answer '$defTxt' (non-interactive)"
        return $Default
    }
    Clear-StatusLine
    Write-Host ("  ? {0} {1} (auto-'{2}' in {3}s): " -f $Prompt, $hint, $defTxt, $TimeoutSec) -NoNewline -ForegroundColor White
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
            if ([Console]::KeyAvailable) {
                $k = [Console]::ReadKey($true)
                if ($k.Key -eq 'Enter') { Write-Host $defTxt; return $Default }
                if ($k.KeyChar -eq 'y' -or $k.KeyChar -eq 'Y') { Write-Host 'yes'; Write-Log "$Prompt -> yes"; return $true }
                if ($k.KeyChar -eq 'n' -or $k.KeyChar -eq 'N') { Write-Host 'no';  Write-Log "$Prompt -> no";  return $false }
            }
            Start-Sleep -Milliseconds 100
        }
        Write-Host $defTxt
        return $Default
    } catch {
        $a = Read-Host
        if ($a -match '^[Yy]') { return $true }
        if ($a -match '^[Nn]') { return $false }
        return $Default
    }
}

# =============================================================================
#  NATIVE PROCESS ENGINE (live output, heartbeat, timeout, no stderr->exception)
# =============================================================================
function ConvertTo-QuotedArg {
    param([AllowEmptyString()][string]$Arg)
    if ($Arg.Length -gt 0 -and $Arg -notmatch '[\s"]') { return $Arg }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $bs = 0
    foreach ($ch in $Arg.ToCharArray()) {
        if ($ch -eq [char]'\') {
            $bs++
        } elseif ($ch -eq [char]'"') {
            [void]$sb.Append([char]'\', ($bs * 2 + 1)); [void]$sb.Append([char]'"'); $bs = 0
        } else {
            if ($bs -gt 0) { [void]$sb.Append([char]'\', $bs); $bs = 0 }
            [void]$sb.Append($ch)
        }
    }
    if ($bs -gt 0) { [void]$sb.Append([char]'\', ($bs * 2)) }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    try {
        if ($env:OS -eq 'Windows_NT') {
            $k = Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID', "$ProcessId", '/T', '/F') -WindowStyle Hidden -Wait -PassThru
        } else {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
        try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Get-NetRxBytes {
    try {
        $t = 0L
        foreach ($n in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
            if ($n.OperationalStatus -eq 'Up' -and $n.NetworkInterfaceType -ne 'Loopback') {
                $t += $n.GetIPv4Statistics().BytesReceived
            }
        }
        return $t
    } catch { return 0L }
}

function Invoke-Native {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgList = @(),
        [string]$Label = '',
        [switch]$Live,
        [scriptblock]$LineFilter,
        [int]$TimeoutSec = 1800,
        [int]$StallSec = 0,
        [hashtable]$Env = @{},
        [string]$WorkDir = '',
        [string]$WatchFile = '',
        [switch]$ShowNet,
        [switch]$Quiet,
        [scriptblock]$Probe
    )
    if (-not $WorkDir) { $WorkDir = $PROJECT_ROOT }
    if (-not $Label)   { $Label = [IO.Path]::GetFileName($FilePath) }
    $lines  = New-Object System.Collections.ArrayList
    $result = [pscustomobject]@{ ExitCode = -1; Output = @(); TimedOut = $false; Stalled = $false; Started = $false; ProbeOk = $false; Seconds = 0.0 }
    $sw = [Diagnostics.Stopwatch]::StartNew()

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $FilePath
    $psi.Arguments              = (@($ArgList | ForEach-Object { ConvertTo-QuotedArg ([string]$_) }) -join ' ')
    $psi.WorkingDirectory       = $WorkDir
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.RedirectStandardInput  = $true
    $psi.CreateNoWindow         = $true
    try { $psi.StandardOutputEncoding = [Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [Text.Encoding]::UTF8 } catch { }
    # Environment variables that silently break Python / pip inside a venv
    foreach ($v in @('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PIP_USER', 'PIP_TARGET', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__', 'PIP_REQUIRE_VIRTUALENV')) {
        if ($psi.EnvironmentVariables.ContainsKey($v)) { $psi.EnvironmentVariables.Remove($v) }
    }
    $psi.EnvironmentVariables['PYTHONUNBUFFERED']              = '1'
    $psi.EnvironmentVariables['PYTHONIOENCODING']              = 'utf-8'
    $psi.EnvironmentVariables['PYTHONUTF8']                    = '1'
    $psi.EnvironmentVariables['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    $psi.EnvironmentVariables['PIP_NO_INPUT']                  = '1'
    $psi.EnvironmentVariables['PIP_NO_COLOR']                  = '1'
    foreach ($k in $Env.Keys) { $psi.EnvironmentVariables[[string]$k] = [string]$Env[$k] }

    Write-Log ">> $FilePath $($psi.Arguments)"
    $p = $null
    try {
        try {
            $p = [Diagnostics.Process]::Start($psi)
        } catch {
            [void]$lines.Add("Failed to start process: $($_.Exception.Message)")
            Write-Log "   start failed: $($_.Exception.Message)"
            $result.Output = @($lines)
            return $result
        }
        $result.Started = $true
        try { $p.StandardInput.Close() } catch { }

        $streams = @(
            @{ R = $p.StandardOutput; T = $p.StandardOutput.ReadLineAsync(); Done = $false },
            @{ R = $p.StandardError;  T = $p.StandardError.ReadLineAsync();  Done = $false }
        )
        $spin          = @('|', '/', '-', '\')
        $lastAct       = [DateTime]::UtcNow
        $lastBeat      = [DateTime]::UtcNow.AddSeconds(-5)
        $lastPlain     = [DateTime]::UtcNow
        $lastProbe     = [DateTime]::UtcNow
        $lastNetT      = [DateTime]::UtcNow
        $lastNetActive = [DateTime]::UtcNow
        $lastNetB      = 0L
        $netRate       = 0.0
        $sampleNet     = ($ShowNet -or $StallSec -gt 0)
        if ($sampleNet) { $lastNetB = Get-NetRxBytes }
        $exitIdle      = 0
        $wasKilled     = $false

        while ($true) {
            $got = $false
            foreach ($s in $streams) {
                if ($s.Done) { continue }
                if ($s.T.IsCompleted) {
                    $line = $null
                    try { $line = $s.T.Result } catch { $line = $null }
                    if ($null -eq $line) {
                        $s.Done = $true
                    } else {
                        $got = $true
                        $lastAct = [DateTime]::UtcNow
                        if ($line.Contains("`r")) { $line = ($line -split "`r")[-1] }
                        [void]$lines.Add($line)
                        Write-Log ("    " + $line)
                        if ($Live -and $line.Trim().Length -gt 0) {
                            $disp = $line.TrimEnd(); $col = 'DarkGray'
                            if ($LineFilter) {
                                $r = & $LineFilter $line
                                if ($null -eq $r) { $disp = $null } else { $disp = [string]$r.Text; $col = [string]$r.Color }
                            }
                            if ($disp) { Out-Line ('         ' + (Limit-Text $disp ((Get-ConsoleWidth) - 10))) $col -NoLog }
                        }
                        $s.T = $s.R.ReadLineAsync()
                    }
                }
            }

            if (-not $got) {
                if ($p.HasExited) {
                    $open = @($streams | Where-Object { -not $_.Done })
                    if ($open.Count -eq 0) { break }
                    $exitIdle++
                    if ($exitIdle -gt 30) { break }   # a grandchild kept the pipe open; never hang on it
                }
                Start-Sleep -Milliseconds 100
            }
            $now = [DateTime]::UtcNow

            # ---- checks that must run even while the child is chatty ----
            if ($sampleNet -and ($now - $lastNetT).TotalSeconds -ge 1) {
                $cur = Get-NetRxBytes
                $dt = ($now - $lastNetT).TotalSeconds
                $inst = ($cur - $lastNetB) / $dt
                if ($inst -lt 0) { $inst = 0 }
                $netRate = ($netRate * 0.5) + ($inst * 0.5)
                if ($netRate -gt 2048) { $lastNetActive = $now }
                $lastNetB = $cur; $lastNetT = $now
            }

            if ($p.HasExited) { continue }

            if ($Probe -and ($now - $lastProbe).TotalSeconds -ge 1) {
                $lastProbe = $now
                $ok = $false
                try { $ok = [bool](& $Probe) } catch { $ok = $false }
                if ($ok) { $result.ProbeOk = $true; Stop-ProcessTree $p.Id; $wasKilled = $true; break }
            }

            if ($StallSec -gt 0 -and $ShowNet -and ($now - $lastAct).TotalSeconds -gt $StallSec -and ($now - $lastNetActive).TotalSeconds -gt $StallSec) {
                Out-Line "  [WARN] '$Label' shows no output and no network traffic for $StallSec s - restarting it." 'Yellow'
                Stop-ProcessTree $p.Id
                $result.Stalled = $true; $wasKilled = $true
                break
            }

            if ($sw.Elapsed.TotalSeconds -gt $TimeoutSec) {
                Out-Line "  [WARN] '$Label' exceeded the $TimeoutSec s time limit - terminating it." 'Yellow'
                Stop-ProcessTree $p.Id
                $result.TimedOut = $true; $wasKilled = $true
                break
            }

            if (-not $Quiet -and -not $got -and ($now - $lastBeat).TotalMilliseconds -ge 500) {
                $lastBeat = $now
                $script:SpinIdx++
                $txt = '  [{0}] {1}  {2}' -f $spin[$script:SpinIdx % 4], $Label, (Format-Elapsed $sw.Elapsed)
                $idle = ($now - $lastAct).TotalSeconds
                if ($idle -ge 5) { $txt += ('  | no output for {0}s - still working' -f [int]$idle) }
                if ($ShowNet) {
                    $txt += ('  | network {0}' -f (Format-Rate $netRate))
                    if ($idle -ge 120 -and $netRate -lt 2048) { $txt += ' (idle - may be stalled)' }
                }
                if ($WatchFile -and (Test-Path -LiteralPath $WatchFile)) {
                    try { $txt += ('  | file {0}' -f (Format-Size (Get-Item -LiteralPath $WatchFile).Length)) } catch { }
                }
                if ($script:IsRedirected) {
                    if (($now - $lastPlain).TotalSeconds -ge 30) { Out-Line $txt 'DarkGray'; $lastPlain = $now }
                } else {
                    Show-StatusLine $txt
                }
                Set-WindowTitleSafe ("Shorts Engine Setup [{0}/{1}] {2} - {3}" -f $script:StepNum, $TOTAL_STEPS, $Label, (Format-Elapsed $sw.Elapsed))
            }
        }

        try { [void]$p.WaitForExit(10000) } catch { }
        if ($p.HasExited) { $result.ExitCode = $p.ExitCode }
        if ($wasKilled -and $result.ProbeOk) { $result.ExitCode = 0 }
    } finally {
        Clear-StatusLine
        try { if ($p -and -not $p.HasExited) { Stop-ProcessTree $p.Id } } catch { }
        $sw.Stop()
        $result.Seconds = $sw.Elapsed.TotalSeconds
        $result.Output  = @($lines)
        Write-Log ("   exit={0} timedOut={1} stalled={2} seconds={3:N1}" -f $result.ExitCode, $result.TimedOut, $result.Stalled, $result.Seconds)
    }
    return $result
}

function Get-RootCause {
    param([string[]]$Lines)
    $clean = @($Lines | Where-Object { $_ -and $_.Trim().Length -gt 0 })
    if ($clean.Count -eq 0) { return '(no output)' }
    $err = @($clean | Where-Object { $_ -match '^\s*[A-Za-z_.]*(Error|Exception|Warning|Exit)\b.*:' -or $_ -match '^ERROR' -or $_ -match 'INSECURE STARTUP' })
    if ($err.Count -gt 0) { return $err[-1].Trim() }
    return $clean[-1].Trim()
}

function Write-Tail {
    param([string[]]$Lines, [int]$Count = 12)
    $clean = @($Lines | Where-Object { $_ -and $_.Trim().Length -gt 0 })
    $tail = @($clean | Select-Object -Last $Count)
    foreach ($l in $tail) { Out-Line ('         | ' + (Limit-Text $l ((Get-ConsoleWidth) - 14))) 'DarkYellow' }
}

# =============================================================================
#  NETWORK / DOWNLOAD ENGINE
# =============================================================================
function Initialize-Network {
    try {
        $p = [Net.SecurityProtocolType]::Tls12
        if ([Enum]::GetNames([Net.SecurityProtocolType]) -contains 'Tls13') { $p = $p -bor [Net.SecurityProtocolType]::Tls13 }
        [Net.ServicePointManager]::SecurityProtocol = $p
        [Net.ServicePointManager]::DefaultConnectionLimit = 16
        [Net.ServicePointManager]::Expect100Continue = $false
    } catch { }
    try {
        $sysProxy = [Net.WebRequest]::GetSystemWebProxy()
        $sysProxy.Credentials = [Net.CredentialCache]::DefaultCredentials
        [Net.WebRequest]::DefaultWebProxy = $sysProxy
    } catch { }
    if ($Proxy) {
        try {
            [Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy(([Uri]$Proxy), $true)
            $env:HTTP_PROXY = $Proxy; $env:HTTPS_PROXY = $Proxy
            Write-Info 'Using the proxy supplied with -Proxy for downloads and pip.'
        } catch { Write-Warn "Invalid -Proxy value: $($_.Exception.Message)" }
    }
}

function Test-UrlReachable {
    param([string]$Url, [int]$TimeoutMs = 7000)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $getDate = {
        param($headers)
        try { $d = $headers['Date']; if ($d) { return [DateTimeOffset]::Parse($d, [Globalization.CultureInfo]::InvariantCulture) } } catch { }
        return $null
    }
    try {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.Method = 'HEAD'; $req.Timeout = $TimeoutMs; $req.UserAgent = 'ShortsEngineSetup/3.2'
        $resp = $req.GetResponse()
        $sd = & $getDate $resp.Headers
        $resp.Close()
        return [pscustomobject]@{ Ok = $true; Ms = [int]$sw.ElapsedMilliseconds; Detail = 'OK'; ServerDate = $sd }
    } catch [System.Net.WebException] {
        $r = $_.Exception.Response
        if ($r) {
            $code = [int]$r.StatusCode
            $sd = & $getDate $r.Headers
            if ($code -eq 407) { return [pscustomobject]@{ Ok = $false; Ms = [int]$sw.ElapsedMilliseconds; Detail = 'proxy authentication required (HTTP 407)'; ServerDate = $null } }
            return [pscustomobject]@{ Ok = $true; Ms = [int]$sw.ElapsedMilliseconds; Detail = "reachable (HTTP $code)"; ServerDate = $sd }
        }
        $d = $_.Exception.Message
        if ($d -match 'trust|SSL|TLS|certificate') { $d += '  [TLS problem: check the system clock and any HTTPS-inspecting proxy]' }
        return [pscustomobject]@{ Ok = $false; Ms = [int]$sw.ElapsedMilliseconds; Detail = $d; ServerDate = $null }
    } catch {
        return [pscustomobject]@{ Ok = $false; Ms = [int]$sw.ElapsedMilliseconds; Detail = $_.Exception.Message; ServerDate = $null }
    }
}

function Invoke-HttpDownload {
    param([string]$Url, [string]$OutFile, [string]$Label)
    $part = "$OutFile.part"
    $existing = 0L
    if (Test-Path -LiteralPath $part) { $existing = (Get-Item -LiteralPath $part).Length }

    $req = [Net.HttpWebRequest]::Create($Url)
    $req.UserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ShortsEngineSetup/3.2'
    $req.AllowAutoRedirect = $true
    $req.Timeout = 30000
    $req.ReadWriteTimeout = 60000
    if ($existing -gt 0) { $req.AddRange([long]$existing) }

    $resp = $null; $in = $null; $fs = $null
    try {
        try {
            $resp = $req.GetResponse()
        } catch [System.Net.WebException] {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 416) {
                Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                throw 'Server rejected resume range; restarting download.'
            }
            throw
        }
        $startAt = 0L
        $mode = [IO.FileMode]::Create
        if ([int]$resp.StatusCode -eq 206 -and $existing -gt 0) { $startAt = $existing; $mode = [IO.FileMode]::Append }
        $len = $resp.ContentLength
        $total = -1L
        if ($len -gt 0) { $total = $startAt + $len }

        $in = $resp.GetResponseStream()
        $fs = [IO.File]::Open($part, $mode, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $buf = New-Object byte[] 262144
        $got = 0L
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $lastPaint = -1000L
        while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
            $fs.Write($buf, 0, $n)
            $got += $n
            if ($sw.ElapsedMilliseconds - $lastPaint -ge 250) {
                $lastPaint = $sw.ElapsedMilliseconds
                $done = $startAt + $got
                $rate = $got / [Math]::Max(0.001, $sw.Elapsed.TotalSeconds)
                if ($total -gt 0) {
                    $eta = ''
                    if ($rate -gt 0) { $eta = ' ETA ' + (Format-Elapsed ([TimeSpan]::FromSeconds(($total - $done) / $rate))) }
                    Show-StatusLine ('  Downloading {0}  [{1}] {2,3}%  {3} / {4}  {5}{6}' -f $Label, (New-Bar ($done / [double]$total)), [int](100 * $done / $total), (Format-Size $done), (Format-Size $total), (Format-Rate $rate), $eta)
                } else {
                    Show-StatusLine ('  Downloading {0}  {1}  {2}' -f $Label, (Format-Size $done), (Format-Rate $rate))
                }
                Set-WindowTitleSafe ("Shorts Engine Setup - downloading {0} {1}" -f $Label, (Format-Size ($startAt + $got)))
            }
        }
        $fs.Flush(); $fs.Close(); $fs = $null
        $final = (Get-Item -LiteralPath $part).Length
        if ($total -gt 0 -and $final -ne $total) { throw ("Incomplete download ({0} of {1} bytes)." -f $final, $total) }
        if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force }
        Move-Item -LiteralPath $part -Destination $OutFile -Force
        Clear-StatusLine
        Write-OK ("Downloaded {0} ({1} in {2})" -f $Label, (Format-Size $final), (Format-Elapsed $sw.Elapsed))
    } finally {
        if ($fs) { try { $fs.Close() } catch { } }
        if ($in) { try { $in.Close() } catch { } }
        if ($resp) { try { $resp.Close() } catch { } }
        Clear-StatusLine
    }
}

function Invoke-CurlDownload {
    param([string]$Url, [string]$OutFile, [string]$Label)
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (-not (Test-Path -LiteralPath $curl)) { throw 'curl.exe not available' }
    $r = Invoke-Native -FilePath $curl -ArgList @('-L', '--fail', '--retry', '3', '--retry-delay', '3', '--connect-timeout', '30', '-sS', '-A', 'ShortsEngineSetup/3.2', '-o', $OutFile, $Url) -Label "Downloading $Label (curl)" -WatchFile $OutFile -ShowNet -TimeoutSec 3600
    if ($r.ExitCode -ne 0) { throw ("curl failed (exit {0}): {1}" -f $r.ExitCode, (Get-RootCause $r.Output)) }
}

function Invoke-BitsDownload {
    param([string]$Url, [string]$OutFile, [string]$Label)
    if (-not (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue)) { throw 'BITS not available' }
    $job = Start-BitsTransfer -Source $Url -Destination $OutFile -Asynchronous -DisplayName "ShortsEngine-$Label"
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($job.JobState -in @('Queued', 'Connecting', 'Transferring', 'TransientError')) {
            $tot = $job.BytesTotal; $done = $job.BytesTransferred
            $pct = 0; if ($tot -gt 0) { $pct = [int](100 * $done / $tot) }
            Show-StatusLine ('  Downloading {0} (BITS)  {1}%  {2}  {3}' -f $Label, $pct, (Format-Size $done), (Format-Elapsed $sw.Elapsed))
            Start-Sleep -Milliseconds 500
            if ($sw.Elapsed.TotalMinutes -gt 60) { throw 'BITS timeout' }
        }
        if ($job.JobState -ne 'Transferred') { throw "BITS state: $($job.JobState)" }
        Complete-BitsTransfer -BitsJob $job
    } finally {
        Clear-StatusLine
        try { if ($job.JobState -ne 'Transferred') { Remove-BitsTransfer -BitsJob $job -ErrorAction SilentlyContinue } } catch { }
    }
}

function Save-Download {
    <# Multi-URL, multi-method, resumable download. Returns $true on success. #>
    param([string[]]$Urls, [string]$OutFile, [string]$Label, [long]$MinBytes = 100KB, [int]$Attempts = 3)
    $dir = Split-Path -Parent $OutFile
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $urlNum = 0
    foreach ($url in $Urls) {
        $urlNum++
        Write-Info ("Source {0}/{1}: {2}" -f $urlNum, $Urls.Count, $url)
        for ($a = 1; $a -le $Attempts; $a++) {
            try {
                Invoke-HttpDownload -Url $url -OutFile $OutFile -Label $Label
                if ((Get-Item -LiteralPath $OutFile).Length -ge $MinBytes) { return $true }
                throw 'Downloaded file is suspiciously small.'
            } catch {
                Clear-StatusLine
                Write-Warn ("Download attempt {0}/{1} failed: {2}" -f $a, $Attempts, $_.Exception.Message)
                if ($a -lt $Attempts) {
                    $wait = [int][Math]::Pow(2, $a)
                    Write-Info "Retrying in $wait s (download will resume if the server allows)..."
                    Start-Sleep -Seconds $wait
                }
            }
        }
        foreach ($method in @('curl', 'bits')) {
            try {
                Write-Info "Trying alternative download method: $method"
                if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue }
                if ($method -eq 'curl') { Invoke-CurlDownload -Url $url -OutFile $OutFile -Label $Label }
                else                    { Invoke-BitsDownload -Url $url -OutFile $OutFile -Label $Label }
                if ((Test-Path -LiteralPath $OutFile) -and (Get-Item -LiteralPath $OutFile).Length -ge $MinBytes) {
                    Write-OK ("Downloaded {0} via {1}" -f $Label, $method); return $true
                }
            } catch {
                Write-Warn ("{0} download failed: {1}" -f $method, $_.Exception.Message)
            }
        }
    }
    return $false
}

function Test-CachedFile {
    param([string]$Path, [long]$MinBytes)
    return ((Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path).Length -ge $MinBytes))
}

# =============================================================================
#  SYSTEM HELPERS
# =============================================================================
function Get-RegValue {
    param([string]$Path, [string]$Name)
    try { $k = Get-Item -LiteralPath $Path -ErrorAction Stop; return $k.GetValue($Name) } catch { return $null }
}

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Update-PathFromRegistry {
    Write-Info 'Refreshing PATH from the Windows registry...'
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ';'
    if (Test-Path -LiteralPath $FFMPEG_BIN) { $env:Path = "$FFMPEG_BIN;$env:Path" }
}

function Wait-ProcessLive {
    param($Process, [string]$Label, [int]$TimeoutSec = 900)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $spin = @('|', '/', '-', '\'); $i = 0
    while (-not $Process.HasExited) {
        $i++
        Show-StatusLine ('  [{0}] {1}  {2}' -f $spin[$i % 4], $Label, (Format-Elapsed $sw.Elapsed))
        Set-WindowTitleSafe ("Shorts Engine Setup - {0} {1}" -f $Label, (Format-Elapsed $sw.Elapsed))
        Start-Sleep -Milliseconds 400
        if ($sw.Elapsed.TotalSeconds -gt $TimeoutSec) { Stop-ProcessTree $Process.Id; throw "$Label timed out after $TimeoutSec s" }
    }
    Clear-StatusLine
}

function Test-ValidSignature {
    param([string]$Path, [string]$SubjectPattern)
    try {
        $sig = Get-AuthenticodeSignature -FilePath $Path
        $subj = ''
        if ($sig.SignerCertificate) { $subj = [string]$sig.SignerCertificate.Subject }
        if ($sig.Status -eq 'Valid') { return ($subj -match $SubjectPattern) }
        if (($sig.Status -eq 'NotTrusted' -or $sig.Status -eq 'UnknownError') -and $subj -match $SubjectPattern) {
            Write-Warn 'Signature chain could not be fully validated (offline root certificates?) but the publisher name matches - continuing.'
            return $true
        }
        return $false      # NotSigned / HashMismatch / wrong publisher
    } catch {
        Write-Warn "Could not verify the digital signature ($($_.Exception.Message)); relying on the HTTPS source."
        return $true
    }
}

# =============================================================================
#  CONSOLE GUARDS: no QuickEdit freeze, no sleep during long installs
# =============================================================================
function Enable-ConsoleGuards {
    if ($env:OS -ne 'Windows_NT') { return }
    try {
        if (-not ('YtSetup.NativeMethods' -as [type])) {
            $src = @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
[DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
[DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
'@
            Add-Type -Namespace YtSetup -Name NativeMethods -MemberDefinition $src -ErrorAction Stop
        }
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED : keep Windows awake while we work
        [void][YtSetup.NativeMethods]::SetThreadExecutionState([Convert]::ToUInt32('80000001', 16))
        $script:GuardsOn = $true
        if (-not $script:IsRedirected) {
            $h = [YtSetup.NativeMethods]::GetStdHandle(-10)
            $mode = [uint32]0
            if ([YtSetup.NativeMethods]::GetConsoleMode($h, [ref]$mode)) {
                $script:ConsoleModeOrig = $mode
                # clear ENABLE_QUICK_EDIT_MODE (0x40) so an accidental click cannot freeze the output; set ENABLE_EXTENDED_FLAGS (0x80)
                $new = [uint32](([int64]$mode -band 4294967231L) -bor 128L)
                [void][YtSetup.NativeMethods]::SetConsoleMode($h, $new)
            }
        }
        Write-Log 'Console guards enabled (sleep prevention, QuickEdit off).'
    } catch { Write-Log "Console guards unavailable: $($_.Exception.Message)" }
}

function Disable-ConsoleGuards {
    if (-not $script:GuardsOn) { return }
    try {
        [void][YtSetup.NativeMethods]::SetThreadExecutionState([Convert]::ToUInt32('80000000', 16))
        if ($null -ne $script:ConsoleModeOrig) {
            $h = [YtSetup.NativeMethods]::GetStdHandle(-10)
            [void][YtSetup.NativeMethods]::SetConsoleMode($h, [uint32]$script:ConsoleModeOrig)
        }
    } catch { }
    $script:GuardsOn = $false
}

# =============================================================================
#  REPORTING
# =============================================================================
function Write-Report {
    param([int]$ExitCode)
    try {
        $rep = [ordered]@{
            scriptVersion = $SCRIPT_VERSION
            finishedAt    = (Get-Date).ToString('s')
            elapsedSec    = [int]((Get-Date) - $script:T0).TotalSeconds
            exitCode      = $ExitCode
            projectRoot   = $PROJECT_ROOT
            python        = [string]$script:PythonExe
            pythonVersion = [string]$script:PythonVer
            venvOk        = $script:VenvOk
            gpuTorch      = $script:GpuTorch
            dashboardPort = $script:DashPort
            rebootNeeded  = $script:RebootNeeded
            steps         = @($script:StepResults | ForEach-Object { [ordered]@{ n = $_.Num; name = $_.Name; status = $_.Status; seconds = $_.Seconds } })
            errors        = @($script:Errors | Select-Object -Unique)
            warnings      = @($script:Warnings | Select-Object -Unique)
        }
        [IO.File]::WriteAllText((Join-Path $script:LOG_DIR 'setup_report.json'), ($rep | ConvertTo-Json -Depth 5), $script:Utf8NoBom)
    } catch { }
}

function Write-Diagnostics {
    <# Support bundle. Contains NO secrets: .env values are never read into it, only key names. #>
    try {
        $f = Join-Path $script:LOG_DIR 'setup_diagnostics.txt'
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine("Shorts Engine setup diagnostics v$SCRIPT_VERSION  ($(Get-Date -Format 's'))")
        [void]$sb.AppendLine(('=' * 70))
        $os = ''; try { $os = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption } catch { }
        [void]$sb.AppendLine("OS           : $os  build $([Environment]::OSVersion.Version.Build)  64bitOS=$([Environment]::Is64BitOperatingSystem)")
        [void]$sb.AppendLine("PowerShell   : $($PSVersionTable.PSVersion)  admin=$($script:IsAdmin)  language=$($ExecutionContext.SessionState.LanguageMode)")
        [void]$sb.AppendLine("Project      : $PROJECT_ROOT")
        [void]$sb.AppendLine("Python       : $($script:PythonExe) ($($script:PythonVer))   venvOk=$($script:VenvOk)")
        [void]$sb.AppendLine("Online       : $($script:Online)   proxyEnvSet=$([bool]($env:HTTPS_PROXY -or $env:HTTP_PROXY))")
        foreach ($v in @('PYTHONHOME', 'PYTHONPATH', 'PIP_INDEX_URL', 'PIP_USER', 'VIRTUAL_ENV')) {
            if ([Environment]::GetEnvironmentVariable($v)) { [void]$sb.AppendLine("Env present  : $v") }
        }
        try {
            $drv = New-Object System.IO.DriveInfo([IO.Path]::GetPathRoot($PROJECT_ROOT))
            [void]$sb.AppendLine(("Free disk    : {0:N1} GB on {1}" -f ($drv.AvailableFreeSpace / 1GB), $drv.Name))
        } catch { }
        $vc = Get-VCRuntimeStatus
        [void]$sb.AppendLine("VC++ runtime : version=$($vc.Version) missingDlls=$($vc.MissingDlls -join ',')")
        if (Test-Path -LiteralPath $ENV_FILE) {
            $keys = @((Get-DotEnvMap $ENV_FILE).Keys | Sort-Object)
            [void]$sb.AppendLine("Keys in .env : $($keys -join ', ')   (values intentionally omitted)")
        }
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('STEP RESULTS')
        foreach ($r in $script:StepResults) { [void]$sb.AppendLine(("  {0,2}. {1,-36} {2,-5} {3}s" -f $r.Num, $r.Name, $r.Status, $r.Seconds)) }
        [void]$sb.AppendLine(''); [void]$sb.AppendLine('ERRORS');   foreach ($e in @($script:Errors   | Select-Object -Unique)) { [void]$sb.AppendLine("  - $e") }
        [void]$sb.AppendLine(''); [void]$sb.AppendLine('WARNINGS'); foreach ($w in @($script:Warnings | Select-Object -Unique)) { [void]$sb.AppendLine("  - $w") }
        if ($script:VenvOk) {
            $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @('-m', 'pip', 'list', '--format=freeze') -Quiet -TimeoutSec 60
            [void]$sb.AppendLine(''); [void]$sb.AppendLine('INSTALLED PACKAGES (pip list)')
            foreach ($l in $r.Output) { [void]$sb.AppendLine("  $l") }
        }
        if (Test-Path -LiteralPath $script:LOG_FILE) {
            [void]$sb.AppendLine(''); [void]$sb.AppendLine('LAST 80 LOG LINES')
            foreach ($l in @(Get-Content -LiteralPath $script:LOG_FILE -Tail 80 -ErrorAction SilentlyContinue)) { [void]$sb.AppendLine("  $l") }
        }
        [IO.File]::WriteAllText($f, $sb.ToString(), $script:Utf8NoBom)
        return $f
    } catch { return $null }
}

function Update-FfmpegPth {
    <# Makes the project-local FFmpeg visible to EVERY python process started from the venv,
       even when the user launches it without our launcher or without activating anything. #>
    if (-not $script:VenvOk) { return }
    if (-not (Test-Path -LiteralPath (Join-Path $FFMPEG_BIN 'ffmpeg.exe'))) { return }
    $sp = Join-Path $VENV_DIR 'Lib\site-packages'
    if (-not (Test-Path -LiteralPath $sp)) { return }
    if ($FFMPEG_BIN -match '[^\x20-\x7E]' -or $FFMPEG_BIN.Contains("'")) {
        Write-Warn 'FFmpeg path has unusual characters; skipped venv PATH hook (use tools\launchers\*.bat instead).'; return
    }
    try {
        $pth = Join-Path $sp 'yt_shorts_ffmpeg_path.pth'
        $line = "import os; os.environ['PATH'] = r'$FFMPEG_BIN' + os.pathsep + os.environ.get('PATH', '')"
        [IO.File]::WriteAllText($pth, $line + "`r`n", (New-Object System.Text.ASCIIEncoding))
        Write-OK 'FFmpeg is on PATH for every Python process started from this venv (no activation needed)'
    } catch { Write-Warn "Could not install the venv FFmpeg PATH hook: $($_.Exception.Message)" }
}

function Get-PipRequirementsPath {
    try {
        $b = [IO.File]::ReadAllBytes($REQ_FILE)
        if ($b.Length -ge 2 -and (($b[0] -eq 0xFF -and $b[1] -eq 0xFE) -or ($b[0] -eq 0xFE -and $b[1] -eq 0xFF))) {
            New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
            $fixed = Join-Path $TMP_DIR 'requirements.utf8.txt'
            [IO.File]::WriteAllText($fixed, (Get-Content -LiteralPath $REQ_FILE -Raw), $script:Utf8NoBom)
            Write-Warn 'requirements.txt is saved as UTF-16 (pip cannot read that, typical after "pip freeze > file" in PowerShell) - using a converted UTF-8 copy.'
            return $fixed
        }
    } catch { }
    return $REQ_FILE
}

# =============================================================================
#  STEP 1 - PRE-FLIGHT
# =============================================================================
function Step-Preflight {
    if ($env:OS -ne 'Windows_NT') { Write-Fatal 'This installer supports Windows only.' }

    $caption = 'Windows'; $build = [Environment]::OSVersion.Version.Build
    try { $cim = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop; $caption = $cim.Caption; $build = [int]$cim.BuildNumber } catch { }
    Write-Info "Operating system : $caption (build $build)"
    if (-not [Environment]::Is64BitOperatingSystem) {
        Write-Fatal '32-bit Windows detected. PyTorch and ONNX Runtime require 64-bit Windows.'
    }
    if ($build -lt 17763) { Write-Warn "Windows build $build is older than Windows 10 1809; some downloads/installers may fail." }
    if (-not [Environment]::Is64BitProcess) { Write-Warn 'Running in 32-bit PowerShell. Use the 64-bit shell if anything fails.' }
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if ($arch -eq 'ARM64') { Write-Warn 'ARM64 Windows detected: x64 Python runs under emulation; some wheels (PyTorch) may be unavailable.' }
    Write-Info "PowerShell       : $($PSVersionTable.PSVersion)"
    Write-Log ("ENV: PROCESSOR_ARCHITECTURE={0} Culture={1} User={2} TEMP={3}" -f $arch, [Globalization.CultureInfo]::CurrentCulture.Name, $env:USERNAME, $env:TEMP)
    foreach ($v in @('PYTHONHOME', 'PYTHONPATH', 'PIP_USER', 'VIRTUAL_ENV', 'PIP_INDEX_URL')) {
        if ([Environment]::GetEnvironmentVariable($v)) { Write-Warn "Environment variable $v is set. Setup ignores PYTHONHOME/PYTHONPATH/PIP_USER/VIRTUAL_ENV for its own processes, but your shell may behave differently." }
    }
    $rb = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    if ($rb) { Write-Warn 'Windows has a pending restart. Installers may fail (exit 1618/3010) until you reboot; setup will retry automatically.' }

    $script:IsAdmin = Test-IsAdmin
    if ($script:IsAdmin) { Write-Info 'Privileges       : Administrator' }
    else                 { Write-Info 'Privileges       : standard user (a UAC prompt may appear if the C++ runtime must be installed)' }

    # --- project integrity -----------------------------------------------------
    if (-not (Test-Path -LiteralPath $REQ_FILE)) {
        Write-Fatal "requirements.txt not found in $PROJECT_ROOT" 'Run setup.bat from the project root folder (the folder that contains requirements.txt).'
    }
    if (-not (Test-Path -LiteralPath $PYPROJECT)) { Write-Warn 'pyproject.toml not found - editable package install will be skipped.' }
    if (-not (Test-Path -LiteralPath $RUN_PY))    { Write-Warn 'run.py not found - launchers and the server smoke test will be skipped.' }
    Write-OK 'Project files located'

    # --- path sanity -----------------------------------------------------------
    if ($PROJECT_ROOT -like '\\*') { Write-Warn 'Project is on a network (UNC) path. Virtual environments on network shares are unreliable - copy the project to a local drive.' }
    if ($PROJECT_ROOT -match '(?i)\\OneDrive[^\\]*\\') { Write-Warn 'Project is inside a OneDrive-synced folder. Sync locking can break pip installs - consider a path like C:\Projects.' }
    if ($PROJECT_ROOT -match '[^\x00-\x7F]') { Write-Warn 'Project path contains non-ASCII characters; some Python tools mis-handle them. A plain ASCII path is safer.' }
    $lp = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' 'LongPathsEnabled'
    if ($PROJECT_ROOT.Length -gt 80 -and $lp -ne 1) {
        if ($script:IsAdmin) {
            try {
                Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord
                Write-OK 'Enabled Windows long-path support (path length > 80 characters).'
            } catch { Write-Warn "Could not enable long paths: $($_.Exception.Message)" }
        } else {
            Write-Warn ('Project path is {0} characters long and Windows long-path support is off. Deep package paths may exceed 260 characters; move the project closer to the drive root if pip fails.' -f $PROJECT_ROOT.Length)
        }
    }

    # --- disk space ------------------------------------------------------------
    try {
        $drv = [IO.Path]::GetPathRoot($PROJECT_ROOT)
        if ($drv -and $drv -notlike '\\*') {
            $di = New-Object System.IO.DriveInfo($drv)
            $freeGb = [Math]::Round($di.AvailableFreeSpace / 1GB, 1)
            $need = $MIN_FREE_GB; if ($GPU) { $need = 10 }
            if ($freeGb -lt 2) { Write-Fatal "Only $freeGb GB free on $drv - at least $need GB is required." 'Free up disk space and re-run.' }
            elseif ($freeGb -lt $need) { Write-Warn "Only $freeGb GB free on $drv (recommended: $need GB)." }
            else { Write-OK "Disk space: $freeGb GB free on $drv" }
        }
    } catch { Write-Dim "Disk space check skipped: $($_.Exception.Message)" }

    try {
        $tdrv = [IO.Path]::GetPathRoot($env:TEMP)
        if ($tdrv -and ($tdrv -ne [IO.Path]::GetPathRoot($PROJECT_ROOT))) {
            $tfree = [Math]::Round((New-Object System.IO.DriveInfo($tdrv)).AvailableFreeSpace / 1GB, 1)
            if ($tfree -lt 3) { Write-Warn "Only $tfree GB free on $tdrv (TEMP). pip builds and installers use TEMP." }
        }
    } catch { }

    # --- write access ----------------------------------------------------------
    try {
        New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
        $probe = Join-Path $TMP_DIR ('write_test_{0}.tmp' -f [guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText($probe, 'ok'); Remove-Item -LiteralPath $probe -Force
        Write-OK 'Project folder is writable'
    } catch {
        Write-Fatal "Cannot write to the project folder: $($_.Exception.Message)" 'Run from a folder you own (not Program Files) or fix permissions.'
    }

    # --- connectivity ----------------------------------------------------------
    Initialize-Network
    $targets = @(
        @{ N = 'PyPI index';       U = 'https://pypi.org/simple/pip/' },
        @{ N = 'PyPI files';       U = 'https://files.pythonhosted.org/' },
        @{ N = 'GitHub';           U = 'https://github.com' },
        @{ N = 'python.org';       U = 'https://www.python.org' },
        @{ N = 'PyTorch index';    U = 'https://download.pytorch.org/whl/cpu/' }
    )
    $okCount = 0; $i = 0; $consecFail = 0; $skew = $null
    foreach ($t in $targets) {
        $i++
        Show-StatusLine ("  Checking internet access {0}/{1}: {2}..." -f $i, $targets.Count, $t.N)
        $r = Test-UrlReachable $t.U
        if ($r.Ok) {
            $okCount++; $consecFail = 0
            Write-OK ("{0,-14} reachable ({1} ms)" -f $t.N, $r.Ms)
            if ($null -eq $skew -and $r.ServerDate) { $skew = [Math]::Abs(([DateTimeOffset]::UtcNow - $r.ServerDate).TotalMinutes) }
        } else {
            $consecFail++
            Write-Warn ("{0,-14} NOT reachable: {1}" -f $t.N, $r.Detail)
            if ($consecFail -ge 3) { Write-Info 'Three hosts in a row failed - skipping the remaining checks.'; break }
        }
    }
    if ($null -ne $skew -and $skew -gt 10) {
        Write-Warn ("Your PC clock differs from internet time by {0:N0} minutes. This breaks HTTPS certificate checks (downloads / pip may fail). Fix the date, time and time zone." -f $skew)
    }
    $script:Online = ($okCount -gt 0)
    if ($okCount -eq 0) {
        Write-Err 'No internet access detected. Downloads will fail unless everything is already installed.'
        Write-Hint 'Check your connection / proxy / firewall. Corporate proxy: set HTTPS_PROXY and re-run.'
    } elseif ($okCount -lt $targets.Count) {
        Write-Hint 'Some hosts are blocked; installers have alternate sources, but a firewall/proxy may cause failures.'
    }
}

# =============================================================================
#  STEP 2 - PYTHON
# =============================================================================
function Get-PythonInfo {
    param([string]$Exe)
    $bad = { param($why) return [pscustomobject]@{ Ok = $false; Reason = $why; Version = $null; Path = $Exe } }
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return (& $bad 'file not found') }
    if ($Exe -match '(?i)\\WindowsApps\\') { return (& $bad 'Microsoft Store alias/stub is not supported') }
    $code = "import sys,struct;print('PYINFO|%d.%d.%d|%d|%s' % (sys.version_info[0],sys.version_info[1],sys.version_info[2],struct.calcsize('P')*8,sys.executable));import venv,ensurepip,ssl,sqlite3;print('PYMODS|OK')"
    $r = Invoke-Native -FilePath $Exe -ArgList @('-c', $code) -TimeoutSec 40 -Quiet
    if (-not $r.Started) { return (& $bad 'could not start') }
    $info = @($r.Output | Where-Object { $_ -match '^PYINFO\|' }) | Select-Object -First 1
    if (-not $info) { return (& $bad ('not a working Python: ' + (Get-RootCause $r.Output))) }
    $parts = $info -split '\|'
    $ver = [version]$parts[1]; $bits = [int]$parts[2]
    $real = $Exe; if ($parts.Count -ge 4 -and $parts[3]) { $real = $parts[3] }
    if ($bits -ne 64) { return (& $bad "$ver is 32-bit (64-bit required)") }
    if ($ver -lt $PY_MIN) { return (& $bad "version $ver is older than $PY_MIN") }
    if ($ver.Minor -gt $PY_MAX_MINOR) { return (& $bad "version $ver is too new (project pins numpy<2, which has no wheels for Python 3.13+)") }
    if (-not (@($r.Output | Where-Object { $_ -match '^PYMODS\|OK' }).Count)) { return (& $bad 'missing venv/ensurepip/ssl/sqlite3 modules') }
    return [pscustomobject]@{ Ok = $true; Reason = ''; Version = $ver; Path = $real }
}

function Get-PythonCandidates {
    $c = New-Object System.Collections.ArrayList
    $add = { param($p) if ($p -is [string] -and $p.Trim()) { [void]$c.Add($p.Trim().Trim('"')) } }

    foreach ($n in @('python', 'python3')) {
        foreach ($cmd in @(Get-Command $n -All -CommandType Application -ErrorAction SilentlyContinue)) { & $add $cmd.Source }
    }
    $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($py) {
        $r = Invoke-Native -FilePath $py.Source -ArgList @('-0p') -Quiet -TimeoutSec 20
        foreach ($l in $r.Output) { if ($l -match '([A-Za-z]:\\.*?python\.exe)') { & $add $Matches[1] } }
    }
    foreach ($root in @('HKCU:\Software\Python\PythonCore', 'HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore')) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($v in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $ip = Join-Path (Join-Path $root $v.PSChildName) 'InstallPath'
            $exe = Get-RegValue $ip 'ExecutablePath'
            if (-not $exe) { $dir = Get-RegValue $ip ''; if ($dir) { $exe = Join-Path $dir 'python.exe' } }
            & $add $exe
        }
    }
    foreach ($m in @(11, 12)) {
        & $add (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3$m\python.exe")
        & $add (Join-Path $env:ProgramFiles "Python3$m\python.exe")
        & $add "C:\Python3$m\python.exe"
    }
    & $add (Join-Path $LOCAL_PY_DIR 'python.exe')
    return @($c)
}

function Find-ValidPython {
    $best = $null; $seen = @{}
    $cands = @(Get-PythonCandidates)
    $i = 0
    foreach ($cand in $cands) {
        $key = $cand.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $i++
        Show-StatusLine ("  Inspecting Python candidate {0}: {1}" -f $i, (Limit-Text $cand 70))
        $info = Get-PythonInfo $cand
        if (-not $info.Ok) { Write-Dim ("skipped {0} - {1}" -f $cand, $info.Reason); continue }
        Write-Dim ("candidate {0} -> Python {1}" -f $cand, $info.Version)
        $score = [Math]::Abs($info.Version.Minor - $PY_PREFERRED) * 1000 - $info.Version.Build
        if ($null -eq $best -or $score -lt $best.Score) {
            $best = [pscustomobject]@{ Path = $info.Path; Version = $info.Version; Score = $score }
        }
    }
    Clear-StatusLine
    return $best
}

function Install-PythonFromOrg {
    foreach ($ver in $PY_INSTALL_VERS) {
        Out-Line "  --- Method A: python.org installer (Python $ver, per-user, adds to PATH) ---" 'Cyan'
        $url = "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe"
        $dst = Join-Path $DL_DIR "python-$ver-amd64.exe"
        $valid = (Test-CachedFile $dst 20MB) -and (Test-ValidSignature $dst 'Python Software Foundation')
        if ($valid) { Write-OK "Using previously downloaded installer: $dst" }
        else {
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue }
            if (-not (Save-Download -Urls @($url) -OutFile $dst -Label "Python $ver installer" -MinBytes 20MB)) { continue }
            if (-not (Test-ValidSignature $dst 'Python Software Foundation')) {
                Write-Warn 'Installer signature check FAILED (file corrupted or tampered). Discarding it.'
                Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue
                continue
            }
            Write-OK 'Installer digital signature verified (Python Software Foundation)'
        }
        $ilog = Join-Path $script:LOG_DIR "python-$ver-install.log"
        for ($try = 1; $try -le 3; $try++) {
            Write-Info "Running silent installer (attempt $try/3) - this takes 1-3 minutes..."
            $pyArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'Include_doc=0', 'Shortcuts=0', 'AssociateFiles=0', '/log', $ilog)
            $r = Invoke-Native -FilePath $dst -ArgList $pyArgs -Label "Installing Python $ver" -WatchFile $ilog -TimeoutSec 1200 -Quiet:$false
            Write-Info "Installer exit code: $($r.ExitCode)"
            if ($r.ExitCode -eq 0 -or $r.ExitCode -eq 3010 -or $r.ExitCode -eq 1638) {
                if ($r.ExitCode -eq 3010) { $script:RebootNeeded = $true }
                Update-PathFromRegistry
                return $true
            }
            if ($r.ExitCode -eq 1618) { Write-Warn 'Another installation is in progress. Waiting 30 s...'; Start-Sleep -Seconds 30; continue }
            if ($r.ExitCode -eq 1602) { Write-Warn 'Installation was cancelled.'; break }
            Write-Warn "Python installer failed (exit $($r.ExitCode))."
            if (Test-Path -LiteralPath $ilog) {
                $errs = @(Get-Content -LiteralPath $ilog -ErrorAction SilentlyContinue | Where-Object { $_ -match 'Error 0x|failed|Failed' } | Select-Object -Last 5)
                foreach ($e in $errs) { Write-Dim $e.Trim() }
            }
            Start-Sleep -Seconds 5
        }
    }
    return $false
}

function Install-PythonFromWinget {
    Out-Line '  --- Method B: winget ---' 'Cyan'
    $w = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $w) { Write-Warn 'winget is not available on this system.'; return $false }
    $filter = { param($l) if ($l -match '^[\s\-\\|/]*$') { return $null } else { return @{ Text = $l.Trim(); Color = 'Gray' } } }
    foreach ($scope in @(@('--scope', 'user'), @())) {
        $a = @('install', '--id', 'Python.Python.3.11', '-e', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') + $scope
        $r = Invoke-Native -FilePath $w.Source -ArgList $a -Label 'winget install Python 3.11' -Live -LineFilter $filter -ShowNet -TimeoutSec 1800
        if ($r.ExitCode -eq 0 -or $r.ExitCode -eq -1978335189 -or $r.ExitCode -eq -1978335188) { Update-PathFromRegistry; return $true }
        Write-Warn "winget exit code $($r.ExitCode)"
    }
    return $false
}

function Install-PythonFromNuGet {
    Out-Line '  --- Method C: portable Python from the NuGet package (no installer, no admin) ---' 'Cyan'
    $ver = '3.11.9'
    $pkg = Join-Path $DL_DIR "python.$ver.nupkg"
    if (-not (Test-CachedFile $pkg 20MB)) {
        if (-not (Save-Download -Urls @("https://www.nuget.org/api/v2/package/python/$ver") -OutFile $pkg -Label "Python $ver (NuGet)" -MinBytes 20MB)) { return $false }
    }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($pkg)
        try {
            if (Test-Path -LiteralPath $LOCAL_PY_DIR) { Remove-Item -LiteralPath $LOCAL_PY_DIR -Recurse -Force -ErrorAction SilentlyContinue }
            $entries = @($zip.Entries | Where-Object { $_.FullName -like 'tools/*' -and $_.Name })
            $n = 0
            foreach ($e in $entries) {
                $n++
                $rel = $e.FullName.Substring(6) -replace '/', '\'
                $dest = Join-Path $LOCAL_PY_DIR $rel
                $dd = Split-Path -Parent $dest
                if (-not (Test-Path -LiteralPath $dd)) { New-Item -ItemType Directory -Path $dd -Force | Out-Null }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
                if ($n % 50 -eq 0 -or $n -eq $entries.Count) { Show-StatusLine ('  Extracting portable Python  {0}/{1} files' -f $n, $entries.Count) }
            }
        } finally { $zip.Dispose(); Clear-StatusLine }
        $info = Get-PythonInfo (Join-Path $LOCAL_PY_DIR 'python.exe')
        if ($info.Ok) { Write-OK "Portable Python $($info.Version) extracted to $LOCAL_PY_DIR"; return $true }
        Write-Warn "Portable Python failed validation: $($info.Reason)"
    } catch { Write-Warn "NuGet extraction failed: $($_.Exception.Message)" }
    return $false
}

function Step-Python {
    $found = Find-ValidPython
    if ($found) {
        $script:PythonExe = $found.Path; $script:PythonVer = $found.Version
        Write-OK ("Found Python {0}: {1}" -f $found.Version, $found.Path)
        if ($found.Version.Minor -ne $PY_PREFERRED) { Write-Info "Note: Python 3.$PY_PREFERRED is the tested version; 3.$($found.Version.Minor) is accepted." }
        return
    }

    if ($script:NoInstall) { Write-Fatal "No suitable 64-bit Python $PY_MIN - 3.$PY_MAX_MINOR found (verify-only mode: not installing)." 'Run setup.bat without -VerifyOnly to install it automatically.' }
    Write-Warn "No suitable Python found (need 64-bit Python $PY_MIN - 3.$PY_MAX_MINOR). Installing automatically..."
    if (-not $script:Online) { Write-Fatal 'Python is missing and there is no internet access to download it.' 'Install Python 3.11 from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and re-run.' }

    $methods = @(
        @{ N = 'python.org installer'; F = { Install-PythonFromOrg } },
        @{ N = 'winget';               F = { Install-PythonFromWinget } },
        @{ N = 'portable NuGet build'; F = { Install-PythonFromNuGet } }
    )
    foreach ($m in $methods) {
        try {
            $ok = & $m.F
            if (-not $ok) { Write-Warn "Method '$($m.N)' did not succeed - trying the next method."; continue }
        } catch {
            Write-Warn "Method '$($m.N)' failed: $($_.Exception.Message)"; continue
        }
        Update-PathFromRegistry
        $found = Find-ValidPython
        if ($found) { break }
        Write-Warn "Method '$($m.N)' reported success but no usable Python was detected."
    }
    if (-not $found) {
        Write-Fatal 'Python could not be installed automatically.' 'Install Python 3.11 (64-bit) from https://www.python.org/downloads/windows/ with "Add python.exe to PATH" ticked, disable the Microsoft Store python alias (Settings > Apps > Advanced app settings > App execution aliases), then re-run setup.bat.'
    }
    $script:PythonExe = $found.Path; $script:PythonVer = $found.Version
    Write-OK ("Python {0} installed and verified: {1}" -f $found.Version, $found.Path)
    if ($script:RebootNeeded) { Write-Warn 'Windows reports a reboot is recommended after installation. Setup will continue.' }
}

# =============================================================================
#  STEP 3 - MICROSOFT VISUAL C++ RUNTIME (required by PyTorch / CTranslate2 / ONNX)
# =============================================================================
function Get-VCRuntimeStatus {
    $ver = $null
    $key = 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64'
    $installed = Get-RegValue $key 'Installed'
    $maj = Get-RegValue $key 'Major'; $min = Get-RegValue $key 'Minor'; $bld = Get-RegValue $key 'Bld'
    if ($installed -eq 1 -and $null -ne $maj -and $null -ne $min) {
        try { $ver = [version]("{0}.{1}.{2}" -f $maj, $min, $bld) } catch { $ver = [version]("{0}.{1}" -f $maj, $min) }
    }
    $sys = Join-Path $env:SystemRoot 'System32'
    $dlls = @('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll')
    $missing = @($dlls | Where-Object { -not (Test-Path -LiteralPath (Join-Path $sys $_)) })
    return [pscustomobject]@{ Version = $ver; MissingDlls = $missing; Ok = ($missing.Count -eq 0 -and $null -ne $ver -and $ver -ge $VC_MIN_VERSION) }
}

function Install-VCRuntime {
    $dst = Join-Path $DL_DIR 'vc_redist.x64.exe'
    if (-not ((Test-CachedFile $dst 10MB) -and (Test-ValidSignature $dst 'Microsoft'))) {
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue }
        if (-not (Save-Download -Urls $VC_REDIST_URLS -OutFile $dst -Label 'Visual C++ Runtime' -MinBytes 10MB)) { return $false }
        if (-not (Test-ValidSignature $dst 'Microsoft')) {
            Write-Warn 'C++ runtime installer signature check FAILED. Discarding it.'
            Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue
            return $false
        }
        Write-OK 'Installer digital signature verified (Microsoft)'
    }
    $ilog = Join-Path $script:LOG_DIR 'vc_redist-install.log'
    $iargs = @('/install', '/quiet', '/norestart', '/log', $ilog)
    $code = -1
    if ($script:IsAdmin) {
        Write-Info 'Installing Microsoft Visual C++ Runtime (silent)...'
        $r = Invoke-Native -FilePath $dst -ArgList $iargs -Label 'Installing Visual C++ Runtime' -WatchFile $ilog -TimeoutSec 900
        $code = $r.ExitCode
    } else {
        Write-Info 'Administrator rights are required - Windows will show a UAC prompt. Please click "Yes".'
        Write-Info '(If nothing appears, look for a flashing shield icon in the taskbar - the prompt may be behind this window.)'
        try { [Console]::Beep(880, 150) } catch { }
        try {
            $p = Start-Process -FilePath $dst -ArgumentList ($iargs | ForEach-Object { ConvertTo-QuotedArg $_ }) -Verb RunAs -PassThru
            $null = $p.Handle   # cache the handle so ExitCode is readable after exit
            Wait-ProcessLive $p 'Waiting for UAC approval / installing Visual C++ Runtime' 900
            $code = $p.ExitCode
        } catch {
            Write-Warn "Elevation was declined or failed: $($_.Exception.Message)"
            return $false
        }
    }
    Write-Info "Installer exit code: $code"
    if ($code -eq 3010 -or $code -eq 1641) { $script:RebootNeeded = $true; return $true }
    return ($code -eq 0 -or $code -eq 1638)
}

function Step-VCRuntime {
    param([switch]$Force)
    $st = Get-VCRuntimeStatus
    if ($st.Ok -and -not $Force) {
        Write-OK ("Visual C++ Runtime {0} present" -f $st.Version)
        return $true
    }
    if ($st.Ok -and $Force) {
        Write-Info ("Runtime {0} is present, but a native library still failed to load - refreshing it with the latest redistributable." -f $st.Version)
    } else {
        if ($st.MissingDlls.Count -gt 0) { Write-Warn ("Missing runtime DLLs: {0}" -f ($st.MissingDlls -join ', ')) }
        elseif ($null -eq $st.Version) { Write-Warn 'Visual C++ Runtime registry entry not found.' }
        else { Write-Warn ("Visual C++ Runtime {0} is older than the required {1}." -f $st.Version, $VC_MIN_VERSION) }
        Write-Info 'PyTorch, CTranslate2 (faster-whisper) and ONNX Runtime fail to load without it.'
    }
    if ($script:NoInstall) { Write-Err 'Visual C++ Runtime is missing/outdated (verify-only mode: not installing).'; return $false }
    if ($script:VcTried)   { Write-Warn 'The C++ runtime installation was already attempted in this run - not repeating.'; return $false }
    $script:VcTried = $true
    if (-not $script:Online) { Write-Err 'No internet - cannot install the Visual C++ Runtime.'; return $false }

    if (Install-VCRuntime) {
        $st = Get-VCRuntimeStatus
        if ($st.MissingDlls.Count -eq 0) {
            Write-OK ("Visual C++ Runtime installed ({0})" -f $st.Version)
            if ($script:RebootNeeded) { Write-Warn 'A reboot is recommended to finish the C++ runtime installation.' }
            return $true
        }
    }
    Write-Err 'Visual C++ Runtime could not be installed automatically.'
    Write-Hint 'Download and run https://aka.ms/vs/17/release/vc_redist.x64.exe manually, then re-run setup.bat.'
    return $false
}

# =============================================================================
#  STEP 4 - FFMPEG
# =============================================================================
function Test-FFmpegDir {
    param([string]$Dir)
    $ff = Join-Path $Dir 'ffmpeg.exe'
    if (-not (Test-Path -LiteralPath $ff)) { return $null }
    $r = Invoke-Native -FilePath $ff -ArgList @('-version') -Quiet -TimeoutSec 30
    if ($r.ExitCode -ne 0) { return $null }
    $v = @($r.Output | Where-Object { $_ -match 'ffmpeg version' }) | Select-Object -First 1
    if ($v) { return $v.Trim() }
    return 'ffmpeg (version string unavailable)'
}

function Install-FFmpegFromZip {
    param([string]$Url, [string]$Label)
    $zipPath = Join-Path $DL_DIR ([IO.Path]::GetFileName(([Uri]$Url).AbsolutePath))
    if (-not (Test-CachedFile $zipPath 30MB)) {
        if (-not (Save-Download -Urls @($Url) -OutFile $zipPath -Label $Label -MinBytes 30MB)) { return $false }
    } else { Write-OK "Using cached archive: $zipPath" }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = $null
    try {
        try { $zip = [IO.Compression.ZipFile]::OpenRead($zipPath) }
        catch { Write-Warn "Archive is corrupt ($($_.Exception.Message)); deleting it."; Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue; return $false }
        $want = @($zip.Entries | Where-Object { $_.FullName -match '(^|/)bin/(ffmpeg|ffprobe|ffplay)\.exe$' })
        if (-not ($want | Where-Object { $_.Name -eq 'ffmpeg.exe' })) { Write-Warn 'Archive does not contain ffmpeg.exe.'; return $false }
        if (-not (Test-Path -LiteralPath $FFMPEG_BIN)) { New-Item -ItemType Directory -Path $FFMPEG_BIN -Force | Out-Null }
        $i = 0
        foreach ($e in $want) {
            $i++
            Show-StatusLine ('  Extracting {0} ({1}/{2}, {3})' -f $e.Name, $i, $want.Count, (Format-Size $e.Length))
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $FFMPEG_BIN $e.Name), $true)
        }
        Clear-StatusLine
    } catch {
        Write-Warn "Extraction failed: $($_.Exception.Message)"; return $false
    } finally { if ($zip) { $zip.Dispose() }; Clear-StatusLine }
    return $true
}

function Step-FFmpeg {
    $v = Test-FFmpegDir $FFMPEG_BIN
    if ($v) {
        $env:Path = "$FFMPEG_BIN;$env:Path"
        Write-OK "Project-local FFmpeg works: $v"
        return
    }
    $inPath = Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($inPath) {
        $r = Invoke-Native -FilePath $inPath.Source -ArgList @('-version') -Quiet -TimeoutSec 30
        if ($r.ExitCode -eq 0) {
            Write-OK ("FFmpeg found in PATH: {0}" -f $inPath.Source)
            return
        }
        Write-Warn "ffmpeg in PATH ($($inPath.Source)) does not run - installing a project-local copy."
    }
    if ($script:NoInstall) { Write-Err 'FFmpeg is missing (verify-only mode: not installing).'; return }
    if (-not $script:Online) { Write-Err 'FFmpeg missing and no internet access. Video rendering will not work.'; return }

    $sources = @(
        @{ L = 'FFmpeg release build (gyan.dev)';  U = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' },
        @{ L = 'FFmpeg build (BtbN, GitHub)';       U = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' }
    )
    $n = 0
    foreach ($s in $sources) {
        $n++
        Out-Line ("  --- FFmpeg source {0}/{1}: {2} ---" -f $n, $sources.Count, $s.L) 'Cyan'
        try {
            if (Install-FFmpegFromZip -Url $s.U -Label $s.L) {
                $v = Test-FFmpegDir $FFMPEG_BIN
                if ($v) {
                    $env:Path = "$FFMPEG_BIN;$env:Path"
                    Write-OK "FFmpeg installed: $FFMPEG_BIN"
                    Write-Dim $v
                    return
                }
                Write-Warn 'FFmpeg was extracted but does not run (possibly blocked by antivirus or missing C++ runtime).'
            }
        } catch { Write-Warn "FFmpeg source failed: $($_.Exception.Message)" }
    }

    $w = Get-Command winget -ErrorAction SilentlyContinue
    if ($w) {
        Out-Line '  --- FFmpeg fallback: winget (Gyan.FFmpeg) ---' 'Cyan'
        $filter = { param($l) if ($l -match '^[\s\-\\|/]*$') { return $null } else { return @{ Text = $l.Trim(); Color = 'Gray' } } }
        $r = Invoke-Native -FilePath $w.Source -ArgList @('install', '--id', 'Gyan.FFmpeg', '-e', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity') -Label 'winget install FFmpeg' -Live -LineFilter $filter -ShowNet -TimeoutSec 1800
        Update-PathFromRegistry
        if (Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue) { Write-OK 'FFmpeg installed via winget.'; return }
    }
    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($choco -and $script:IsAdmin) {
        Out-Line '  --- FFmpeg fallback: Chocolatey ---' 'Cyan'
        [void](Invoke-Native -FilePath $choco.Source -ArgList @('install', 'ffmpeg', '-y', '--no-progress') -Label 'choco install ffmpeg' -Live -TimeoutSec 1800)
        Update-PathFromRegistry
        if (Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue) { Write-OK 'FFmpeg installed via Chocolatey.'; return }
    }
    Write-Err 'FFmpeg could not be installed automatically. Video rendering will not work until it is.'
    Write-Hint 'Download a release build from https://www.gyan.dev/ffmpeg/builds/ and copy ffmpeg.exe + ffprobe.exe into tools\ffmpeg\bin\'
}

# =============================================================================
#  STEP 5 - VIRTUAL ENVIRONMENT
# =============================================================================
function Test-VenvHealth {
    $res = [pscustomobject]@{ Ok = $false; Version = $null; Reason = '' }
    if (-not (Test-Path -LiteralPath $VENV_PYTHON)) { $res.Reason = 'python.exe missing'; return $res }
    $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @('-c', "import sys,ssl;print('VENV_OK|%d.%d' % sys.version_info[:2])") -Quiet -TimeoutSec 40
    $line = @($r.Output | Where-Object { $_ -match '^VENV_OK\|' }) | Select-Object -First 1
    if ($r.ExitCode -ne 0 -or -not $line) { $res.Reason = (Get-RootCause $r.Output); return $res }
    $res.Version = [version](($line -split '\|')[1])
    $p = Invoke-Native -FilePath $VENV_PYTHON -ArgList @('-m', 'pip', '--version') -Quiet -TimeoutSec 40
    if ($p.ExitCode -ne 0) { $res.Reason = 'pip missing'; return $res }
    if ($res.Version -lt $PY_MIN -or $res.Version.Minor -gt $PY_MAX_MINOR) { $res.Reason = "unsupported Python $($res.Version) in venv"; return $res }
    $res.Ok = $true
    return $res
}

function Remove-VenvSafely {
    for ($i = 1; $i -le 3; $i++) {
        try { Remove-Item -LiteralPath $VENV_DIR -Recurse -Force -ErrorAction Stop; return $true } catch { Write-Dim "Delete attempt $i failed: $($_.Exception.Message)"; Start-Sleep -Seconds 2 }
    }
    try { & cmd.exe /c "rmdir /s /q `"$VENV_DIR`"" 2>&1 | Out-Null } catch { }
    if (-not (Test-Path -LiteralPath $VENV_DIR)) { return $true }
    try {
        $new = "$VENV_DIR.old-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Rename-Item -LiteralPath $VENV_DIR -NewName (Split-Path -Leaf $new) -ErrorAction Stop
        Write-Warn "Could not delete old .venv (files locked); renamed it to $(Split-Path -Leaf $new). You can delete it later."
        return $true
    } catch { return $false }
}

function Step-VirtualEnv {
    if ($script:NoInstall) {
        if (Test-Path -LiteralPath $VENV_DIR) {
            $h = Test-VenvHealth
            if ($h.Ok) { $script:VenvOk = $true; Write-OK "Existing .venv is healthy (Python $($h.Version))" }
            else { Write-Err ".venv is unusable: $($h.Reason) (verify-only mode: not repairing)." }
        } else { Write-Err '.venv does not exist (verify-only mode: not creating it).' }
        return
    }
    if ($ForceVenv -and (Test-Path -LiteralPath $VENV_DIR)) {
        Write-Warn '-ForceVenv specified: recreating .venv.'
        if (-not (Remove-VenvSafely)) { Write-Fatal 'Could not remove existing .venv.' 'Close any terminals/editors using it (or reboot) and re-run.' }
    }
    if (Test-Path -LiteralPath $VENV_DIR) {
        Write-Info 'Checking existing .venv...'
        $h = Test-VenvHealth
        if ($h.Ok) { $script:VenvOk = $true; Write-OK "Existing .venv is healthy (Python $($h.Version))"; return }
        Write-Warn ".venv is unusable ($($h.Reason)). Recreating it..."
        if (-not (Remove-VenvSafely)) { Write-Fatal 'Could not remove the corrupted .venv.' 'Close any programs using it (or reboot), delete the .venv folder manually and re-run.' }
        Write-Info 'Removed old .venv'
    }

    $filter = { param($l) return @{ Text = $l.Trim(); Color = 'Gray' } }
    Write-Info "Creating virtual environment with $($script:PythonExe) ..."
    $r = Invoke-Native -FilePath $script:PythonExe -ArgList @('-m', 'venv', $VENV_DIR) -Label 'Creating virtual environment' -Live -LineFilter $filter -TimeoutSec 600
    $h = Test-VenvHealth
    if (-not $h.Ok) {
        Write-Warn "Standard venv creation failed ($($r.ExitCode): $(Get-RootCause $r.Output)). Trying fallback: venv without pip + get-pip.py"
        if (Test-Path -LiteralPath $VENV_DIR) { [void](Remove-VenvSafely) }
        $r = Invoke-Native -FilePath $script:PythonExe -ArgList @('-m', 'venv', '--without-pip', $VENV_DIR) -Label 'Creating venv (no pip)' -Live -LineFilter $filter -TimeoutSec 600
        if (Test-Path -LiteralPath $VENV_PYTHON) {
            $gp = Join-Path $DL_DIR 'get-pip.py'
            if (Save-Download -Urls @('https://bootstrap.pypa.io/get-pip.py') -OutFile $gp -Label 'get-pip.py' -MinBytes 100KB) {
                [void](Invoke-Native -FilePath $VENV_PYTHON -ArgList @($gp, '--no-warn-script-location') -Label 'Bootstrapping pip' -Live -LineFilter $filter -ShowNet -TimeoutSec 600)
            }
        }
        $h = Test-VenvHealth
    }
    if (-not $h.Ok) {
        Write-Fatal "Virtual environment could not be created ($($h.Reason))." 'Delete the .venv folder, make sure antivirus is not blocking python.exe, and re-run.'
    }
    $script:VenvOk = $true
    Write-OK "Virtual environment ready (Python $($h.Version))"
    if (Test-Path -LiteralPath $DEPS_MARKER) { Remove-Item -LiteralPath $DEPS_MARKER -Force -ErrorAction SilentlyContinue }
}

# =============================================================================
#  STEP 6 - DEPENDENCIES
# =============================================================================
$script:PipFilter = {
    param($l)
    if ($l -match '^\s*Requirement already satisfied') { return $null }
    if ($l -match '^\s*(ERROR|error:)')          { return @{ Text = $l.Trim(); Color = 'Red' } }
    if ($l -match '^\s*WARNING')                 { return @{ Text = $l.Trim(); Color = 'Yellow' } }
    if ($l -match '^\s*Collecting ') {
        $script:PipStats.Collected++
        $t = ($l.Trim() -replace '\s*\(from .*\)\s*$', '')
        return @{ Text = ("[{0,3}] {1}" -f $script:PipStats.Collected, $t); Color = 'Gray' }
    }
    if ($l -match '^\s*INFO: pip is looking at multiple versions of (\S+)') { return @{ Text = ("resolving a version conflict for {0} (pip is backtracking - can add a few minutes, this is normal)" -f $Matches[1]); Color = 'Yellow' } }
    if ($l -match '^\s*INFO: This is taking longer than usual') { return @{ Text = 'pip is still resolving dependencies - please be patient'; Color = 'Yellow' } }
    if ($l -match '^\s*Downloading ')            { return @{ Text = $l.Trim(); Color = 'Cyan' } }
    if ($l -match '^\s*(Installing collected|Successfully installed|Successfully uninstalled|Building|Preparing|Uninstalling|Attempting uninstall|Found existing|Obtaining|Getting requirements|Created wheel)') {
        return @{ Text = (Limit-Text $l.Trim() 200); Color = 'Green' }
    }
    return $null
}

function Get-PipDiagnosis {
    param([string[]]$Lines)
    $t = ($Lines -join "`n")
    if ($t -match 'No matching distribution found|Could not find a version that satisfies') { return 'No compatible wheel was found: the Python version/architecture may be unsupported by a pinned package, or the index was unreachable.' }
    if ($t -match 'Read timed out|ConnectionError|Connection aborted|Max retries exceeded|NewConnectionError|Temporary failure|getaddrinfo|ProxyError') { return 'Network problem while talking to PyPI (timeout / proxy / firewall). Check the connection and retry.' }
    if ($t -match 'SSLError|CERTIFICATE_VERIFY_FAILED') { return 'TLS/SSL failure - often a corporate proxy or a wrong system clock. Check date/time and proxy certificates.' }
    if ($t -match 'WinError 5\b|WinError 32|Permission denied|Access is denied') { return 'A file was locked or access denied (antivirus / running python.exe). Close other Python processes, pause antivirus, retry.' }
    if ($t -match 'No space left|OSError: \[Errno 28\]|not enough space') { return 'Out of disk space.' }
    if ($t -match 'WinError 206|filename or extension is too long|No such file or directory.*site-packages') { return 'Windows path-length limit hit. Move the project to a shorter path (e.g. C:\yt) or enable long paths.' }
    if ($t -match 'Microsoft Visual C\+\+ 14\.0|error: command .cl\.exe. failed|Failed building wheel') { return 'A package tried to compile from source (no prebuilt wheel). Install Visual Studio Build Tools or use a supported Python version (3.11).' }
    if ($t -match 'hash|HASH') { return 'Package hash mismatch - the download was corrupted. Retrying with --no-cache-dir usually fixes it.' }
    return ''
}

function Invoke-PipRetry {
    <# $Strategies: array of extra-arg arrays, one per attempt (last one repeats). #>
    param([string[]]$PipArgs, [string]$Label, [object[]]$Strategies = @(@()), [int]$Attempts = 3, [int]$TimeoutSec = 3600, [string]$WorkDir = '')
    if (-not $WorkDir) { $WorkDir = $PROJECT_ROOT }
    if ($Strategies.Count -eq 0) { $Strategies = @(, @()) }
    $last = $null
    for ($a = 1; $a -le $Attempts; $a++) {
        $extra = @($Strategies[[Math]::Min($a, $Strategies.Count) - 1])
        $script:PipStats.Collected = 0
        Out-Line ("  >> {0}  (attempt {1}/{2})" -f $Label, $a, $Attempts) 'White'
        $rest = @($PipArgs | Select-Object -Skip 1)
        $all = @('-m', 'pip', $PipArgs[0]) + @($script:PipExtra) + $rest + $extra + @('--no-warn-script-location', '--default-timeout', '60', '--retries', '5')
        $last = Invoke-Native -FilePath $VENV_PYTHON -ArgList $all -Label $Label -Live -LineFilter $script:PipFilter -ShowNet -StallSec 900 -TimeoutSec $TimeoutSec -WorkDir $WorkDir
        if ($last.ExitCode -eq 0) { return $last }
        $why = Get-RootCause $last.Output
        if ($last.Stalled)  { $why = 'no output and no network traffic for 15 minutes (stalled); the process was restarted' }
        if ($last.TimedOut) { $why = 'overall time limit exceeded' }
        Write-Warn ("{0} failed on attempt {1} (exit {2}): {3}" -f $Label, $a, $last.ExitCode, $why)
        $dx = Get-PipDiagnosis $last.Output
        if ($dx) { Write-Hint $dx }
        if ($a -lt $Attempts) { Write-Info 'Waiting 5 s, then retrying with a more conservative strategy...'; Start-Sleep -Seconds 5 }
    }
    return $last
}

function Get-RequirementLines {
    $out = @()
    foreach ($l in (Get-Content -LiteralPath $REQ_FILE -ErrorAction Stop)) {
        $t = ($l -replace '\s+#.*$', '').Trim()
        if (-not $t -or $t.StartsWith('#') -or $t.StartsWith('-')) { continue }
        $out += $t
    }
    return $out
}

function Install-RequirementsIndividually {
    $reqs = @(Get-RequirementLines)
    $failed = @(); $i = 0
    foreach ($r in $reqs) {
        $i++
        Out-Line ("  [{0}/{1}] pip install {2}" -f $i, $reqs.Count, $r) 'White'
        $res = Invoke-PipRetry -PipArgs @('install', $r, '--prefer-binary', '--progress-bar', 'off') -Label "install $r" -Strategies @(@(), @('--no-cache-dir')) -Attempts 2
        if ($res.ExitCode -ne 0) { $failed += $r }
    }
    return $failed
}

function Get-NvidiaSmi {
    $c = Get-Command nvidia-smi -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    foreach ($p in @((Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'), (Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe'))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Install-CudaTorch {
    param([string]$SmiPath)
    $drv = $null
    if ($SmiPath) {
        $r = Invoke-Native -FilePath $SmiPath -Quiet -TimeoutSec 30
        $m = [regex]::Match(($r.Output -join "`n"), 'CUDA Version:\s*(\d+)\.(\d+)')
        if ($m.Success) { $drv = [version]("{0}.{1}" -f $m.Groups[1].Value, $m.Groups[2].Value); Write-Info "NVIDIA driver supports CUDA $drv" }
    }
    $all = @(@{ T = 'cu128'; V = [version]'12.8' }, @{ T = 'cu126'; V = [version]'12.6' }, @{ T = 'cu124'; V = [version]'12.4' }, @{ T = 'cu121'; V = [version]'12.1' })
    if ($CudaIndex) { $tags = @($CudaIndex) }
    elseif ($drv)   { $tags = @($all | Where-Object { $_.V -le $drv } | ForEach-Object { $_.T }) }
    else            { $tags = @($all | ForEach-Object { $_.T }) }
    if ($tags.Count -eq 0) { Write-Warn "Driver CUDA $drv is older than any supported PyTorch CUDA build - using CPU PyTorch. Update the NVIDIA driver for GPU support."; return $false }

    foreach ($tag in $tags) {
        Write-Info "Trying PyTorch CUDA build '$tag' (large download, several GB)..."
        $res = Invoke-PipRetry -PipArgs @('install', '--upgrade', 'torch', '--index-url', "https://download.pytorch.org/whl/$tag", '--progress-bar', 'off') -Label "PyTorch ($tag)" -Strategies @(@(), @('--no-cache-dir')) -Attempts 2 -TimeoutSec 5400
        if ($res.ExitCode -eq 0) {
            $chk = Invoke-Native -FilePath $VENV_PYTHON -ArgList @('-c', "import torch;print('TORCHINFO|%s|%s' % (torch.__version__, torch.cuda.is_available()))") -Quiet -TimeoutSec 120
            $ti = @($chk.Output | Where-Object { $_ -match '^TORCHINFO\|' }) | Select-Object -First 1
            if ($ti -and $ti -match '\|True$') { Write-OK "PyTorch CUDA build installed and GPU is usable ($($ti -replace 'TORCHINFO\|','' -replace '\|',', CUDA='))"; $script:GpuTorch = $true; return $true }
            Write-Warn "PyTorch '$tag' installed but CUDA is not usable here; trying the next build."
        }
    }
    Write-Warn 'No CUDA build of PyTorch could be installed. Falling back to CPU PyTorch.'
    [void](Invoke-PipRetry -PipArgs @('install', '--force-reinstall', '--no-deps', 'torch', '--progress-bar', 'off') -Label 'PyTorch (CPU restore)' -Attempts 2)
    return $false
}

function Get-DepsHash {
    $sha = [Security.Cryptography.SHA256]::Create()
    $sb = New-Object System.Text.StringBuilder
    foreach ($f in @($REQ_FILE, $PYPROJECT)) {
        if (Test-Path -LiteralPath $f) { [void]$sb.Append([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($f)))) }
    }
    [void]$sb.Append("|py$($script:PythonVer)|gpu$GPU")
    return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sb.ToString()))).Replace('-', '')
}

function Step-Dependencies {
    if ($script:NoInstall) { Write-Info 'Skipped (verify-only mode).'; return }
    if (-not $script:VenvOk) { Write-Err 'Virtual environment unavailable - skipping dependency installation.'; return }
    $script:PipExtra = @()
    if ($PipIndexUrl) { $script:PipExtra += @('--index-url', $PipIndexUrl); Write-Info "Using custom package index: $PipIndexUrl" }
    $wheelhouse = Join-Path $PROJECT_ROOT 'wheelhouse'
    if (Test-Path -LiteralPath $wheelhouse) {
        $script:PipExtra += @('--find-links', $wheelhouse)
        Write-Info "Local wheelhouse detected - pip will use it first: $wheelhouse"
        if (-not $script:Online) { $script:PipExtra += '--no-index'; Write-Info 'Offline: installing from the wheelhouse only.' }
    }
    $reqForPip = Get-PipRequirementsPath
    $hash = Get-DepsHash
    if (-not $Force -and (Test-Path -LiteralPath $DEPS_MARKER)) {
        $old = (Get-Content -LiteralPath $DEPS_MARKER -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($old -eq $hash) {
            Write-OK 'Dependencies unchanged since the last successful install - skipping (use -Force to reinstall).'
            Write-Info 'Import verification (step 11) will still confirm everything works.'
            return
        }
    }
    $nreq = @(Get-RequirementLines).Count
    Write-Info "requirements.txt lists $nreq direct packages; pip will resolve dependencies and download wheels."
    Write-Info 'First install downloads ~1-2 GB (PyTorch, OpenCV, SciPy...) - on slow links this can take 15-30 minutes.'
    Write-Info 'You will see "Collecting"/"Downloading" lines plus a live spinner, elapsed time and network speed.'

    # 1. pip tooling
    $up = Invoke-PipRetry -PipArgs @('install', '--upgrade', 'pip', 'setuptools', 'wheel', '--progress-bar', 'off') -Label 'Upgrade pip / setuptools / wheel' -Attempts 3 -TimeoutSec 900
    if ($up.ExitCode -eq 0) { Write-OK 'pip / setuptools / wheel upgraded' } else { Write-Warn 'pip upgrade failed - continuing with the existing pip.' }

    # 2. requirements
    $reqOk = $true
    $res = Invoke-PipRetry -PipArgs @('install', '-r', $reqForPip, '--progress-bar', 'off') -Label 'Install requirements.txt' -Strategies @(@(), @('--no-cache-dir', '--prefer-binary'), @('--no-cache-dir', '--prefer-binary')) -Attempts 3 -TimeoutSec 5400
    if ($res.ExitCode -eq 0) { Write-OK 'requirements.txt installed' }
    else {
        Write-Warn 'Bulk install failed. Falling back to installing each package individually to isolate the problem...'
        $failed = @(Install-RequirementsIndividually)
        if ($failed.Count -eq 0) { Write-OK 'All packages installed individually (bulk resolver failed, per-package succeeded).' }
        else {
            $reqOk = $false
            foreach ($f in $failed) { Write-Err "Package failed to install: $f" }
        }
    }

    # 3. editable install
    $editOk = $true
    if (Test-Path -LiteralPath $PYPROJECT) {
        $res = Invoke-PipRetry -PipArgs @('install', '-e', '.', '--progress-bar', 'off') -Label 'Install shorts_engine (editable)' -Strategies @(@(), @('--no-build-isolation'), @('--no-build-isolation', '--no-deps')) -Attempts 3 -TimeoutSec 1200
        if ($res.ExitCode -eq 0) { Write-OK 'shorts_engine installed in editable mode' }
        else { $editOk = $false; Write-Err 'Editable install of shorts_engine failed.' }
    } else { Write-Warn 'Skipped editable install (no pyproject.toml).' }

    # 4. GPU build of torch
    $smi = Get-NvidiaSmi
    $gpuName = $null
    if ($smi) {
        $q = Invoke-Native -FilePath $smi -ArgList @('--query-gpu=name', '--format=csv,noheader') -Quiet -TimeoutSec 30
        $gpuName = @($q.Output | Where-Object { $_.Trim() }) | Select-Object -First 1
    }
    if ($gpuName) { Write-OK "NVIDIA GPU detected: $gpuName" }
    if ($GPU -or $gpuName) {
        Write-Info 'Note: GPU transcription with faster-whisper additionally needs CUDA 12 cuBLAS/cuDNN DLLs (pip: nvidia-cublas-cu12, nvidia-cudnn-cu12) - CPU mode always works.'
        $go = $true
        if (-not $GPU) { $go = Read-YesNo 'Install the CUDA build of PyTorch (multi-GB download, much faster transcription)?' $true }
        if ($go) { [void](Install-CudaTorch -SmiPath $smi) } else { Write-Info 'Keeping CPU PyTorch.' }
    } else {
        Write-Info 'No NVIDIA GPU detected - using CPU PyTorch (re-run with -GPU to force a CUDA install).'
    }

    if ($reqOk -and $editOk) {
        try { [IO.File]::WriteAllText($DEPS_MARKER, (Get-DepsHash), $script:Utf8NoBom) } catch { }
    }
}

# =============================================================================
#  STEP 7 - .ENV
# =============================================================================
function New-RandomKey {
    param([int]$Bytes = 32)
    $buf = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($buf) -replace '[+/=]', '')
}

function Get-DotEnvMap {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($l in (Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)) {
        if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $v = $Matches[2]
            if (-not ($v.StartsWith('"') -or $v.StartsWith("'"))) { $v = ($v -replace '\s+#.*$', '').Trim() }
            if ($v.Length -ge 2 -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) { $v = $v.Substring(1, $v.Length - 2) }
            $map[$Matches[1]] = $v
        }
    }
    return $map
}

function Get-TemplateMap {
    foreach ($n in @('.env.example', '.env.sample', 'env.example', 'example.env')) {
        $p = Join-Path $PROJECT_ROOT $n
        if (Test-Path -LiteralPath $p -ErrorAction SilentlyContinue) { return (Get-DotEnvMap $p) }
    }
    return @{}
}

function Update-InsecureSecrets {
    <# Replaces placeholder secrets with fresh random ones. A value counts as a placeholder when it is a known
       placeholder literal, looks like one (dev_/local_/changeme...), or is IDENTICAL to the value shipped in
       .env.example for a secret-named key. -All rotates every secret-named key regardless.
       Returns @{ Text; Changed } #>
    param([string]$Text, [hashtable]$Template = @{}, [switch]$All)
    $changed = New-Object System.Collections.ArrayList
    $nl = "`n"; if ($Text.Contains("`r`n")) { $nl = "`r`n" }
    $out = New-Object System.Collections.ArrayList
    foreach ($line in ($Text -split "\r?\n")) {
        $m = [regex]::Match($line, '^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(["'']?)(.*?)(["'']?)\s*$')
        if ($m.Success -and $m.Groups[2].Value -match '(?i)(SECRET|MASTER|SALT|SESSION|KDF|ENCRYPT)' -and $m.Groups[2].Value -notmatch '(?i)API|TOKEN_URL|CLIENT') {
            $name = $m.Groups[2].Value
            $val = $m.Groups[5].Value
            $hit = $false
            if ($val) {
                if ($All) { $hit = $true }
                elseif ($PLACEHOLDER_SECRETS -contains $val) { $hit = $true }
                elseif ($val.Length -ge 16 -and $val -match '(?i)(dev_|_dev|local_|_local|placeholder|changeme|change_me|default|insecure|not_secure|example|replace_me|yt_shorts_)') { $hit = $true }
                elseif ($Template.ContainsKey($name) -and [string]$Template[$name] -ceq $val -and $val.Length -ge 8) { $hit = $true }
            }
            if ($hit) {
                $line = $m.Groups[1].Value + $name + $m.Groups[3].Value + $m.Groups[4].Value + (New-RandomKey 32) + $m.Groups[6].Value
                [void]$changed.Add($name)
            }
        }
        [void]$out.Add($line)
    }
    return @{ Text = ($out -join $nl); Changed = @($changed) }
}

function ConvertTo-EnvJsonList {
    <# Returns the normalised .env value for a list-typed setting, or $null when it is already valid / cannot be fixed. #>
    param([string]$Raw)
    $v = $Raw.Trim()
    if (-not ($v.StartsWith('"') -or $v.StartsWith("'"))) { $v = ($v -replace '\s+#.*$', '').Trim() }
    $inner = $v
    if ($inner.Length -ge 2 -and (($inner.StartsWith("'") -and $inner.EndsWith("'")) -or ($inner.StartsWith('"') -and $inner.EndsWith('"')))) { $inner = $inner.Substring(1, $inner.Length - 2) }
    if ($inner.Trim() -eq '') { return '[]' }
    if ($inner.TrimStart().StartsWith('[')) {
        # PowerShell's JSON parser is more lenient than Python's: check the strict rules ourselves
        $stripped = [regex]::Replace($inner, '"(?:[^"\\]|\\.)*"', '""')
        $bad = ($stripped.Contains("'") -or ($stripped -match ',\s*\]'))
        if (-not $bad) { try { [void](ConvertFrom-Json $inner); return $null } catch { } }   # already strict JSON
        $alt = $inner
        if ($stripped.Contains("'")) { $alt = $alt -replace "'", '"' }                       # python-style ['a','b']
        $alt = $alt -replace ',\s*\]', ']'                                                    # trailing comma
        try { [void](ConvertFrom-Json $alt); return ("'" + $alt + "'") } catch { return $null }
    }
    $items = @($inner -split '[,;\r\n]+' | ForEach-Object { $_.Trim().Trim('"').Trim("'") } | Where-Object { $_ -and $_ -notmatch '(?i)^(your|put|add|paste|xxx|changeme|<)' })
    $json = '[' + ((@($items | ForEach-Object { '"' + ($_ -replace '\\', '\\' -replace '"', '\"') + '"' })) -join ',') + ']'
    return ("'" + $json + "'")
}

function Repair-EnvListFields {
    <# pydantic-settings needs list-typed fields to be JSON. "KEY=" (empty) or "KEY=abc,def" makes the whole app crash at import. #>
    param([string]$Text, [string]$OnlyName = '')
    $fixed = New-Object System.Collections.ArrayList
    $nl = "`n"; if ($Text.Contains("`r`n")) { $nl = "`r`n" }
    $out = New-Object System.Collections.ArrayList
    # proactive pass: only *_KEYS; when pydantic itself named the failing field ($OnlyName) we may fix any list-like name
    $listName = '(_KEYS)'
    if ($OnlyName) { $listName = '(_KEYS|_LIST|_IDS|_ORIGINS|_HOSTS|_URLS|_TAGS|_PRESETS|_MODELS|_DOMAINS|_EMAILS)' }
    foreach ($line in ($Text -split "\r?\n")) {
        $m = [regex]::Match($line, '^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)\s*$')
        if ($m.Success -and $m.Groups[2].Value -match ($listName + '$') -and (-not $OnlyName -or $m.Groups[2].Value -eq $OnlyName)) {
            $raw = $m.Groups[4].Value
            $norm = ConvertTo-EnvJsonList $raw
            if ($null -ne $norm -and $norm -ne $raw) {
                $line = $m.Groups[1].Value + $m.Groups[2].Value + $m.Groups[3].Value + $norm
                [void]$fixed.Add($m.Groups[2].Value)
            }
        }
        [void]$out.Add($line)
    }
    return @{ Text = ($out -join $nl); Fixed = @($fixed) }
}

function Backup-EnvFile {
    if ($script:EnvBackedUp) { return }
    $bak = "$ENV_FILE.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Copy-Item -LiteralPath $ENV_FILE -Destination $bak -Force
    $script:EnvBackedUp = $true
    Write-Info "Backup of your .env: $(Split-Path -Leaf $bak)  (secrets are never written to the log)"
}

function Invoke-SecretRotation {
    param([bool]$DefaultYes = $false, [switch]$All)
    $text = [IO.File]::ReadAllText($ENV_FILE)
    $res = Update-InsecureSecrets $text (Get-TemplateMap) -All:$All
    if ($res.Changed.Count -eq 0) { return $false }
    $dbs = @()
    try { $dbs = @(Get-ChildItem -LiteralPath (Join-Path $PROJECT_ROOT 'storage') -Recurse -File -Include '*.db', '*.sqlite', '*.sqlite3' -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 0 }) } catch { }
    $go = $true
    if ($dbs.Count -gt 0) {
        Write-Warn 'An existing database was found. Anything in it encrypted with the OLD master key (saved API keys / OAuth tokens) becomes unreadable after rotation. Your .env is backed up so you can restore the old key.'
        $go = Read-YesNo 'Rotate the secrets now?' $DefaultYes
    }
    if (-not $go) { return $false }
    Backup-EnvFile
    [IO.File]::WriteAllText($ENV_FILE, $res.Text, $script:Utf8NoBom)
    Write-OK ('Secrets rotated with fresh random values: ' + ((@($res.Changed | Select-Object -Unique)) -join ', '))
    return $true
}

$SETTINGS_PROBE_PY = @'
import sys, os
root = sys.argv[1]
os.chdir(root)
sys.path.insert(0, root)
try:
    from shorts_engine.config.settings import settings
    print("SETTINGS_OK")
except BaseException as e:
    msg = "%s: %s" % (type(e).__name__, e)
    print("SETTINGS_FAIL|" + msg.replace("\r", " ").replace("\n", " // "))
    sys.exit(3)
'@

function Invoke-SettingsDoctor {
    <# Loads the app's real settings in the venv, and repairs the known .env problems automatically. #>
    if (-not $script:VenvOk) { return $false }
    New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
    $probe = Join-Path $TMP_DIR 'settings_probe.py'
    [IO.File]::WriteAllText($probe, $SETTINGS_PROBE_PY, $script:Utf8NoBom)
    $rotated = $false
    try {
        for ($i = 1; $i -le 6; $i++) {
            $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @($probe, $PROJECT_ROOT) -Label 'Validating application settings' -TimeoutSec 180 -Quiet
            $line = @($r.Output | Where-Object { $_ -match '^SETTINGS_(OK|FAIL)' }) | Select-Object -Last 1
            if ($line -and $line -match '^SETTINGS_OK') { Write-OK 'Application settings load correctly (.env is valid)'; return $true }
            $msg = ''
            if ($line) { $msg = ($line -replace '^SETTINGS_FAIL\|', '') } else { $msg = Get-RootCause $r.Output }

            if ($msg -match 'INSECURE STARTUP' -and -not $rotated) {
                if ($script:NoInstall) { Write-Err 'The app refuses to start: placeholder secrets in .env (verify-only mode: not rotating).'; return $false }
                Write-Warn 'The app refuses to start with default/placeholder secrets - rotating them now.'
                $ok = Invoke-SecretRotation -DefaultYes $true
                if (-not $ok) { $ok = Invoke-SecretRotation -DefaultYes $true -All }
                $rotated = $true
                if ($ok) { continue }
                Write-Err 'Placeholder secrets remain in .env - the app will not start.'
                Write-Hint 'Replace MASTER_ENCRYPTION_KEY / DASHBOARD_SECRET_KEY / ENCRYPTION_SALT with random 32+ character strings (python -c "import secrets;print(secrets.token_urlsafe(32))").'
                return $false
            }
            if ($msg -match 'error parsing value for field "([A-Za-z0-9_]+)"') {
                $field = $Matches[1]
                if ($script:NoInstall) { Write-Err "Setting $field in .env is not valid JSON (verify-only mode: not repairing)."; return $false }
                $res = Repair-EnvListFields ([IO.File]::ReadAllText($ENV_FILE)) $field
                if ($res.Fixed.Count -gt 0) {
                    Backup-EnvFile
                    [IO.File]::WriteAllText($ENV_FILE, $res.Text, $script:Utf8NoBom)
                    Write-OK "Repaired .env value of $field (a list setting must be valid JSON such as [] or [""key""]; your keys were kept)."
                    continue
                }
                Write-Err "The value of $field in .env cannot be parsed and could not be repaired automatically."
                Write-Hint "Edit .env and set $field to a JSON list, e.g.  $field=[]   or   $field='[""your-key""]'"
                return $false
            }
            Write-Err ("Application settings do not load: " + (Limit-Text $msg 300))
            if ($msg -match 'validation error') { Write-Hint 'A value in .env has the wrong type or is missing. Compare it with .env.example.' }
            else { Write-Hint 'Run  .\.venv\Scripts\python.exe run.py dashboard  to see the full error.' }
            return $false
        }
    } finally { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
    Write-Err 'Application settings still fail after repeated automatic repairs.'
    return $false
}

function Step-EnvConfig {
    $script:EnvBackedUp = $false
    $tmplMap = Get-TemplateMap

    if (-not (Test-Path -LiteralPath $ENV_FILE)) {
        if ($script:NoInstall) { Write-Err '.env is missing (verify-only mode: not creating it).'; return }
        $tmpl = $null
        foreach ($n in @('.env.example', '.env.sample', 'env.example', 'example.env')) {
            $p = Join-Path $PROJECT_ROOT $n
            if (Test-Path -LiteralPath $p) { $tmpl = $p; break }
        }
        if (-not $tmpl) {
            Write-Err '.env is missing and no .env.example template exists - the app cannot be configured automatically.'
            Write-Hint 'Restore .env.example from the repository (or copy a working .env) and re-run.'
            return
        }
        Write-Info "Creating .env from $(Split-Path -Leaf $tmpl) with fresh cryptographic keys..."
        $res = Update-InsecureSecrets ([IO.File]::ReadAllText($tmpl)) $tmplMap
        $lf = Repair-EnvListFields $res.Text
        try { [IO.File]::WriteAllText($ENV_FILE, $lf.Text, $script:Utf8NoBom) } catch { Write-Err "Could not write .env: $($_.Exception.Message)"; return }
        Write-OK '.env created with fresh 256-bit random secrets'
        if ($lf.Fixed.Count -gt 0) { Write-OK ('Normalised list settings to valid JSON: ' + ($lf.Fixed -join ', ')) }
    } else {
        $sz = (Get-Item -LiteralPath $ENV_FILE -Force).Length
        Write-Info ".env exists ($sz bytes) - checking encoding, list settings and secrets..."
        $bytes = [IO.File]::ReadAllBytes($ENV_FILE)
        $badEnc = ''
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $badEnc = 'UTF-8 with BOM' }
        elseif ($bytes.Length -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) { $badEnc = 'UTF-16' }
        $text = [IO.File]::ReadAllText($ENV_FILE)
        if ($badEnc) {
            Write-Warn ".env is saved as $badEnc - python-dotenv then mis-reads the first variable name."
            if ($script:NoInstall) { Write-Err '.env encoding needs fixing (verify-only mode: not changing it).' }
            else {
                Backup-EnvFile
                [IO.File]::WriteAllText($ENV_FILE, $text, $script:Utf8NoBom)
                Write-OK '.env re-saved as plain UTF-8 (no BOM)'
            }
        }

        # 1) list-typed settings must be JSON (empty "KEY=" crashes the app at import)
        $lf = Repair-EnvListFields $text
        if ($lf.Fixed.Count -gt 0) {
            Write-Warn ('These .env list settings are not valid JSON and would crash the app at startup: ' + ($lf.Fixed -join ', '))
            if ($script:NoInstall) { Write-Err 'List settings need repair (verify-only mode: not changing .env).' }
            else {
                Backup-EnvFile
                [IO.File]::WriteAllText($ENV_FILE, $lf.Text, $script:Utf8NoBom)
                $text = $lf.Text
                Write-OK 'List settings normalised to valid JSON (existing keys preserved)'
            }
        }

        # 2) placeholder secrets
        $res = Update-InsecureSecrets $text $tmplMap
        $envMap0 = Get-DotEnvMap $ENV_FILE
        $isProd = ($envMap0.ContainsKey('ENV') -and [string]$envMap0['ENV'] -match '(?i)^prod')
        if ($res.Changed.Count -eq 0) {
            Write-OK '.env secrets are not template defaults - your values were preserved untouched'
        } elseif ($script:NoInstall) {
            Write-Warn ('Secrets still equal the template defaults: ' + ((@($res.Changed | Select-Object -Unique)) -join ', ') + '  (verify-only mode: not rotating)')
        } elseif ($RotateSecrets -or $isProd) {
            if ($isProd) { Write-Warn 'ENV=production with template-default secrets - rotating them.' }
            [void](Invoke-SecretRotation -DefaultYes ([bool]$isProd))
        } else {
            Write-Info ('Secrets still equal the template defaults: ' + ((@($res.Changed | Select-Object -Unique)) -join ', '))
            Write-Info 'That is fine for local development. Rotate them before production with:  setup.bat -RotateSecrets'
            Write-Info '(If the app ever refuses to start because of them, setup rotates them automatically.)'
        }
    }

    $map = Get-DotEnvMap $ENV_FILE
    if ($map.ContainsKey('DASHBOARD_PORT')) { $pv = 0; if ([int]::TryParse($map['DASHBOARD_PORT'], [ref]$pv) -and $pv -gt 0 -and $pv -lt 65536) { $script:DashPort = $pv } }
    $script:TestPort = $script:DashPort
    $status = @()
    foreach ($k in @('GROQ_API_KEYS', 'GEMINI_API_KEYS', 'OPENROUTER_API_KEYS')) {
        $v = ''; if ($map.ContainsKey($k)) { $v = [string]$map[$k] }
        $set = ($v.Trim().Length -gt 0 -and $v.Trim() -notmatch '(?i)^(\[\s*\]|""|''''|none|null|false)$' -and $v -notmatch '(?i)^(your|put|add|paste|xxx|changeme|<)')
        $status += [pscustomobject]@{ Key = $k; Set = $set }
    }
    $script:ApiKeyStatus = $status
    if (-not (@($status | Where-Object { $_.Set }).Count)) {
        Out-Line '' 'Yellow' -NoLog
        Out-Line '  +-----------------------------------------------------------------+' 'Yellow'
        Out-Line '  |  ACTION REQUIRED: add at least one free AI API key to .env       |' 'Yellow'
        Out-Line '  |   GROQ_API_KEYS       https://console.groq.com/keys              |' 'Yellow'
        Out-Line '  |   GEMINI_API_KEYS     https://aistudio.google.com/app/apikey    |' 'Yellow'
        Out-Line '  |   OPENROUTER_API_KEYS https://openrouter.ai/keys                 |' 'Yellow'
        Out-Line '  |  Use a JSON list for each:  ["key1","key2"]   or empty:  []      |' 'Yellow'
        Out-Line '  +-----------------------------------------------------------------+' 'Yellow'
        Write-Info "File: $ENV_FILE"
    } else {
        Write-OK ('AI API keys configured: ' + ((@($status | Where-Object { $_.Set } | ForEach-Object { $_.Key })) -join ', '))
    }

    if ($script:VenvOk) { [void](Invoke-SettingsDoctor) }
}

# =============================================================================
#  STEP 8 - DIRECTORIES & LAUNCHERS
# =============================================================================
function Write-LauncherFile {
    param([string]$Path, [string]$Content)
    $marker = 'REM generated-by-setup.ps1'
    if (Test-Path -LiteralPath $Path) {
        $cur = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
        if ($cur -and -not $cur.Contains($marker)) { Write-Dim "Launcher exists and is user-modified; left untouched: $(Split-Path -Leaf $Path)"; return }
    }
    [IO.File]::WriteAllText($Path, ($Content -replace "(?<!\r)\n", "`r`n"), (New-Object System.Text.ASCIIEncoding))
    Write-OK "Launcher: tools\launchers\$(Split-Path -Leaf $Path)"
}

function Step-Directories {
    if ($script:NoInstall) { Write-Info 'Skipped (verify-only mode).'; return }
    $dirs = @('storage', 'storage\downloads', 'storage\outputs', 'storage\temp', 'storage\thumbnails', 'storage\models', 'storage\logs', 'storage\config',
              'assets', 'assets\sfx_library', 'assets\fonts', 'tools', 'tools\ffmpeg', 'tools\ffmpeg\bin', 'tools\launchers')
    $created = 0
    foreach ($rel in $dirs) {
        $full = Join-Path $PROJECT_ROOT $rel
        if (-not (Test-Path -LiteralPath $full)) {
            try {
                New-Item -ItemType Directory -Path $full -Force | Out-Null
                if ($rel -match '^(storage|assets)\\') { $gk = Join-Path $full '.gitkeep'; if (-not (Test-Path -LiteralPath $gk)) { [IO.File]::WriteAllText($gk, '') } }
                Write-OK "Created: $rel"; $created++
            } catch { Write-Err "Could not create $rel : $($_.Exception.Message)" }
        }
    }
    if ($created -eq 0) { Write-OK ("All {0} required folders already exist" -f $dirs.Count) }

    $winFont = Join-Path $env:SystemRoot 'Fonts\seguiemj.ttf'
    $dstFont = Join-Path $PROJECT_ROOT 'assets\fonts\seguiemj.ttf'
    if ((Test-Path -LiteralPath $winFont) -and -not (Test-Path -LiteralPath $dstFont)) {
        try { Copy-Item -LiteralPath $winFont -Destination $dstFont -Force; Write-OK 'Copied Windows emoji font to assets\fonts' } catch { Write-Warn "Could not copy emoji font: $($_.Exception.Message)" }
    }

    if (Test-Path -LiteralPath $RUN_PY) {
        foreach ($mode in @('dashboard', 'worker')) {
            $content = @"
@echo off
REM generated-by-setup.ps1  (safe to delete; regenerated by setup)
setlocal
pushd "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
set "PATH=%CD%\tools\ffmpeg\bin;%PATH%"
".venv\Scripts\python.exe" run.py $mode %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] $mode exited with code %RC%. See storage\logs for details.
    pause
)
popd
exit /b %RC%
"@
            Write-LauncherFile (Join-Path $LAUNCH_DIR "run_$mode.bat") $content
        }
    }
    Update-FfmpegPth
}

# =============================================================================
#  STEP 9 - DATABASE
# =============================================================================
function Step-RuntimeSettings {
    $dir = Split-Path -Parent $RUNTIME_CFG
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $default = "{`r`n  `"updated_at`": `"initial_setup`",`r`n  `"groq_api_keys`": [],`r`n  `"gemini_api_keys`": [],`r`n  `"openrouter_api_keys`": [],`r`n  `"openai_api_keys`": []`r`n}`r`n"
    if (Test-Path -LiteralPath $RUNTIME_CFG) {
        try {
            $raw = [IO.File]::ReadAllText($RUNTIME_CFG)
            $hadBom = $raw.Length -gt 0 -and $raw[0] -eq [char]0xFEFF
            $clean = $raw.TrimStart([char]0xFEFF)
            [void]($clean | ConvertFrom-Json)
            if ($hadBom) { [IO.File]::WriteAllText($RUNTIME_CFG, $clean, $script:Utf8NoBom); Write-OK 'runtime_settings.json: removed UTF-8 BOM (Python json would reject it)' }
            else { Write-Info 'runtime_settings.json is valid' }
            return
        } catch {
            $bak = "$RUNTIME_CFG.corrupt-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
            Copy-Item -LiteralPath $RUNTIME_CFG -Destination $bak -Force
            Write-Warn "runtime_settings.json was not valid JSON; backed up to $(Split-Path -Leaf $bak) and recreated."
        }
    }
    $tmp = "$RUNTIME_CFG.tmp"
    [IO.File]::WriteAllText($tmp, $default, $script:Utf8NoBom)
    Move-Item -LiteralPath $tmp -Destination $RUNTIME_CFG -Force
    Write-OK 'Created default runtime_settings.json'
}

function Step-Database {
    if ($script:NoInstall) { Write-Info 'Skipped (verify-only mode).'; return }
    if (-not $script:VenvOk) { Write-Err 'Virtual environment unavailable - skipping database initialisation.'; return }
    $show = { param($l) if ($l -match '(?i)\[error\]|error|exception|traceback|fail') { return @{ Text = $l.Trim(); Color = 'Yellow' } } else { return @{ Text = $l.Trim(); Color = 'Gray' } } }
    $ok = $false

    if (Test-Path -LiteralPath $SETUP_ENV_PY) {
        for ($attempt = 1; $attempt -le 2 -and -not $ok; $attempt++) {
            if ($attempt -eq 2) {
                Write-Info 'Re-validating settings, then retrying the database initialisation...'
                if (-not (Invoke-SettingsDoctor)) { break }
            }
            Write-Info "Running setup_environment.py (attempt $attempt/2) ..."
            $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @($SETUP_ENV_PY) -Label 'setup_environment.py' -Live -LineFilter $show -TimeoutSec 600
            # setup_environment.py prints "[ERROR] ..." but may still exit 0 - trust the text, not just the exit code
            $errLines = @($r.Output | Where-Object { $_ -match '\[ERROR\]|initialization failed|initialisation failed' })
            if ($r.ExitCode -eq 0 -and $errLines.Count -eq 0) { Write-OK 'Database and admin account initialised via setup_environment.py'; $ok = $true }
            else {
                $cause = Get-RootCause $r.Output
                if ($errLines.Count -gt 0) { $cause = $errLines[0].Trim() }
                Write-Warn ("setup_environment.py reported a failure (exit {0}): {1}" -f $r.ExitCode, $cause)
            }
        }
        if (-not $ok) {
            Write-Err 'Database could not be initialised - the dashboard will not work until this is fixed.'
            Write-Hint 'Fix the .env / settings problem reported above, then re-run setup.bat (it is safe to re-run).'
        }
    } else {
        Write-Info 'setup_environment.py not found - trying a direct init_db() call...'
        New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
        $tmpPy = Join-Path $TMP_DIR 'init_db_fallback.py'
        $code = @'
import sys, os
root = sys.argv[1]
sys.path.insert(0, root)
os.chdir(root)
try:
    from shorts_engine.db.session import init_db
    init_db()
    print("DB_OK")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("DB_FAIL: %s" % e)
    sys.exit(1)
'@
        [IO.File]::WriteAllText($tmpPy, $code, $script:Utf8NoBom)
        $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @($tmpPy, $PROJECT_ROOT) -Label 'init_db() fallback' -Live -LineFilter $show -TimeoutSec 300
        Remove-Item -LiteralPath $tmpPy -Force -ErrorAction SilentlyContinue
        if (@($r.Output | Where-Object { $_ -match 'DB_OK' }).Count -gt 0) { Write-OK 'Database initialised via init_db()'; $ok = $true }
        else { Write-Warn ("Database could not be initialised now: {0} (it will be created on first run)" -f (Get-RootCause $r.Output)) }
    }
    Step-RuntimeSettings
}

# =============================================================================
#  STEP 10 - PORT CHECK
# =============================================================================
function Get-PortOwner {
    param([int]$Port)
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    } catch { }
    try {
        $ns = & netstat.exe -ano -p tcp 2>$null
        foreach ($l in $ns) { if ($l -match ('^\s*TCP\s+\S+:{0}\s+\S+\s+LISTENING\s+(\d+)' -f $Port)) { return [int]$Matches[1] } }
    } catch { }
    return $null
}

function Test-PortFree {
    param([int]$Port)
    try {
        $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}

function Step-PortCheck {
    $port = $script:DashPort
    $owner = Get-PortOwner $port
    if ($null -eq $owner -and (Test-PortFree $port)) { Write-OK "Port $port is free"; return }
    $name = 'unknown'; $path = ''
    if ($owner) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc) { $name = $proc.ProcessName; try { $path = $proc.Path } catch { } }
        Write-Warn "Port $port is already in use by $name (PID $owner)"
    } else { Write-Warn "Port $port cannot be bound (reserved by Windows or in use)." }
    $protected = @('System', 'Idle', 'svchost', 'lsass', 'csrss', 'wininit', 'services', 'smss', 'winlogon', 'MsMpEng')
    $ours = ($path -and $path.StartsWith($PROJECT_ROOT, [StringComparison]::OrdinalIgnoreCase))
    if ($owner -and $owner -gt 4 -and ($protected -notcontains $name) -and -not $script:NoInstall) {
        $q = "Stop '$name' (PID $owner) to free port $port?"
        if (Read-YesNo $q $ours) {
            try { Stop-Process -Id $owner -Force; Start-Sleep -Seconds 1; Write-OK "Process stopped; port $port is free." } catch { Write-Warn "Could not stop the process: $($_.Exception.Message)" }
        }
    }
    if (-not (Test-PortFree $port)) {
        for ($p = $port + 1; $p -lt $port + 50; $p++) { if (Test-PortFree $p) { $script:TestPort = $p; break } }
        Write-Info "The smoke test will use port $($script:TestPort) instead. To change the real port set DASHBOARD_PORT in .env."
    }
}

# =============================================================================
#  STEP 11 - IMPORT VERIFICATION WITH SELF-HEALING
# =============================================================================
$VERIFY_PY = @'
import sys, time, traceback, importlib
for m in sys.argv[1:]:
    t = time.time()
    try:
        importlib.import_module(m)
        print("IMPORT|%s|OK|%.2f" % (m, time.time() - t), flush=True)
    except BaseException as e:
        tb = traceback.format_exc().strip().replace("\r", "").replace("\n", " || ")
        print("IMPORT|%s|FAIL|%s" % (m, tb), flush=True)
'@

$IMPORT_CHECKS = @(
    @{ M = 'fastapi';              L = 'FastAPI';              P = 'fastapi' },
    @{ M = 'uvicorn';              L = 'Uvicorn';              P = 'uvicorn[standard]' },
    @{ M = 'starlette';            L = 'Starlette';            P = 'starlette' },
    @{ M = 'jinja2';               L = 'Jinja2';               P = 'jinja2' },
    @{ M = 'httpx';                L = 'httpx';                P = 'httpx' },
    @{ M = 'pydantic';             L = 'Pydantic';             P = 'pydantic' },
    @{ M = 'pydantic_settings';    L = 'pydantic-settings';    P = 'pydantic-settings' },
    @{ M = 'sqlalchemy';           L = 'SQLAlchemy';           P = 'sqlalchemy' },
    @{ M = 'cryptography';         L = 'cryptography';         P = 'cryptography' },
    @{ M = 'numpy';                L = 'NumPy';                P = 'numpy<2' },
    @{ M = 'cv2';                  L = 'OpenCV (cv2)';         P = 'opencv-python-headless' },
    @{ M = 'PIL';                  L = 'Pillow';               P = 'pillow' },
    @{ M = 'scipy';                L = 'SciPy';                P = 'scipy' },
    @{ M = 'sklearn';              L = 'scikit-learn';         P = 'scikit-learn' },
    @{ M = 'yt_dlp';               L = 'yt-dlp';               P = 'yt-dlp' },
    @{ M = 'groq';                 L = 'Groq SDK';             P = 'groq' },
    @{ M = 'google.genai';         L = 'Google GenAI SDK';     P = 'google-genai' },
    @{ M = 'googleapiclient';      L = 'Google API client';    P = 'google-api-python-client' },
    @{ M = 'google_auth_oauthlib'; L = 'Google OAuth';         P = 'google-auth-oauthlib' },
    @{ M = 'typer';                L = 'Typer';                P = 'typer' },
    @{ M = 'rich';                 L = 'Rich';                 P = 'rich' },
    @{ M = 'psutil';               L = 'psutil';               P = 'psutil' },
    @{ M = 'onnxruntime';          L = 'ONNX Runtime';         P = 'onnxruntime' },
    @{ M = 'ctranslate2';          L = 'CTranslate2';          P = 'ctranslate2' },
    @{ M = 'torch';                L = 'PyTorch';              P = 'torch' },
    @{ M = 'faster_whisper';       L = 'Faster-Whisper';       P = 'faster-whisper' },
    @{ M = 'shorts_engine';        L = 'shorts_engine';        P = '-e .' }
)

function Test-Imports {
    <# Returns hashtable module -> @{Ok; Detail; Secs}. Runs everything in ONE python process, then re-tests missing results individually. #>
    param([string[]]$Modules, [switch]$LiveOutput)
    New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
    $pyFile = Join-Path $TMP_DIR 'verify_imports.py'
    [IO.File]::WriteAllText($pyFile, $VERIFY_PY, $script:Utf8NoBom)
    $filter = {
        param($l)
        if ($l -match '^IMPORT\|([^|]+)\|OK\|(.+)$')   { return @{ Text = ("checked {0,-22} OK   ({1}s)" -f $Matches[1], $Matches[2]); Color = 'DarkGreen' } }
        if ($l -match '^IMPORT\|([^|]+)\|FAIL\|(.*)$') { $d = ($Matches[2] -split ' \|\| ') | Select-Object -Last 1; return @{ Text = ("checked {0,-22} FAILED: {1}" -f $Matches[1], $d); Color = 'Red' } }
        return $null
    }
    $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList (@($pyFile) + $Modules) -Label 'Importing modules' -Live:$LiveOutput -LineFilter $filter -TimeoutSec 900 -WorkDir $PROJECT_ROOT
    $res = @{}
    foreach ($l in $r.Output) {
        if ($l -match '^IMPORT\|([^|]+)\|(OK|FAIL)\|(.*)$') {
            $res[$Matches[1]] = @{ Ok = ($Matches[2] -eq 'OK'); Detail = $Matches[3]; Secs = $Matches[3] }
        }
    }
    $missing = @($Modules | Where-Object { -not $res.ContainsKey($_) })
    if ($missing.Count -gt 0 -and $Modules.Count -gt 1) {
        Write-Warn ("Verification process ended early (possible native crash); testing {0} remaining module(s) one by one." -f $missing.Count)
        foreach ($m in $missing) {
            $one = Test-Imports -Modules @($m) -LiveOutput:$LiveOutput
            foreach ($k in $one.Keys) { $res[$k] = $one[$k] }
        }
    } elseif ($missing.Count -gt 0) {
        $res[$missing[0]] = @{ Ok = $false; Detail = ("process crashed: " + (Get-RootCause $r.Output)); Secs = '0' }
    }
    return $res
}

function Get-FailureKind {
    param([string]$Detail)
    if ($Detail -match 'DLL load failed|WinError 126|WinError 127|WinError 193|WinError 1114|Error loading|fbgemm|msvcp140|vcruntime140|The specified module could not be found') { return 'dll' }
    if ($Detail -match 'No module named') { return 'missing' }
    if ($Detail -match '_ARRAY_API|numpy\.core\.multiarray|compiled using NumPy|NumPy 2|numpy\.dtype size changed') { return 'numpy' }
    if ($Detail -match 'INSECURE STARTUP') { return 'secrets' }
    return 'other'
}

function Repair-Import {
    param($Check, [string]$Detail)
    $kind = Get-FailureKind $Detail
    $pkg = $Check.P
    Write-Info ("Repairing {0} (failure type: {1})..." -f $Check.L, $kind)
    switch ($kind) {
        'numpy' {
            [void](Invoke-PipRetry -PipArgs @('install', '--force-reinstall', 'numpy<2', '--progress-bar', 'off') -Label 'Reinstall numpy<2' -Attempts 2)
            [void](Invoke-PipRetry -PipArgs @('install', '--force-reinstall', '--no-deps', '--no-cache-dir', $pkg, '--progress-bar', 'off') -Label "Reinstall $pkg" -Attempts 2)
        }
        default {
            if ($pkg -eq '-e .') {
                [void](Invoke-PipRetry -PipArgs @('install', '-e', '.', '--progress-bar', 'off') -Label 'Reinstall shorts_engine' -Strategies @(@(), @('--no-build-isolation')) -Attempts 2 -TimeoutSec 1200)
            } else {
                [void](Invoke-PipRetry -PipArgs @('install', '--force-reinstall', '--no-cache-dir', $pkg, '--progress-bar', 'off') -Label "Reinstall $pkg" -Attempts 2 -TimeoutSec 3600)
            }
        }
    }
    if ($Check.M -eq 'torch' -and $kind -ne 'numpy') {
        $t = Test-Imports -Modules @('torch')
        if (-not $t['torch'].Ok) {
            Write-Info 'Trying the official CPU-only PyTorch wheel as a last resort...'
            [void](Invoke-PipRetry -PipArgs @('install', '--force-reinstall', 'torch', '--index-url', 'https://download.pytorch.org/whl/cpu', '--progress-bar', 'off') -Label 'PyTorch (CPU index)' -Attempts 2 -TimeoutSec 3600)
        }
    }
}

function Step-Verification {
    if ($SkipTests) { Write-Warn 'Verification skipped (-SkipTests).'; return }
    if (-not $script:VenvOk) { Write-Err 'Virtual environment unavailable - cannot verify imports.'; return }
    $mods = @($IMPORT_CHECKS | ForEach-Object { $_.M })
    Write-Info ("Importing {0} modules (PyTorch and Faster-Whisper take longest, 5-30 s)..." -f $mods.Count)
    $res = Test-Imports -Modules $mods -LiveOutput
    $failed = @($IMPORT_CHECKS | Where-Object { -not $res[$_.M].Ok })

    if ($failed.Count -gt 0 -and $script:NoInstall) {
        Write-Warn ("{0} module(s) failed to import (verify-only mode: no repair attempted)." -f $failed.Count)
    } elseif ($failed.Count -gt 0) {
        Write-Warn ("{0} module(s) failed to import - starting automatic repair." -f $failed.Count)
        foreach ($f in $failed) { Write-Dim ("{0}: {1}" -f $f.L, (($res[$f.M].Detail -split ' \|\| ') | Select-Object -Last 1)) }

        # 1) native-library failures almost always mean the C++ runtime is missing/outdated
        if (@($failed | Where-Object { (Get-FailureKind $res[$_.M].Detail) -eq 'dll' }).Count -gt 0) {
            Write-Info 'Native DLL load failures detected - the Microsoft Visual C++ Runtime is the usual cause.'
            if (Step-VCRuntime -Force) {
                $again = Test-Imports -Modules @($failed | ForEach-Object { $_.M }) -LiveOutput
                foreach ($k in $again.Keys) { $res[$k] = $again[$k] }
                $failed = @($IMPORT_CHECKS | Where-Object { -not $res[$_.M].Ok })
            }
        }
        # 2) per-package repair
        foreach ($f in $failed) {
            Repair-Import -Check $f -Detail $res[$f.M].Detail
            $t = Test-Imports -Modules @($f.M)
            $res[$f.M] = $t[$f.M]
            if ($t[$f.M].Ok) { Write-OK ("{0} repaired and now imports cleanly" -f $f.L) }
        }
        $failed = @($IMPORT_CHECKS | Where-Object { -not $res[$_.M].Ok })
    }

    $okCount = $IMPORT_CHECKS.Count - $failed.Count
    if ($failed.Count -eq 0) { Write-OK ("All {0} critical modules import correctly" -f $IMPORT_CHECKS.Count) }
    else {
        Write-OK ("{0}/{1} modules import correctly" -f $okCount, $IMPORT_CHECKS.Count)
        foreach ($f in $failed) {
            $d = $res[$f.M].Detail
            Write-Err ("{0} cannot be imported: {1}" -f $f.L, (($d -split ' \|\| ') | Select-Object -Last 1))
            Write-Log ("Full traceback for {0}: {1}" -f $f.M, ($d -replace ' \|\| ', "`r`n      "))
            switch (Get-FailureKind $d) {
                'dll'     { Write-Hint 'Install/repair the Microsoft Visual C++ Redistributable (x64) and REBOOT, then re-run: https://aka.ms/vs/17/release/vc_redist.x64.exe' }
                'missing' { Write-Hint "Run:  .\.venv\Scripts\python.exe -m pip install $($f.P)" }
                'secrets' { Write-Hint 'Replace the placeholder secrets in .env (see step 7).' }
                default   { Write-Hint 'See storage\logs\setup.log for the full traceback.' }
            }
        }
    }

    # runtime info
    if ($res['torch'] -and $res['torch'].Ok) {
        $chk = Invoke-Native -FilePath $VENV_PYTHON -ArgList @('-c', "import torch;print('TORCHINFO|%s|%s' % (torch.__version__, torch.cuda.is_available()))") -Quiet -TimeoutSec 120
        $ti = @($chk.Output | Where-Object { $_ -match '^TORCHINFO\|' }) | Select-Object -First 1
        if ($ti) { $p = $ti -split '\|'; Write-Info ("PyTorch {0}, CUDA available: {1}" -f $p[1], $p[2]) }
    }
    $ff = Join-Path $FFMPEG_BIN 'ffmpeg.exe'
    if ((Test-Path -LiteralPath $ff) -or (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-OK 'FFmpeg is available' } else { Write-Warn 'FFmpeg is not available (see step 4).' }
}

# =============================================================================
#  STEP 12 - LIVE SERVER SMOKE TEST
# =============================================================================
function Test-HttpAlive {
    param([int]$Port)
    try {
        $req = [Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/")
        $req.Timeout = 2000; $req.AllowAutoRedirect = $false; $req.Proxy = $null
        $resp = $req.GetResponse(); $resp.Close(); return $true
    } catch [System.Net.WebException] {
        return [bool]$_.Exception.Response      # any HTTP status (401/404/307...) means the server is up
    } catch { return $false }
}

function Step-ServerTest {
    if ($SkipTests) { Write-Warn 'Skipped (-SkipTests).'; return }
    if (-not $script:VenvOk) { Write-Warn 'Skipped: virtual environment unavailable.'; return }

    if ($NoServer -or -not (Test-Path -LiteralPath $RUN_PY)) {
        if ($NoServer) { Write-Info '-NoServer specified: doing an import-only smoke test.' } else { Write-Warn 'run.py not found: doing an import-only smoke test.' }
        New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null
        $f = Join-Path $TMP_DIR 'smoke_import.py'
        [IO.File]::WriteAllText($f, "import sys, os`nroot = sys.argv[1]`nsys.path.insert(0, root)`nos.chdir(root)`nfrom shorts_engine.dashboard.app import app`nprint('SMOKE_OK')`n", $script:Utf8NoBom)
        $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @($f, $PROJECT_ROOT) -Label 'Importing dashboard app' -TimeoutSec 180
        Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
        if (@($r.Output | Where-Object { $_ -match 'SMOKE_OK' }).Count -gt 0) { Write-OK 'Dashboard app imports cleanly' }
        else { Write-Warn ("Dashboard import failed: {0}" -f (Get-RootCause $r.Output)) }
        return
    }

    $port = $script:TestPort
    Write-Info "Starting the real dashboard (python run.py dashboard) on port $port to prove it boots..."
    $filter = { param($l) if ($l -match '(?i)error|exception|traceback|insecure') { return @{ Text = $l.Trim(); Color = 'Yellow' } } else { return @{ Text = $l.Trim(); Color = 'DarkGray' } } }
    $probe = [scriptblock]::Create("Test-HttpAlive -Port $port")
    $env2 = @{ DASHBOARD_PORT = "$port"; PATH = "$FFMPEG_BIN;$env:Path"; BROWSER = 'cmd.exe /c exit' }
    $r = Invoke-Native -FilePath $VENV_PYTHON -ArgList @($RUN_PY, 'dashboard') -Label "Waiting for dashboard on :$port" -Live -LineFilter $filter -Probe $probe -TimeoutSec 120 -Env $env2
    if ($r.ProbeOk) {
        Write-OK "Dashboard started and answered HTTP requests on http://127.0.0.1:$port/  (boot took $([int]$r.Seconds) s)"
        Write-Info 'Test server stopped.'
    } elseif ($r.TimedOut) {
        Write-Warn 'Dashboard did not respond within 120 s (first start can be slow on cold disks). Try launching it manually.'
        Write-Tail $r.Output 8
    } else {
        $cause = Get-RootCause $r.Output
        Write-Err "The dashboard crashed during startup (exit $($r.ExitCode)): $cause"
        Write-Tail $r.Output 12
        if ($cause -match 'INSECURE STARTUP') { Write-Hint 'Placeholder secrets remain in .env - fix step 7 first.' }
        elseif ($cause -match 'DLL load failed') { Write-Hint 'A native library failed to load - fix step 3 / step 11 first.' }
        else { Write-Hint 'Check storage\logs and run:  .\.venv\Scripts\python.exe run.py dashboard  to see the full error.' }
    }
}

# =============================================================================
#  STEP 13 - SUMMARY
# =============================================================================
function Step-Summary {
    Write-Step 'Setup Summary & Next Steps'
    Complete-Step   # close the summary step itself so the table is complete
    $total = (Get-Date) - $script:T0
    $hasErr = $script:Errors.Count -gt 0
    $hasWarn = $script:Warnings.Count -gt 0

    Out-Line '' 'Gray' -NoLog
    Out-Line '  STEP RESULTS' 'White' -NoLog
    foreach ($s in $script:StepResults) {
        $col = 'Green'; if ($s.Status -eq 'WARN') { $col = 'Yellow' }; if ($s.Status -eq 'FAIL') { $col = 'Red' }
        Out-Line ('   {0,2}. {1,-34} {2,-5} {3,5}s' -f $s.Num, $s.Name, $s.Status, $s.Seconds) $col -NoLog
    }
    if ($hasErr) {
        Out-Line '' 'Red' -NoLog
        Out-Line '  ERRORS (action needed):' 'Red' -NoLog
        foreach ($e in @($script:Errors | Select-Object -Unique)) { Out-Line "    - $e" 'Red' -NoLog }
    }
    if ($hasWarn) {
        Out-Line '' 'Yellow' -NoLog
        Out-Line '  Warnings (non-fatal):' 'Yellow' -NoLog
        foreach ($w in @($script:Warnings | Select-Object -Unique)) { Out-Line "    - $w" 'Yellow' -NoLog }
    }
    if ($hasErr) {
        Out-Line '' 'Gray' -NoLog
        Out-Line '  Need help? Send storage\logs\setup_diagnostics.txt (contains no secrets - .env values are never included).' 'Cyan' -NoLog
    }
    if ($script:RebootNeeded) { Out-Line '' 'Yellow' -NoLog; Out-Line '  A Windows restart is recommended to finish installing system components.' 'Yellow' -NoLog }

    Out-Line '' 'Cyan' -NoLog
    Out-Line ('=' * 78) 'Cyan' -NoLog
    if ($hasErr) { Out-Line ("  Setup finished WITH ERRORS in {0} - review the list above" -f (Format-Elapsed $total)) 'Red' -NoLog }
    else         { Out-Line ("  Setup completed SUCCESSFULLY in {0}" -f (Format-Elapsed $total)) 'Green' -NoLog }
    Out-Line ('=' * 78) 'Cyan' -NoLog

    $dashBat = 'tools\launchers\run_dashboard.bat'
    $txt = @"

  HOW TO RUN
  ------------------------------------------------------------------------------
  Easiest (double-click):
       $dashBat        -> Operator Dashboard  http://127.0.0.1:$($script:DashPort)
       tools\launchers\run_worker.bat       -> Background worker (separate window)

  Or from a terminal:
       .\.venv\Scripts\Activate.ps1
       python run.py dashboard
       python run.py worker
       python -m pytest tests -q
       python run.py process --video "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run

  Default dashboard login: admin / AdminPass12345!   (change it after first login)

  Re-run setup.bat any time: it repairs problems and skips what is already done.
  Add -GPU for a CUDA PyTorch build, -Force to reinstall packages, -ForceVenv to rebuild .venv.
  Log file: $($script:LOG_FILE)
  ------------------------------------------------------------------------------
"@
    Out-Line $txt 'Gray' -NoLog
    Write-Log "Setup finished. Errors: $($script:Errors.Count)  Warnings: $($script:Warnings.Count)  Elapsed: $(Format-Elapsed $total)"

    # Offer to launch
    if (-not $hasErr -and $script:Interactive -and (Test-Path -LiteralPath (Join-Path $PROJECT_ROOT $dashBat))) {
        if (Read-YesNo 'Start the dashboard now?' $false 30) {
            $bat = Join-Path $PROJECT_ROOT $dashBat
            try {
                [void](Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', ('"{0}"' -f $bat)) -WorkingDirectory $PROJECT_ROOT)
                if (-not $NoBrowser) {
                    Write-Info 'Waiting for the dashboard to come up, then opening your browser...'
                    $sw = [Diagnostics.Stopwatch]::StartNew(); $up = $false
                    while ($sw.Elapsed.TotalSeconds -lt 90) {
                        Show-StatusLine ('  [*] Waiting for http://127.0.0.1:{0}  {1}' -f $script:DashPort, (Format-Elapsed $sw.Elapsed))
                        if (Test-HttpAlive -Port $script:DashPort) { $up = $true; break }
                        Start-Sleep -Seconds 1
                    }
                    Clear-StatusLine
                    if ($up) { [void](Start-Process "http://127.0.0.1:$($script:DashPort)"); Write-OK 'Dashboard is running - browser opened.' }
                    else     { Write-Warn 'Dashboard has not answered yet; open the URL manually once its window shows it is running.' }
                }
            } catch { Write-Warn "Could not start the dashboard: $($_.Exception.Message)" }
        }
    }
}

# =============================================================================
#  MAIN
# =============================================================================
function Cleanup-TempFiles {
    try {
        foreach ($f in @('verify_imports.py', 'init_db_fallback.py', 'smoke_import.py', 'requirements.utf8.txt')) {
            $p = Join-Path $TMP_DIR $f; if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
        }
        # cached installers are kept only when setup failed (to avoid re-downloading); remove after a clean run
        if ($script:Errors.Count -eq 0 -and (Test-Path -LiteralPath $DL_DIR)) { Remove-Item -LiteralPath $DL_DIR -Recurse -Force -ErrorAction SilentlyContinue }
        if ((Test-Path -LiteralPath $TMP_DIR) -and -not @(Get-ChildItem -LiteralPath $TMP_DIR -Force -ErrorAction SilentlyContinue).Count) { Remove-Item -LiteralPath $TMP_DIR -Force -ErrorAction SilentlyContinue }
    } catch { }
}

$mutex = $null
$ownsRun = $false
$exitCode = 0
try {
    Set-Location -LiteralPath $PROJECT_ROOT

    # single-instance guard (before touching the log so a second window cannot clobber the first one's log)
    try {
        $mname = 'Local\YTShortsSetup_' + ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($PROJECT_ROOT.ToLowerInvariant()))).Replace('-', '').Substring(0, 32))
        $created = $false
        $mutex = New-Object System.Threading.Mutex($true, $mname, [ref]$created)
        if (-not $created) { Write-Fatal 'Another setup is already running for this project.' 'Wait for it to finish (or close its window) and run setup again.' }
    } catch [System.Threading.AbandonedMutexException] {
    } catch {
        if ($_.Exception.Message -like 'SETUP_FATAL::*') { throw }
    }
    $ownsRun = $true

    Initialize-Logging
    Show-Banner
    Write-Log "Starting setup. Total steps: $TOTAL_STEPS"
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
        Write-Fatal "PowerShell is in '$($ExecutionContext.SessionState.LanguageMode)' mode (AppLocker / Device Guard policy). This installer needs FullLanguage mode." 'Ask your IT administrator to allow this script, or run the steps manually from the README.'
    }
    Enable-ConsoleGuards

    Invoke-Step 'Pre-flight System Checks'            { Step-Preflight }      -Critical
    Invoke-Step 'Python Detection & Installation'     { Step-Python }         -Critical
    Invoke-Step 'Visual C++ Runtime'                  { [void](Step-VCRuntime) }
    Invoke-Step 'FFmpeg Detection & Installation'     { Step-FFmpeg }
    Invoke-Step 'Virtual Environment'                 { Step-VirtualEnv }     -Critical
    Invoke-Step 'Python Dependencies'                 { Step-Dependencies }
    Invoke-Step 'Environment Configuration (.env)'    { Step-EnvConfig }
    Invoke-Step 'Folders, Assets & Launchers'         { Step-Directories }
    Invoke-Step 'Database Initialisation'             { Step-Database }
    Invoke-Step 'Port Availability'                   { Step-PortCheck }
    Invoke-Step 'Import Verification & Self-Repair'   { Step-Verification }
    Invoke-Step 'Live Server Smoke Test'              { Step-ServerTest }
    try { Step-Summary } catch {
        Write-Log ($_ | Out-String)
        Out-Line ("  [WARN] Summary could not be completed: {0}" -f $_.Exception.Message) 'Yellow'
    }
    if ($script:Errors.Count -gt 0) { $exitCode = 2 }
} catch {
    $msg = $_.Exception.Message
    if ($msg -like 'SETUP_FATAL::*') {
        Out-Line ''
        Out-Line '  Setup cannot continue (see the message above).' 'Red'
    } elseif ($_.Exception -is [System.Management.Automation.PipelineStoppedException] -or $_.Exception -is [System.OperationCanceledException]) {
        Out-Line ''
        Out-Line '  Setup was cancelled (Ctrl+C). Nothing is left half-installed: just run setup.bat again to continue.' 'Yellow'
        [void]$script:Errors.Add('Setup was cancelled by the user')
    } else {
        $line = ''; try { $line = $_.InvocationInfo.ScriptLineNumber } catch { }
        Out-Line ("[FATAL UNHANDLED ERROR at line {0}] {1}" -f $line, $msg) 'Red'
        Write-Log ($_ | Out-String)
        Write-Log $_.ScriptStackTrace
        [void]$script:Errors.Add("Unhandled error: $msg")
    }
    try { Complete-Step } catch { }
    $exitCode = 1
} finally {
    Clear-StatusLine
    if ($ownsRun) {
        try {
            if ($exitCode -ne 0 -or $script:Errors.Count -gt 0) {
                $diag = Write-Diagnostics
                if ($diag) { Out-Line ("  Diagnostics for support: {0}" -f $diag) 'Yellow' -NoLog }
            }
            Write-Report $exitCode
        } catch { }
        try { Cleanup-TempFiles } catch { }
        Out-Line ("  Full log: {0}" -f $script:LOG_FILE) 'Yellow' -NoLog
    }
    Disable-ConsoleGuards
    if ($mutex -and $ownsRun) { try { $mutex.ReleaseMutex() } catch { }; try { $mutex.Dispose() } catch { } }
    Set-WindowTitleSafe 'Shorts Engine Setup'
}

if ($env:YT_SETUP_FROM_BAT -ne '1' -and $script:Interactive -and $exitCode -ne 0) {
    try { Read-Host 'Press ENTER to exit' | Out-Null } catch { }
}
exit $exitCode
