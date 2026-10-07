$ErrorActionPreference = 'Stop'
try {
    $r = & python -c 'import sys; print("line1", file=sys.stderr); print("line2", file=sys.stderr); sys.exit(1)' 2>&1
    Write-Host "Result: $r"
} catch {
    Write-Host "Exception caught: $_"
}
