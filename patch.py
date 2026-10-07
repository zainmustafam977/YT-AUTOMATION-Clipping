import re

with open('d:/Projects/YT AUTOMATION/setup.ps1', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Insert Invoke-NativeCommandLive after Write-Log functions
native_cmd_code = '''
function Invoke-NativeCommandLive {
    param(
        [string],
        [string[]],
        [string]
    )
     = Continue
    Continue = 'Continue'
    
     = -1
    try {
        &  @Arguments 2>&1 | ForEach-Object {
            Write-Host "    " -ForegroundColor DarkGray
            Write-Log 
        }
         = 
    } finally {
        Continue = 
    }
    return 
}
'''
content = re.sub(r'(function Write-Fatal \{.*?\n\})', r'\1\n' + native_cmd_code, content, flags=re.DOTALL)

# 2. Update Invoke-PipWithRetry
pip_replacement = '''function Invoke-PipWithRetry {
    param(
        [string[]],
        [int] = 3,
        [string] = "pip command"
    )
    
    for ( = 1;  -le ; ++) {
        Write-Info "  Attempt / : pip "
         = Invoke-NativeCommandLive -Command "" -Arguments (@("-m", "pip") + ) -Description 
        if ( -eq 0) { return True }
        Write-Warn "Attempt  failed (exit )"
        if ( -lt ) {
            Write-Info "Waiting 3 seconds before retry..."
            Start-Sleep -Seconds 3
        }
    }
    return False
}'''
content = re.sub(r'function Invoke-PipWithRetry \{.*?\n\}', pip_replacement, content, flags=re.DOTALL)

# 3. Add Step-VCRedist
vc_step = '''
# -----------------------------------------------------------------------------
#  STEP 2 - VC++ REDISTRIBUTABLE
# -----------------------------------------------------------------------------
function Step-VCRedist {
    Write-Step "Visual C++ Redistributable Setup"
    
     = "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    if (Test-Path ) {
         = Get-ItemProperty  -ErrorAction SilentlyContinue
        if (.Installed -eq 1) {
            Write-OK "Visual C++ Redistributable is installed (v)"
            return
        }
    }
    
    Write-Info "Visual C++ Redistributable (x64) not found. Installing..."
    try {
         = Join-Path C:\Users\ZACODEC\AppData\Local\Temp "vc_redist.x64.exe"
        Write-Info "Downloading official installer from Microsoft..."
        Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile  -UseBasicParsing
        
        Write-Info "Running installer silently..."
         = Start-Process -FilePath  -ArgumentList "/install", "/quiet", "/norestart" -Wait -NoNewWindow -PassThru
        if (.ExitCode -eq 0 -or .ExitCode -eq 1638 -or .ExitCode -eq 3010) {
            Write-OK "Visual C++ Redistributable installed successfully."
        } else {
            Write-Warn "VC++ installer exited with code . You may need to install it manually."
        }
    } catch {
        Write-Warn "Failed to install VC++ Redistributable: "
    }
}
'''
content = content.replace('#  STEP 2 - FFMPEG', vc_step + '\n# -----------------------------------------------------------------------------\n#  STEP 3 - FFMPEG')

# 4. Update Main execution loop to include Step-VCRedist
content = content.replace('Step-Python', 'Step-Python\n    Step-VCRedist')

# 5. Fix .env placeholder logic in Step-EnvConfig
env_existing_logic = '''
    if (Test-Path ) {
        Write-Info "Checking existing .env for default development placeholders..."
         = Get-Content  -Raw -Encoding UTF8
        
        if ( -match 'yt_shorts_dev_master_secret_key_32_bytes_min!' -or 
             -match 'yt_shorts_dev_dashboard_session_secret_32_bytes!' -or
             -match 'yt_shorts_kdf_salt_2026_secure!') {
            
            Write-Warn "Default development placeholder keys detected in existing .env!"
            Write-Info "Backing up to .env.bak and rotating keys..."
            Copy-Item  ".bak" -Force
            
               = New-RandomKey -Bytes 32
              = New-RandomKey -Bytes 32
                 = New-RandomKey -Bytes 24

             =  -replace 'yt_shorts_dev_master_secret_key_32_bytes_min!',   
             =  -replace 'yt_shorts_dev_dashboard_session_secret_32_bytes!', 
             =  -replace 'yt_shorts_kdf_salt_2026_secure!',                  

            [System.IO.File]::WriteAllText(, , [System.Text.Encoding]::UTF8)
            Write-OK "Successfully rotated placeholder keys in existing .env"
        } else {
            Write-OK ".env already exists and appears secure."
        }
    } else {
'''
content = re.sub(r'if \(Test-Path \\) \{\s+Write-OK "\.env already exists \(\$\(\(Get-Item \\)\.Length\) bytes\) - preserving your settings"\s+\} else \{', env_existing_logic.strip() + ' {', content)


# 6. Step-Database: use Invoke-NativeCommandLive for setup_environment.py so tracebacks stream correctly
db_logic_old = ''' = & "" "" 2>&1
             | ForEach-Object { Write-Log ; Write-Info "  " }
            if ( -eq 0) {'''

db_logic_new = ''' = Invoke-NativeCommandLive -Command "" -Arguments @("") -Description "setup_environment.py"
            if ( -eq 0) {'''
content = content.replace(db_logic_old, db_logic_new)

# 7. Step-Verification: temporarily suspend ErrorActionPreference
verif_old = '''    foreach ( in ) {
        try {
             = & "" -c "import ; print('OK')" 2>&1
            if ( -match '^OK') {'''
            
verif_new = '''    foreach ( in ) {
        try {
             = Continue
            Continue = 'Continue'
             = & "" -c "import ; print('OK')" 2>&1
            Continue = 
            
            if ( -match '^OK') {'''
content = content.replace(verif_old, verif_new)

# 8. Smoke test: temporarily suspend ErrorActionPreference
smoke_old = '''             = & "" -c  2>&1
            if ( -match 'SMOKE_OK') {'''
            
smoke_new = '''             = Continue
            Continue = 'Continue'
             = & "" -c  2>&1
            Continue = 
            
            if ( -match 'SMOKE_OK') {'''
content = content.replace(smoke_old, smoke_new)

with open('d:/Projects/YT AUTOMATION/setup.ps1', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updates applied to setup.ps1 successfully.")
