# Parse error check
$errors = $null; $tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('d:\Projects\YT AUTOMATION\setup.ps1', [ref]$tokens, [ref]$errors)
Write-Host "=== PARSE ERRORS: $($errors.Count) ==="
if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Host "  ERR: $($_.Message) at line $($_.Extent.StartLineNumber)" } }

# Check -Args occurrences (should be 0)
$argsHits = Select-String -Path 'D:\Projects\YT AUTOMATION\setup.ps1' -Pattern 'Invoke-PipWithRetry\s+-Args'
Write-Host "=== -Args hits (should be 0): $($argsHits.Count) ==="

# Check -PipArgs occurrences (should be 4)
$pipArgsHits = Select-String -Path 'D:\Projects\YT AUTOMATION\setup.ps1' -Pattern '\-PipArgs'
Write-Host "=== -PipArgs hits (should be 4): $($pipArgsHits.Count) ==="
$pipArgsHits | ForEach-Object { Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())" }

# Check Select-Object -First 1 in New-RandomKey
$soHits = Select-String -Path 'D:\Projects\YT AUTOMATION\setup.ps1' -Pattern 'Select-Object -First 1'
Write-Host "=== Select-Object -First 1 hits: $($soHits.Count) ==="
$soHits | ForEach-Object { Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())" }

# Check setup.bat line 31
$batLine31 = (Get-Content 'D:\Projects\YT AUTOMATION\setup.bat')[30]
Write-Host "=== setup.bat line 31: $batLine31 ==="

# Test scripts
$testScripts = Get-ChildItem 'D:\Projects\YT AUTOMATION\' -Filter 'test_*.ps1' -File
$checkScripts = Get-ChildItem 'D:\Projects\YT AUTOMATION\' -Filter 'check_syntax.ps1' -File
Write-Host "=== Test scripts remaining: $($testScripts.Count + $checkScripts.Count) ==="

# QUICK_START.md
Write-Host "=== QUICK_START.md exists: $(Test-Path 'D:\Projects\YT AUTOMATION\QUICK_START.md') ==="

# Step count check - count Write-Step calls in Main
$allSteps = Select-String -Path 'D:\Projects\YT AUTOMATION\setup.ps1' -Pattern '\bWrite-Step\b'
Write-Host "=== Write-Step calls total: $($allSteps.Count) ==="
$allSteps | ForEach-Object { Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())" }

# TOTAL_STEPS value
$totalSteps = Select-String -Path 'D:\Projects\YT AUTOMATION\setup.ps1' -Pattern 'TOTAL_STEPS\s*='
Write-Host "=== TOTAL_STEPS setting: ==="
$totalSteps | ForEach-Object { Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())" }
