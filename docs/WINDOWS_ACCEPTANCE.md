# Windows Acceptance Procedure

**Status: BLOCKED — NOT VERIFIED BY EXECUTION.**
**Owner action required before this can be marked DONE-VERIFIED.**

---

## Status header (R4.1)

> **R4.1 — how this project labels work that a CI runner cannot verify.**
>
> An item whose acceptance requires an environment the runner does not have
> (a Windows VM, a browser binary, a GPU, a live third-party service) must be
> labelled with its real status, never inferred as passing. Specifically:
>
> 1. State the status explicitly: `BLOCKED` if it needs an environment nobody
>    has provisioned yet, `DEFERRED` if an attempt was made and exhausted.
> 2. Record **exactly what was executed** and **exactly what was not**. A
>    review, a static check, or a successful run on a different platform does
>    **not** count as execution.
> 3. Give the precise manual steps a human must perform to unblock it, and
>    what evidence to capture.
> 4. Do not mark it `DONE-VERIFIED` until someone has actually run it and
>    recorded the output.
>
> *This interpretation is recorded here because the rule was given in-session
> and is not otherwise defined in the repository. Correct this section if the
> intended wording differs.*

Applied to this document:

| | |
|---|---|
| **Executed** | Nothing. No step in this procedure has been run. |
| **Performed instead** | Static review of the original procedure; four hazards identified and corrected on paper. |
| **Environment needed** | A disposable Windows 10/11 VM with a pre-taken snapshot. |
| **Why not run here** | Standing instruction: never run this procedure on the user's everyday machine. No VM was available. |
| **Current status** | **BLOCKED** |

**DO NOT RUN THIS ON YOUR EVERYDAY MACHINE.**

---

## Why this document exists

The original procedure contained four defects that could destroy data or kill
unrelated processes on the machine running it. It was never safe to execute as
written. Because running it requires a disposable VM, it is delivered here
**corrected but unexecuted** — reviewed, not proven.

### Provenance

The source document this corrects was supplied in-session and was **not**
committed to the repository, so it cannot be diffed against. This file was
rebuilt from the hazard list recorded in `docs/agent/ledger.yaml`. Section
numbering and wording will therefore not line up with the original; the four
hazards and their corrections are preserved. If you have the original, compare
it against the hazards below rather than trusting this file's numbering.

---

## The four hazards and their corrections

### Hazard 1 — `HKLM` registry writes

**Original problem:** the procedure wrote to `HKLM` (HKEY_LOCAL_MACHINE).

**Why it is dangerous:** `HKLM` is machine-wide. A wrong write there affects
every user account and every application on the machine and cannot be undone by
logging out. On a shared or work machine this can break unrelated software.

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

**Why it is dangerous:** on a developer machine the PID occupying a port may be
a browser, an editor with unsaved work, a local database, or a corporate agent.
Killing it silently destroys unsaved work.

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
- The regex `^[Yy]$` requires a bare `y` or `Y`, so `yes` is not accepted by
  accident.
- Always show the process **name**, not just the PID, so the operator can make
  an informed decision.
- Always offer the non-destructive alternative (change the port) alongside the
  kill.

> Reference implementation: `setup.ps1` already does this correctly in its port
> check (roughly lines 628-644). The acceptance procedure should match that
> pattern rather than inventing its own.

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
   directory.** Not `X:\Program Files`, `X:\Windows`, `X:\Users`, or any parent
   of them, under any circumstances.
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
kills an **arbitrary, unrelated process** — or fails with a confusing error that
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

## How to unblock this document

Perform these steps in order, then update the status header:

1. Provision a Windows 10/11 VM (Hyper-V, VirtualBox, or a cloud VM).
2. Take a snapshot.
3. Run the corrected procedure end to end.
4. **Capture the actual console output** for each step.
5. Fix whatever the run reveals. **Expect corrections to be needed** — this has
   never been executed, so the review above is unproven.
6. Revert the snapshot.
7. Replace the status header with `DONE-VERIFIED`, recording the VM image, the
   date, and where the captured output is stored.

Until step 6 is complete this stays **BLOCKED**.

**Do not commit acceptance logs.** Run output is evidence, not source. Keep it
out of the repository.
