# Deep audit of setup.ps1 for potential strict-mode issues and other bugs

$file = 'D:\Projects\YT AUTOMATION\setup.ps1'

Write-Host "=== ErrorAction SilentlyContinue guards ==="
Select-String -Path $file -Pattern 'ErrorAction SilentlyContinue' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== LASTEXITCODE usage (without -ErrorAction guard on external cmds) ==="
Select-String -Path $file -Pattern 'LASTEXITCODE' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== Null checks on PythonExe ==="
Select-String -Path $file -Pattern '\$script:PythonExe' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== .Count accesses ==="
Select-String -Path $file -Pattern '\.Count' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== Check New-RandomKey function ==="
Select-String -Path $file -Pattern 'New-RandomKey|ToBase64String|replace.*\+' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== Check for any remaining SETUP.ps1 (uppercase) references ==="
Select-String -Path $file -Pattern 'SETUP\.ps1' | ForEach-Object {
    Write-Host "  Line $($_.LineNumber): $($_.Line.Trim())"
}
Select-String -Path 'D:\Projects\YT AUTOMATION\setup.bat' -Pattern 'SETUP\.ps1' | ForEach-Object {
    Write-Host "  BAT Line $($_.LineNumber): $($_.Line.Trim())"
}

Write-Host "`n=== Check QUICK_START.md content ==="
Get-Content 'D:\Projects\YT AUTOMATION\QUICK_START.md'

Write-Host "`n=== Check for any old SETUP.ps1 uppercase file ==="
Get-ChildItem 'D:\Projects\YT AUTOMATION\' -Filter '*.ps1' -File | ForEach-Object { $_.Name }
