import os
import re

pat_write = re.compile(r'(\.write_text\(|json\.dump\(|open\([^,]+,\s*["\'][waWA]|write_bytes\()')
pat_subproc = re.compile(r'subprocess\.(run|Popen|check_call|check_output|call)')

print("=== PART A: ALL WRITE CALL SITES IN SHORTS_ENGINE ===")
for root, dirs, files in sorted(os.walk('shorts_engine')):
    for f in sorted(files):
        if f.endswith('.py'):
            p = os.path.join(root, f)
            with open(p, encoding='utf-8', errors='ignore') as fh:
                for idx, line in enumerate(fh, 1):
                    if pat_write.search(line):
                        print(f"{p}:{idx}: {line.strip()}")

print("\n=== PART B: ALL SUBPROCESS / POPEN CALL SITES IN SHORTS_ENGINE ===")
for root, dirs, files in sorted(os.walk('shorts_engine')):
    for f in sorted(files):
        if f.endswith('.py'):
            p = os.path.join(root, f)
            with open(p, encoding='utf-8', errors='ignore') as fh:
                for idx, line in enumerate(fh, 1):
                    if pat_subproc.search(line):
                        print(f"{p}:{idx}: {line.strip()}")
