import re
import glob

patterns = [
    re.compile(r"subprocess\.(run|Popen|check_output|call)"),
    re.compile(r"SubprocessWatchdog\.run"),
]

print("=== SUBPROCESS AND WATCHDOG CALL SITES ===")
for f in sorted(glob.glob("shorts_engine/**/*.py", recursive=True)):
    with open(f, "r", encoding="utf-8", errors="ignore") as file:
        for idx, line in enumerate(file, 1):
            for p in patterns:
                if p.search(line):
                    print(f"{f}:{idx}: {line.strip()}")
                    break
