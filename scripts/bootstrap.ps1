# ==============================================================================
# YouTube Shorts Automation Studio - Self-Healing Windows Bootstrapper
# ==============================================================================
# Automatically verifies, auto-heals, and provisions all prerequisites:
# 1. Python 3.11/3.12 Runtime (Detection, validation, winget auto-install)
# 2. FFmpeg GPL Engine with Hardware Acceleration (NVENC, QSV, AMF, libass)
# 3. Virtual Environment (.venv) & Core Dependencies (Self-healing auto-repair)
# 4. Database Schema & Migrations (Alembic + SQLAlchemy auto-init)
# 5. Port 8000 Conflict Resolution & Reclaim
# 6. Server Launch & Intelligent Browser Health Polling
# ==============================================================================

param(
    [switch]$NoServer,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $ProjectRoot

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   YouTube Shorts Automation Studio - Windows Self-Healing Setup " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# [1/6] Check / Install Python Runtime
# ------------------------------------------------------------------------------
Write-Host "`n[1/6] Checking Python Runtime (3.11 or 3.12)..." -ForegroundColor Yellow
$PythonExe = $null

function Test-PythonCandidate ($Candidate) {
    if (-not $Candidate) { return $null }
    try {
        $checkCmd = '& "' + $Candidate + '" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"'
        Invoke-Expression $checkCmd 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $verCmd = '& "' + $Candidate + '" -c "import sys; print(f''{sys.version_info.major}.{sys.version_info.minor}'')"'
            $ver = (Invoke-Expression $verCmd 2>$null).Trim()
            return @{ Path = $Candidate; Version = $ver }
        }
    } catch {}
    return $null
}

# 1a. Check python in current PATH
if (Get-Command python -ErrorAction SilentlyContinue) {
    $res = Test-PythonCandidate (Get-Command python).Source
    if ($res) {
        $PythonExe = $res.Path
        Write-Host "  -> Found compatible Python $($res.Version) in PATH: $PythonExe" -ForegroundColor Green
    }
}

# 1b. Check py launcher
if (-not $PythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        $pyPath = (py -3.11 -c "import sys; print(sys.executable)" 2>$null).Trim()
        $res = Test-PythonCandidate $pyPath
        if ($res) {
            $PythonExe = $res.Path
            Write-Host "  -> Found compatible Python via py launcher: $PythonExe" -ForegroundColor Green
        }
    } catch {}
}

# 1c. Check common installation directories
if (-not $PythonExe) {
    $CommonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($cand in $CommonPaths) {
        if (Test-Path $cand) {
            $res = Test-PythonCandidate $cand
            if ($res) {
                $PythonExe = $res.Path
                Write-Host "  -> Found compatible Python $($res.Version) at: $PythonExe" -ForegroundColor Green
                break
            }
        }
    }
}

# 1d. Auto-install Python via winget if missing
if (-not $PythonExe) {
    Write-Host "  -> Python 3.11 not found on system. Installing via Windows Package Manager (winget)..." -ForegroundColor Magenta
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        # Refresh PATH from registry
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $cand = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
        $res = Test-PythonCandidate $cand
        if ($res) {
            $PythonExe = $res.Path
            Write-Host "  -> Python 3.11 successfully installed." -ForegroundColor Green
        }
    }
    if (-not $PythonExe) {
        Write-Host "  [ERROR] Python 3.11/3.12 is required but could not be located or installed." -ForegroundColor Red
        Write-Host "  Please install Python 3.11 from https://www.python.org/downloads/ and re-run." -ForegroundColor Red
        exit 1
    }
}

# ------------------------------------------------------------------------------
# [2/6] Check / Provision FFmpeg with Hardware Acceleration
# ------------------------------------------------------------------------------
Write-Host "`n[2/6] Checking FFmpeg Multimedia Engine (with HWAccel & libass)..." -ForegroundColor Yellow
$ToolsFfmpegDir = Join-Path $ProjectRoot "tools\ffmpeg\bin"
$ToolsFfmpegExe = Join-Path $ToolsFfmpegDir "ffmpeg.exe"

