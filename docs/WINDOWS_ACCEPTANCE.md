# Windows Acceptance Procedure

This document is the **manual verification script** the owner runs on a
clean Windows machine (or a `windows-latest` GitHub Actions runner) to
flip every R4-1.x row from `DONE-UNVERIFIED` to `DONE-VERIFIED`.

The procedure is broken into numbered sections. Every section ends with
a pass/fail checkbox. **All boxes must be ticked** before the owner
approves PR #2.

> **Prerequisites**
>
> - Windows 10 21H2 or Windows 11.
> - PowerShell 5.1 (built-in) and PowerShell 7 (`pwsh`) both available.
> - At least 20 GB free disk space on `C:`.
> - A GitHub account with access to the repo.
> - The repo cloned to a path **without spaces or non-ASCII** for the
>   first run (e.g. `C:\src\YT-AUTOMATION-Clipping`). A second run
>   exercises the spaces-and-non-ASCII case.
> - The `setup.ps1` script in the repo root.
> - For §3 (long paths): `Local Group Policy Editor` access (admin) or
>   the registry key `LongPathsEnabled` set to 1.

---

## §1. Preflight — one-time machine setup

Run each block in **PowerShell 7 (pwsh)**. Each block ends with a
`# PASS` line you should see literally.

```powershell
# 1.1 Confirm PowerShell version.
$PSVersionTable.PSVersion
# Expect: Major >= 7

# 1.2 Confirm ExecutionPolicy is Restricted (we will not weaken it).
Get-ExecutionPolicy -List | Format-List
# Expect: CurrentUser = Restricted, LocalMachine = Restricted

# 1.3 Confirm the repo path.
Set-Location C:\src\YT-AUTOMATION-Clipping
git status
# Expect: branch arena/1bfe7f4c-yt-automation-clipping, clean tree
# PASS
```

- [ ] PowerShell 7+ installed
- [ ] ExecutionPolicy is `Restricted`
- [ ] Repo on `arena/1bfe7f4c-yt-automation-clipping`, clean

## §2. Idempotent setup

The setup script must be **idempotent** — running it twice in a row must
not change anything the second time. This section exercises that.

```powershell
# 2.1 First run.
./setup.ps1 -Mode install 2>&1 | Tee-Object -FilePath logs\setup-1.log
# Expect: "Setup complete." within 5 minutes.
# Expect: a fresh .env.bak.<timestamp> file in the repo root.

# 2.2 Second run.
./setup.ps1 -Mode install 2>&1 | Tee-Object -FilePath logs\setup-2.log
# Expect: every "Step X" line says "skipped (already done)".

# 2.3 Inspect the backup.
Get-ChildItem -Path .env.bak.* | Sort-Object LastWriteTime -Descending | Select-Object -First 1
# Expect: a file with today's date.
# PASS
```

- [ ] First run completes in ≤ 5 min, creates `.env.bak.*`
- [ ] Second run is a no-op (every step skipped)
- [ ] `.env.bak.*` exists with today's date

## §3. Long paths

```powershell
# 3.1 Enable long paths (one-time, machine-wide).
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force

# 3.2 Generate a 240-character path.
$deep = "C:\src\YT-AUTOMATION-Clipping\storage\test-" + ("a" * 200)
New-Item -ItemType Directory -Path $deep -Force | Out-Null

# 3.3 Have setup create a file there.
./setup.ps1 -Mode install -TestLongPath $deep 2>&1 | Tee-Object -FilePath logs\longpath.log
# Expect: a file named "longpath-marker.txt" inside $deep.

# 3.4 Cleanup.
Remove-Item -Recurse -Force $deep
# PASS
```

- [ ] `LongPathsEnabled = 1`
- [ ] 240-character path is writable
- [ ] `setup.ps1 -TestLongPath` succeeds

## §4. Non-ASCII paths

```powershell
# 4.1 Create a folder with non-ASCII characters.
$nonAscii = "C:\src\YT-AUTOMATION-Clipping\storage\тест-フォルダ-🎬"
New-Item -ItemType Directory -Path $nonAscii -Force | Out-Null

# 4.2 Have setup create a file there.
./setup.ps1 -Mode install -TestNonAscii $nonAscii 2>&1 | Tee-Object -FilePath logs\nonascii.log
# Expect: a file named "nonascii-marker.txt" inside $nonAscii.

# 4.3 Cleanup.
Remove-Item -Recurse -Force $nonAscii
# PASS
```

