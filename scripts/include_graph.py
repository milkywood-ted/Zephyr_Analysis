#!/usr/bin/env python3
"""Extract the #include graph of the Zephyr tree, bucketed by architectural layer.

Not a C preprocessor: it resolves each #include textually against the tree's
real header locations. Unresolved includes are counted and reported rather
than silently dropped, so the numbers can be read with their error bar.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# <workspace>/zephyr/ , resolved from this script's location:
#   <workspace>/<repo>/scripts/include_graph.py
ZEPHYR = Path(__file__).resolve().parent.parent.parent / "zephyr"

if not ZEPHYR.is_dir():
    sys.exit(f"zephyr 소스 트리를 찾을 수 없습니다: {ZEPHYR}")

INCLUDE_RE = re.compile(r'^\s*#\s*include\s+([<"])([^">]+)[">]', re.M)

# Layer buckets, checked in order. (label, path prefix predicate)
LAYERS = [
    ("include/zephyr", lambda p: p.startswith("include/zephyr/")),
    ("arch",           lambda p: p.startswith("arch/")),
    ("kernel",         lambda p: p.startswith("kernel/")),
    ("lib",            lambda p: p.startswith("lib/")),
    ("subsys",         lambda p: p.startswith("subsys/")),
    ("drivers",        lambda p: p.startswith("drivers/")),
    ("soc",            lambda p: p.startswith("soc/")),
    ("boards",         lambda p: p.startswith("boards/")),
    ("modules",        lambda p: p.startswith("modules/")),
    ("tests",          lambda p: p.startswith("tests/") or p.startswith("samples/")),
]

SCAN_LAYERS = {"arch", "kernel", "lib", "subsys", "drivers", "soc"}


def layer_of(rel: str):
    for name, pred in LAYERS:
        if pred(rel):
            return name
    return None


def build_header_index():
    """basename and tail-path -> list of repo-relative header paths."""
    by_tail = defaultdict(list)
    by_base = defaultdict(list)
    for h in ZEPHYR.rglob("*.h"):
        rel = h.relative_to(ZEPHYR).as_posix()
        if rel.startswith(("tests/", "samples/", "doc/")):
            continue
        by_base[h.name].append(rel)
        parts = rel.split("/")
        for i in range(len(parts)):
            by_tail["/".join(parts[i:])].append(rel)
    return by_tail, by_base


def main():
    by_tail, by_base = build_header_index()

    edges = Counter()          # (src_layer, dst_layer) -> count
    unresolved = Counter()     # src_layer -> count
    kernel_private_users = Counter()   # who includes kernel/include/*.h
    arch_iface_users = Counter()       # who includes arch_interface / arch/ headers

    files_scanned = 0

    for src in ZEPHYR.rglob("*"):
        if src.suffix not in (".c", ".h"):
            continue
        rel = src.relative_to(ZEPHYR).as_posix()
        slayer = layer_of(rel)
        if slayer not in SCAN_LAYERS:
            continue
        files_scanned += 1
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        owndir = "/".join(rel.split("/")[:-1])

        for quote, inc in INCLUDE_RE.findall(text):
            target = None
            # 1. quoted include: try relative to the including file first
            if quote == '"':
                cand = f"{owndir}/{inc}" if owndir else inc
                cand = str(Path(cand))
                if (ZEPHYR / cand).exists():
                    target = cand
            # 2. <zephyr/...> and other rooted forms: exact tail match
            if target is None:
                hits = by_tail.get(inc)
                if hits:
                    # prefer a hit under include/zephyr for <zephyr/...> forms,
                    # otherwise prefer one sharing the longest path prefix
                    if inc.startswith("zephyr/"):
                        pref = [h for h in hits if h.startswith("include/zephyr/")]
                        hits = pref or hits
                    target = min(hits, key=lambda h: (0 if h.startswith(owndir) else 1, len(h)))
            # 3. bare basename fallback
            if target is None and "/" not in inc:
                hits = by_base.get(inc)
                if hits:
                    target = min(hits, key=lambda h: (0 if h.startswith(owndir) else 1, len(h)))

            if target is None:
                unresolved[slayer] += 1
                continue

            dlayer = layer_of(target)
            if dlayer is None:
                unresolved[slayer] += 1
                continue

            edges[(slayer, dlayer)] += 1

            if target.startswith("kernel/include/"):
                kernel_private_users[slayer] += 1
            if target.startswith("arch/") and target.endswith(".h"):
                arch_iface_users[slayer] += 1

    order = ["include/zephyr", "arch", "kernel", "lib", "subsys", "drivers", "soc"]
    print(f"스캔한 파일: {files_scanned}개\n")

    print("=== 계층 간 #include 횟수 (행=포함하는 쪽, 열=포함되는 쪽) ===")
    hdr = f"{'':<16}" + "".join(f"{c[:9]:>11}" for c in order)
    print(hdr)
    for s in order:
        if s not in SCAN_LAYERS:
            continue
        row = f"{s:<16}"
        for d in order:
            n = edges.get((s, d), 0)
            row += f"{(n if n else '.'):>11}"
        print(row)

    print("\n=== 미해결 include (참고: 오차 범위) ===")
    for s in order:
        if unresolved.get(s):
            print(f"  {s:<16} {unresolved[s]}")

    print("\n=== kernel/include/*.h (커널 내부 전용 헤더)를 포함하는 계층 ===")
    for s, n in kernel_private_users.most_common():
        print(f"  {s:<16} {n}")

    print("\n=== arch/**/*.h 를 포함하는 계층 ===")
    for s, n in arch_iface_users.most_common():
        print(f"  {s:<16} {n}")


if __name__ == "__main__":
    sys.exit(main())
