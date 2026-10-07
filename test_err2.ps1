$ErrorActionPreference = 'Stop'
try {
    $oldErr = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $r = & python -c 'import sys; print("line1", file=sys.stderr); print("line2", file=sys.stderr); sys.exit(1)' 2>&1
    $ErrorActionPreference = $oldErr
    Write-Host "Exit Code: $LASTEXITCODE"
    Write-Host "Result: $($r -join \"
\")"
} catch {
    Write-Host "Exception caught: $_"
}