- [ ] Non-ASCII folder created
- [ ] `setup.ps1 -TestNonAscii` succeeds
- [ ] Cleanup works

## §5. UTF-8 console

```powershell
# 5.1 Set the console to UTF-8.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

# 5.2 Run the smoke command.
./setup.ps1 -Mode install -TestUtf8 2>&1 | Tee-Object -FilePath logs\utf8.log
# Expect: lines like "✓ Установка завершена" (Russian) and "✓ セットアップ" (Japanese) print without `?` substitution.

# 5.3 Inspect the log.
Select-String -Path logs\utf8.log -Pattern "завершена|セットアップ"
# Expect: at least one match per language.
# PASS
```

- [ ] Console output is UTF-8
- [ ] Russian + Japanese strings render correctly
- [ ] No `?` substitution

## §6. Drive letter + spaces

```powershell
# 6.1 Create a path on a different drive with spaces.
$spaced = "D:\Program Files\YT-AUTOMATION-Clipping\storage"
New-Item -ItemType Directory -Path $spaced -Force | Out-Null

# 6.2 Run setup with -InstallRoot pointed at the spaced path.
./setup.ps1 -Mode install -InstallRoot $spaced 2>&1 | Tee-Object -FilePath logs\spaced.log
# Expect: setup completes, .env points to $spaced, dashboard starts on http://localhost:8000.

# 6.3 Curl the dashboard.
(Invoke-WebRequest -Uri http://localhost:8000/api/health -UseBasicParsing).Content
# Expect: {"status":"ok"}

# 6.4 Cleanup.
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -Recurse -Force $spaced
# PASS
```

- [ ] Path with spaces on `D:\` is accepted
- [ ] Dashboard starts and `/api/health` returns ok
- [ ] Cleanup is clean

## §7. ffmpeg filter + ASS escaping

```powershell
# 7.1 Generate a 5-second test video.
ffmpeg -y -f lavfi -i "testsrc=size=320x240:rate=30" -t 5 C:\src\YT-AUTOMATION-Clipping\storage\test.mp4

# 7.2 Run a clip with a caption containing single-quote, comma, colon, and backslash.
python -m shorts_engine.cli run-clip `
  --input storage\test.mp4 `
  --output storage\out.mp4 `
  --caption "It's a 'test': with, comma; and \ backslash" 2>&1 | Tee-Object -FilePath logs\ffmpeg-escape.log

# 7.3 Probe the output.
ffprobe -v error -show_streams storage\out.mp4 | Select-String "codec_name"
# Expect: h264, aac.

# 7.4 Inspect the rendered caption (ASS file in the temp dir).
Get-ChildItem -Path $env:TEMP -Filter *.ass -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 | Get-Content
# Expect: the caption is present, with backslash properly escaped.
# PASS
```

- [ ] `ffmpeg` is on PATH and version reports build
- [ ] `run-clip` with quote/comma/colon/backslash succeeds
- [ ] Output is h264/aac
- [ ] ASS file shows properly escaped caption

## §8. pywin32 job objects — child process cleanup

This is the **safety-critical** section. A worker that spawns `ffmpeg`
and then dies must not leave `ffmpeg` running.

```powershell
# 8.1 Start a worker with a long-running clip.
$job = Start-Job -ScriptBlock {
  Set-Location C:\src\YT-AUTOMATION-Clipping
  python -m shorts_engine.cli run-clip --input storage\test.mp4 --output storage\long.mp4 --duration 300
}

# 8.2 Wait 5 seconds, then check that exactly one ffmpeg is running.
Start-Sleep -Seconds 5
(Get-Process -Name ffmpeg -ErrorAction SilentlyContinue).Count
# Expect: 1

# 8.3 Kill the parent PowerShell job.
Stop-Job $job
Remove-Job $job -Force

# 8.4 Wait 2 seconds, then check that ffmpeg is gone.
Start-Sleep -Seconds 2
(Get-Process -Name ffmpeg -ErrorAction SilentlyContinue).Count
# Expect: 0
# If 0, the pywin32 job object is working.
# PASS
```

- [ ] Exactly 1 ffmpeg while worker is alive
- [ ] 0 ffmpeg within 2 s of worker kill
- [ ] No orphan processes

## §9. Watch-folder lock + restart

