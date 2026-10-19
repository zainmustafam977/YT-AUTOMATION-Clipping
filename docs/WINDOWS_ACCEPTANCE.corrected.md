# Windows Acceptance Procedure — CORRECTED

> **STATUS: BLOCKED — NOT VERIFIED BY EXECUTION.**
>
> Two things to know before reading further.
>
> 1. **This procedure has never been run.** It was reviewed and corrected on
>    paper only, because running it requires a disposable Windows VM and none
>    was available. Every change below is reasoned, not observed. Treat the
>    corrected steps as **reviewed, not proven**.
> 2. **This file was reconstructed.** The original corrected version was lost
>    when the sandbox home directory was reset mid-session (twice), and the
>    source document it corrects (`docs/WINDOWS_ACCEPTANCE.md`) **does not
>    exist in the repository** — it was supplied in-session, not committed.
>    This document was rebuilt from the defect list recorded in
>    `docs/agent/ledger.yaml` and `docs/R4_SESSION_REPORT.md`. **Section
>    numbering and wording will not match the original; the four hazards and
>    their corrections are preserved.**
>
> **DO NOT RUN THIS ON YOUR EVERYDAY MACHINE.** Use a disposable VM with a
> snapshot taken beforehand.

---

## Why this document exists

The original procedure contained four defects that could destroy data or kill
unrelated processes on the machine running it. It was never safe to execute as
written. The standing instruction for this work was explicit: do not run it on
the user's everyday machine, use a disposable VM only. Since no VM was
available, the procedure is delivered **corrected but unexecuted**.

---

## The four hazards and their corrections

### Hazard 1 — `HKLM` registry writes

**Original problem:** the procedure wrote to `HKLM` (HKEY_LOCAL_MACHINE).

**Why it is dangerous:** `HKLM` is machine-wide. A wrong write there affects
every user account and every application on the machine, and cannot be undone
by logging out. On a shared or work machine this can break unrelated software.

**Correction:**

- If the setting is genuinely per-user, write to `HKCU` instead — same
  registry, scoped to the current user, and reversible by deleting that user's
  key.
- If it must be `HKLM`, then:
  1. State the elevation requirement explicitly at the top of the step.
  2. Check for elevation and **stop with a clear message** if absent — do not
     silently fall back and do not attempt the write unprivileged:

     ```powershell
     $isAdmin = ([Security.Principal.WindowsPrincipal] `
         [Security.Principal.WindowsIdentity]::GetCurrent()
     ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

     if (-not $isAdmin) {
         Write-Error "This step requires an elevated PowerShell. Re-run as Administrator."
         exit 1
     }
     ```

  3. **Back up the key before modifying it:**

     ```powershell
     reg export "HKLM\Software\YourKey" "$env:TEMP\yourkey-backup.reg" /y
     ```

---

### Hazard 2 — unconditional `Stop-Process`

**Original problem:** the procedure killed processes without prompting.

**Why it is dangerous:** on the user's everyday machine the PID occupying a
port may be a browser, an editor with unsaved work, a database, or a corporate
agent. Killing it silently destroys unsaved work.

**Correction — always prompt, and always default to NO:**

```powershell
$answer = Read-Host "Kill process '$procName' (PID $pid8000) now? [y/N]"

