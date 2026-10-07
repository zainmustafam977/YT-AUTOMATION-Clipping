"""
AST-based Import Graph and Call Hierarchy Analyzer
"""

import ast
import os
import json
from pathlib import Path
from collections import defaultdict, deque

def analyze_imports():
    root = Path('.')
    py_files = sorted(list(root.glob('shorts_engine/**/*.py')) + [Path('cli.py'), Path('run.py')])

    file_to_imports = {}
    package_to_packages = defaultdict(set)
    reverse_deps = defaultdict(set)

    for p in py_files:
        rel = p.as_posix()
        file_to_imports[rel] = set()
        try:
            tree = ast.parse(p.read_text(encoding='utf-8', errors='ignore'))
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name
                        if mod.startswith('shorts_engine') or mod in ('cli', 'run'):
                            file_to_imports[rel].add(mod)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod = node.module
                        if mod.startswith('shorts_engine') or mod in ('cli', 'run'):
                            file_to_imports[rel].add(mod)
        except Exception as e:
            print(f"Error parsing {rel}: {e}")

    # Build package level graph
    def to_pkg(item: str) -> str:
        if item.startswith('shorts_engine'):
            parts = item.replace('\\', '/').split('/') if '/' in item else item.split('.')
            if len(parts) >= 3 and parts[1] == 'services':
                return f"shorts_engine.services.{parts[2]}"
            elif len(parts) >= 2:
                return f"shorts_engine.{parts[1]}"
            return 'shorts_engine'
        return item

    for src_file, imported_mods in file_to_imports.items():
        src_pkg = to_pkg(src_file)
        for imp in imported_mods:
            tgt_pkg = to_pkg(imp)
            if src_pkg != tgt_pkg:
                package_to_packages[src_pkg].add(tgt_pkg)
                reverse_deps[tgt_pkg].add(src_pkg)

    # Detect cycles in package graph using Tarjan's or simple DFS
    def find_cycles():
        visited = {}
        rec_stack = set()
        cycles = []

        def dfs(node, path):
            visited[node] = True
            rec_stack.add(node)
            path.append(node)

            for neighbor in package_to_packages.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in rec_stack:
                    # Cycle found
                    idx = path.index(neighbor)
                    cycle = path[idx:] + [neighbor]
                    cycles.append(cycle)

            rec_stack.remove(node)
            path.pop()

        for pkg in list(package_to_packages.keys()):
            if pkg not in visited:
                dfs(pkg, [])

        return cycles

    cycles = find_cycles()

    # Determine topological ordering / layer hierarchy
    # Leaf packages: out-degree == 0 (import nothing inside shorts_engine or only config/db)
    results = {
        "file_to_imports": {k: sorted(list(v)) for k, v in file_to_imports.items()},
        "package_graph": {k: sorted(list(v)) for k, v in package_to_packages.items()},
        "reverse_deps": {k: sorted(list(v)) for k, v in reverse_deps.items()},
        "cycles": cycles,
    }

    with open('import_analysis.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

    print(f"Parsed {len(py_files)} files. Package nodes: {len(package_to_packages)}. Cycles found: {len(cycles)}.")
    return results

if __name__ == '__main__':
    analyze_imports()