```powershell
# 9.1 Start two watch-folder daemons on the same folder.
$wf1 = Start-Job -ScriptBlock { python -m shorts_engine.cli watch-folder --folder C:\wf-test }
$wf2 = Start-Job -ScriptBlock { python -m shorts_engine.cli watch-folder --folder C:\wf-test }

# 9.2 Wait 5 seconds, then check the logs.
Start-Sleep -Seconds 5
Get-Content -Path logs\watch-folder-1.log -Tail 10
# Expect: "primary" on one, "standby" on the other.

# 9.3 Kill the primary.
Get-Process -Name python | Where-Object { $_.Id -eq (Get-Job $wf1).Id } | Stop-Process -Force

# 9.4 Wait 10 seconds, then check the other log.
Start-Sleep -Seconds 10
Get-Content -Path logs\watch-folder-2.log -Tail 10
# Expect: "promoted to primary" within 10 seconds.

# 9.5 Cleanup.
Stop-Job $wf2; Remove-Job $wf2 -Force
Get-Process -Name ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -Recurse -Force C:\wf-test
# PASS
```

- [ ] Two watch-folder instances start; one is primary, one is standby
- [ ] Standby promotes to primary within 10 s of primary crash
- [ ] Cleanup is clean

## §10. GPU encoder detection (QSV / AMF / NVENC)

```powershell
# 10.1 List available encoders.
ffmpeg -hide_banner -encoders | Select-String "h264_(qsv|amf|nvenc)"
# Expect: at least one of h264_qsv, h264_amf, h264_nvenc. If none, the run uses libx264 and the log records the reason.

# 10.2 Run a clip and inspect the encoder choice in the log.
python -m shorts_engine.cli run-clip --input storage\test.mp4 --output storage\gpu.mp4 2>&1 | Tee-Object -FilePath logs\gpu.log
Select-String -Path logs\gpu.log -Pattern "encoder="
# Expect: "encoder=h264_qsv" on Intel, "encoder=h264_amf" on AMD, "encoder=h264_nvenc" on NVIDIA, "encoder=libx264" elsewhere — plus a one-line "reason=".

# 10.3 STRICT_MODE behaviour.
$env:STRICT_MODE = "true"
python -m shorts_engine.cli run-clip --input storage\test.mp4 --output storage\strict.mp4 2>&1 | Tee-Object -FilePath logs\strict.log
# Expect: if the chosen encoder is libx264 (because no GPU is present), the run exits with code 2 and a "STRICT_MODE: GPU encoder unavailable" message.
Remove-Item Env:STRICT_MODE
# PASS
```

- [ ] Encoder list reported
- [ ] Chosen encoder + reason logged
- [ ] STRICT_MODE exits 2 if GPU absent

## §11. setup.bat double-click

```powershell
# 11.1 From a clean state, double-click setup.bat in Explorer.
# (Skip this block on a CI runner; it is owner-only.)

# 11.2 A console opens, shows live progress, asks for the install directory, then says "Setup complete."

# 11.3 Double-click the start-menu shortcut the script created.
# Expect: the dashboard opens in a browser at http://localhost:8000.
# PASS
```

- [ ] `setup.bat` double-click installs
- [ ] Start-menu shortcut launches dashboard

## §12. Sign-off

When every box above is ticked, run this final block:

```powershell
# 12.1 Generate the acceptance report.
python tools/winsmoke.py --report acceptance.json 2>&1 | Tee-Object -FilePath logs\final.log

# 12.2 Inspect the report.
Get-Content acceptance.json | ConvertFrom-Json | Format-List
# Expect: all sections "ok" or "skipped", none "fail".

# 12.3 Commit the report to the repo.
git add acceptance.json logs/
git commit -m "verify(R4-1.x): Windows acceptance procedure complete"
# PASS
```

- [ ] `winsmoke.py` reports all sections ok
- [ ] Report committed to the repo
- [ ] Ledger rows R4-1.1, R4-1.4, R4-1.6, R4-1.8 marked DONE-VERIFIED

---

**Once §12 is complete, the owner updates the ledger rows and pushes
the commit. PR #2 can then be merged by the owner.**

If any box is **not** ticked, the owner opens an issue with the failing
section number, the log file path, and the screenshot/error message. The
issue is referenced from the corresponding R4-1.x row in the ledger
under `evidence:`.