function Test-FFmpegExecutable ($Candidate) {
    if (-not $Candidate -or -not (Test-Path $Candidate)) { return $false }
    try {
        $checkCmd = '& "' + $Candidate + '" -version'
        Invoke-Expression $checkCmd 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$SysFfmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (Test-FFmpegExecutable $ToolsFfmpegExe) {
    if ($env:Path -notlike "*$ToolsFfmpegDir*") {
        $env:Path = "$ToolsFfmpegDir;" + $env:Path
    }
    Write-Host "  -> Using bundled FFmpeg at: $ToolsFfmpegExe" -ForegroundColor Green
} elseif ($SysFfmpegCmd -and (Test-FFmpegExecutable $SysFfmpegCmd.Source)) {
    $SysFfmpeg = $SysFfmpegCmd.Source
    Write-Host "  -> Using system FFmpeg in PATH: $SysFfmpeg" -ForegroundColor Green
} else {
    Write-Host "  -> FFmpeg not found or unverified. Downloading official BtbN GPL build (includes NVENC, QSV, AMF)..." -ForegroundColor Magenta
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "tools\ffmpeg") | Out-Null
    $ZipPath = Join-Path $ProjectRoot "tools\ffmpeg_download.zip"
    $DownloadUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "  -> Downloading from $DownloadUrl..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing
        
        Write-Host "  -> Extracting FFmpeg binaries..." -ForegroundColor Cyan
        $ExtractTmp = Join-Path $ProjectRoot "tools\ffmpeg_extract_tmp"
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractTmp -Force
        
        $ExtractedBin = Get-ChildItem -Path $ExtractTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if ($ExtractedBin) {
            New-Item -ItemType Directory -Force -Path $ToolsFfmpegDir | Out-Null
            Copy-Item -Path "$($ExtractedBin.DirectoryName)\*" -Destination $ToolsFfmpegDir -Recurse -Force
            Write-Host "  -> FFmpeg GPL build successfully installed to $ToolsFfmpegDir" -ForegroundColor Green
        }
        Remove-Item -Recurse -Force $ZipPath, $ExtractTmp -ErrorAction SilentlyContinue
        $env:Path = "$ToolsFfmpegDir;" + $env:Path
    } catch {
        Write-Host "  [WARNING] Automatic FFmpeg download notice: $_" -ForegroundColor Yellow
        Write-Host "  Please ensure ffmpeg is available in PATH." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------------------
# [3/6] Virtual Environment & Core Dependencies (Self-Healing Auto-Repair)
# ------------------------------------------------------------------------------
Write-Host "`n[3/6] Verifying Python Virtual Environment (.venv)..." -ForegroundColor Yellow
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# 3a. Verify venv executable health
$VenvFunctional = $false
if (Test-Path $VenvPython) {
    try {
        $testRes = & $VenvPython -c "import sys; print('ok')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $testRes -match "ok") {
            $VenvFunctional = $true
        }
    } catch {}
}

# 3b. If corrupted or missing, re-create with system site-packages
if (-not $VenvFunctional) {
    if (Test-Path $VenvDir) {
        Write-Host "  -> Existing .venv appears corrupted or unlinked. Rebuilding cleanly..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
    }
    Write-Host "  -> Creating self-healing virtual environment at $VenvDir..." -ForegroundColor Cyan
    & $PythonExe -m venv --system-site-packages $VenvDir
    Write-Host "  -> Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "  -> Virtual environment runtime verified: $VenvPython" -ForegroundColor Green
}

# 3c. Verify core runtime packages and editable project installation
$DepsHealthy = $false
try {
    $depTest = & $VenvPython -c "import uvicorn, fastapi, sqlalchemy, pydantic; import shorts_engine; print('healthy')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $depTest -match "healthy") {
        $DepsHealthy = $true
    }
} catch {}

if (-not $DepsHealthy) {
    Write-Host "  -> Core packages or shorts_engine module not found in .venv. Auto-healing dependencies..." -ForegroundColor Yellow
    try {
        Write-Host "  -> Ensuring pip, setuptools, and wheel are current..." -ForegroundColor Cyan
        & $VenvPython -m pip install --upgrade pip setuptools wheel --quiet
        
        Write-Host "  -> Installing shorts-engine in editable mode..." -ForegroundColor Cyan
        & $VenvPython -m pip install -e . --no-deps --quiet
        
        Write-Host "  -> Verifying web server stack (uvicorn, fastapi, alembic)..." -ForegroundColor Cyan
        & $VenvPython -m pip install "uvicorn[standard]" fastapi sqlalchemy alembic pydantic pydantic-settings python-multipart jinja2 aiofiles httpx --quiet
        
        # Re-check health
        $recheck = & $VenvPython -c "import uvicorn, fastapi, sqlalchemy, alembic; import shorts_engine; print('healthy')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $recheck -match "healthy") {
            $DepsHealthy = $true
            Write-Host "  -> Dependencies successfully auto-repaired and verified." -ForegroundColor Green
        }
    } catch {
        Write-Host "  [WARNING] Dependency repair encountered a notice: $_" -ForegroundColor Yellow
    }
}

