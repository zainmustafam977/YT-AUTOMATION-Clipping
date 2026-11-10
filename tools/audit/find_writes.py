import re
import glob

patterns = [
    re.compile(r"write_text\("),
    re.compile(r"json\.dump\("),
    re.compile(r"open\(.*['\"][wa]"),
]

print("=== PERSISTENT STATE WRITES IN SHORTS_ENGINE ===")
for f in sorted(glob.glob("shorts_engine/**/*.py", recursive=True)):
    with open(f, "r", encoding="utf-8", errors="ignore") as file:
        for idx, line in enumerate(file, 1):
            for p in patterns:
                if p.search(line):
                    print(f"{f}:{idx}: {line.strip()}")
                    break