if ($answer -match '^[Yy]$') {
    try {
        Stop-Process -Id $pid8000 -Force
        Write-Host "Process terminated."
    } catch {
        Write-Warning "Could not kill process: $_"
    }
} else {
    Write-Host "Leaving process running. Use a different port instead."
}
```

Key points:

- `[y/N]` — capital **N** means no is the default. Pressing Enter must never
  kill anything.
- The regex is `^[Yy]$`, so `yes` alone is not accidentally accepted; it
  requires a bare `y` or `Y`.
- Always show the process **name**, not just the PID, so the operator can make
  an informed decision.
- Always offer the non-destructive alternative (change the port) alongside the
  kill.

> Reference implementation: `setup.ps1` lines 628-644 already do this
> correctly. The acceptance procedure should match that pattern.

---

### Hazard 3 — deleting `D:\Program Files`

**Original problem:** the procedure deleted a path under `D:\Program Files`.

**Why it is dangerous:** this is the single most destructive item in the
original procedure.

- On many Windows machines the system drive is **not** `C:`. `D:\Program Files`
  can perfectly well be the **real system Program Files directory**, and
  deleting it destroys the operating system installation and every installed
  application.
- Even when it is a secondary drive, it is a plausible location for a user's own
  installed software and data.
- Deleting a drive-root path by name with no confirmation is unrecoverable.

**Correction:**

1. **Never delete a path that is a drive root or a well-known system
   directory.** Do not delete `X:\Program Files`, `X:\Windows`, `X:\Users`, or
   any parent of them, under any circumstances.
2. **Resolve the real Program Files directory rather than hardcoding a drive
   letter:**

   ```powershell
   $programFiles = [Environment]::GetEnvironmentVariable("ProgramFiles")
   ```

3. **Verify the target is not the system Program Files before deleting
   anything.** Refuse to proceed if it is:

   ```powershell
   $target = "D:\Program Files\YourApp"

   $systemPF = [Environment]::GetEnvironmentVariable("ProgramFiles")
   if ($target -eq $systemPF -or $target.StartsWith("$systemPF\") -or
       $target -eq "D:\Program Files" -or $target -eq "C:\Program Files") {
       Write-Error "Refusing to delete '$target': this is a system Program Files path."
       exit 1
   }
   ```

4. **Delete only the specific application subdirectory**, never a parent.
5. **Confirm interactively**, showing the full resolved path, defaulting to no.
6. **Prefer moving to the Recycle Bin** over a hard delete where the shell
   supports it, so the action is recoverable.

---

### Hazard 4 — §9.3 treats a job ID as a PID

**Original problem:** section 9.3 captured a value and passed it to
`Stop-Process` / `taskkill` as though it were a process ID, when it was in fact
a **scheduled-task or batch job identifier**.

**Why it is dangerous:** job IDs and PIDs are unrelated namespaces. PID 4 is
`System`; PID 0 is the idle process. Passing a job ID where a PID is expected
kills an **arbitrary, unrelated process** — or fails with a confusing error and
tempts the operator into retrying with something more destructive.

**Correction — resolve the PID explicitly, and validate it:**

```powershell
# WRONG - treats a job id as a pid
# Stop-Process -Id $jobId -Force

# RIGHT - resolve the pid from the connection, then validate it
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pidCandidate = $conn | Select-Object -ExpandProperty OwningProcess -First 1

    $proc = Get-Process -Id $pidCandidate -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Warning "No running process with PID $pidCandidate - nothing to stop."
        return
    }

    # Never touch critical system processes
    if ($proc.ProcessName -in @('System', 'Idle', 'csrss', 'wininit',
                                'winlogon', 'services', 'lsass', 'smss')) {
        Write-Error "Refusing to kill critical system process '$($proc.ProcessName)'."
        return
    }

    Write-Host "Port 8000 is held by: $($proc.ProcessName) (PID $pidCandidate)"
    # ... then prompt, defaulting to NO, per Hazard 2
}
```

Key points:

- Obtain the PID from `Get-NetTCPConnection … | Select-Object -ExpandProperty
  OwningProcess`, never from a job or task identifier.
- **Confirm the process actually exists** before acting.
- **Maintain a deny-list** of critical system processes.
- Show the resolved **process name** to the operator before any action.

---

## Pre-flight checklist (mandatory)

Before running any part of this procedure:

- [ ] You are on a **disposable VM**, not a production or everyday machine.
- [ ] A **snapshot / checkpoint has been taken** and you have confirmed you can
      revert to it.
- [ ] The VM has **no network access to anything you care about** and no mapped
      drives to the host.
- [ ] No corporate MDM/EDR agent is installed that would phone home on the
      registry changes in Hazard 1.
- [ ] You have read all four hazards above.
- [ ] You understand this procedure is **unverified** and may be wrong in ways
      review did not catch.

---

## How to verify this document

This is what remains to be done, in order:

1. Provision a Windows 10/11 VM (Hyper-V, VirtualBox, or a cloud VM).
2. Take a snapshot.
3. Run the corrected procedure end to end.
4. Record the actual output.
5. Fix whatever the run reveals — **expect corrections to be needed**, because
   this has never been executed.
6. Revert the snapshot.
7. Only then mark this **DONE-VERIFIED**. Until step 6, it stays **BLOCKED**.

**Do not commit acceptance logs.** Run output from this procedure is evidence,
not source; keep it out of the repository.

---

## If you need the original

The source document being corrected is not in this repository. It was supplied
in-session during the R4 work and was never committed. If you have a copy,
compare it against the four hazards above rather than trusting this file's
section numbering, which was reconstructed and will not line up.