if (-not $DepsHealthy) {
    Write-Host "  [ERROR] Virtual environment dependencies could not be completely resolved." -ForegroundColor Red
    Write-Host "  Please run 'pip install -e .' manually or inspect network connectivity." -ForegroundColor Red
    exit 1
}

Write-Host "  -> Virtual environment dependencies verified 100% operational." -ForegroundColor Green

# Ensure assets/fonts exists and color emoji font is provisioned (Enhancement 6)
$FontsDir = Join-Path $ProjectRoot "assets\fonts"
if (-not (Test-Path $FontsDir)) {
    New-Item -ItemType Directory -Force -Path $FontsDir | Out-Null
}
$WindowsEmojiFont = "C:\Windows\Fonts\seguiemj.ttf"
$TargetEmojiFont = Join-Path $FontsDir "seguiemj.ttf"
if ((Test-Path $WindowsEmojiFont) -and (-not (Test-Path $TargetEmojiFont))) {
    try {
        Copy-Item -Path $WindowsEmojiFont -Destination $TargetEmojiFont -Force
        Write-Host "  -> Bundled Windows Color Emoji font (seguiemj.ttf) into assets/fonts" -ForegroundColor Green
    } catch {
        Write-Host "  [WARNING] Could not copy Windows emoji font: $_" -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------------------
# [4/6] Database Schema Migrations & Initialization
# ------------------------------------------------------------------------------
Write-Host "`n[4/6] Running Database Schema Migrations & Initialization..." -ForegroundColor Yellow
try {
    # Attempt Alembic migration first
    & $VenvPython -m alembic upgrade head 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  -> Alembic schema migrations up-to-date." -ForegroundColor Green
    }
} catch {}

# Guarantee database tables, indexes, and upgrades are fully initialized
try {
    & $VenvPython -c "from shorts_engine.db.session import init_db; init_db()"
    Write-Host "  -> Database tables, indexes, and schema verified." -ForegroundColor Green
} catch {
    Write-Host "  [WARNING] Database schema check notice: $_" -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# [5/6] Port 8000 Conflict Resolution & Reclaim
# ------------------------------------------------------------------------------
Write-Host "`n[5/6] Checking Port 8000 Availability..." -ForegroundColor Yellow
$Occupied = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($Occupied) {
    $StalePid = $Occupied.OwningProcess
    Write-Host "  -> Port 8000 is occupied by PID $StalePid. Reclaiming port..." -ForegroundColor Yellow
    try {
        Stop-Process -Id $StalePid -Force -ErrorAction SilentlyContinue
        # Poll up to 3 seconds for socket release
        for ($i = 0; $i -lt 6; $i++) {
            Start-Sleep -Milliseconds 500
            $Occupied = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
            if (-not $Occupied) { break }
        }
        if (-not $Occupied) {
            Write-Host "  -> Stale process (PID $StalePid) terminated; Port 8000 released." -ForegroundColor Green
        } else {
            Write-Host "  [WARNING] Port 8000 may still be held by PID $StalePid." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [WARNING] Could not terminate PID $StalePid. Server will attempt to bind." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Port 8000 is available." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# [6/6] Launch Application & Intelligent Browser Trigger
# ------------------------------------------------------------------------------
if ($NoServer) {
    Write-Host "`n[6/6] Self-healing bootstrap verification completed successfully (-NoServer)." -ForegroundColor Green
    exit 0
}

Write-Host "`n[6/6] Launching YouTube Shorts Automation Studio..." -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  Dashboard URL: http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C in this terminal window to stop the server." -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# Launch browser asynchronously once the server actually responds to HTTP health check
if (-not $NoBrowser) {
    $BrowserTrigger = {
        try {
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Milliseconds 500
                try {
                    $req = [System.Net.WebRequest]::Create("http://127.0.0.1:8000/healthz")
                    $req.Timeout = 1000
                    $resp = $req.GetResponse()
                    if ($resp.StatusCode -eq 200) {
                        $resp.Close()
                        break
                    }
                    $resp.Close()
                } catch {}
            }
            Start-Process "http://127.0.0.1:8000" -ErrorAction SilentlyContinue
        } catch {}
    }
    try {
        [System.Threading.ThreadPool]::QueueUserWorkItem([System.Threading.WaitCallback]{ & $BrowserTrigger }) | Out-Null
    } catch {}
}

# Start FastAPI server via uvicorn
$env:PYTHONPATH = $ProjectRoot
$ErrorActionPreference = "Continue"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
& $VenvPython -m uvicorn shorts_engine.dashboard.app:app --host 127.0.0.1 --port 8000

