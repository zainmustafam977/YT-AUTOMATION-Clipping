# AST-based analysis of setup.ps1
$file = 'D:\Projects\YT AUTOMATION\setup.ps1'
$errors = $null
$tokens = $null
$result = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)

Write-Host "=== PARSE ERRORS: $($errors.Count) ==="
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Host "  ERR at line $($_.Extent.StartLineNumber): $($_.Message)" }
}

# Count all function definitions
$funcs = $result.FindAll({ param($ast) $ast -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
Write-Host "`n=== Functions defined: $($funcs.Count) ==="
$funcs | ForEach-Object { Write-Host "  - $($_.Name)" }

# Verify Invoke-PipWithRetry param name
Write-Host "`n=== Invoke-PipWithRetry parameter names: ==="
$pipFunc = $funcs | Where-Object { $_.Name -eq 'Invoke-PipWithRetry' }
if ($pipFunc) {
    $pipFunc.Body.ParamBlock.Parameters | ForEach-Object { 
        Write-Host "  - $($_.Name.VariablePath.UserPath) (type: $($_.StaticType))"
    }
}

# Find all Invoke-PipWithRetry calls and show their arguments
Write-Host "`n=== All CommandAst calls to Invoke-PipWithRetry: ==="
$calls = $result.FindAll({ 
    param($ast) 
    $ast -is [System.Management.Automation.Language.CommandAst] -and 
    $ast.GetCommandName() -eq 'Invoke-PipWithRetry'
}, $true)
$calls | ForEach-Object {
    Write-Host "  Line $($_.Extent.StartLineNumber): $($_.Extent.Text.Substring(0, [Math]::Min(100, $_.Extent.Text.Length)))"
}

Write-Host "`n=== Done ==="
