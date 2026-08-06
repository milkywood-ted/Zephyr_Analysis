#!/usr/bin/env python3
"""Reproduce the extension-mechanism counts quoted in 09-확장-메커니즘.md.

Document 09 stated its numbers with the shell commands that produced them, but
nothing re-ran them, so the figures could drift from the tree without notice.
This script recomputes each one.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
Z = ROOT / "zephyr"
if not Z.is_dir():
    sys.exit(f"zephyr 소스 트리를 찾을 수 없습니다: {Z}")


def count_lines(pattern, paths, include=("*.c", "*.h"), regex=True):
    n = 0
    for base in paths:
        for pat in include:
            for f in (Z / base).rglob(pat):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                n += len(re.findall(pattern, text, re.M)) if regex else text.count(pattern)
    return n


def count_files(pattern, paths, include=("*.c", "*.h")):
    s = set()
    for base in paths:
        for pat in include:
            for f in (Z / base).rglob(pat):
                try:
                    if re.search(pattern, f.read_text(encoding="utf-8", errors="replace"), re.M):
                        s.add(f)
                except OSError:
                    pass
    return len(s)


results = [
    ("arch_interface.h 의 arch_* 선언",
     len(re.findall(r"^[a-zA-Z].*\barch_[a-z_0-9]+\(",
                    (Z / "include/zephyr/arch/arch_interface.h").read_text(errors="replace"), re.M)), 75),
    ("kernel_arch_interface.h 의 arch_* 언급",
     len(re.findall(r"arch_[a-z_0-9]+\(",
                    (Z / "kernel/include/kernel_arch_interface.h").read_text(errors="replace"))), 45),
    # 문서 §12 의 방법론대로 .c 만 센다. 헤더에는 __weak 를 언급하는 주석이 있어
    # .h 를 포함하면 정의가 아닌 산문이 잡힌다 (kernel_arch_interface.h:591).
    ("__weak 를 담은 파일 (kernel)", count_files(r"__weak", ["kernel"], ("*.c",)), 8),
    ("__weak 를 담은 파일 (arch)", count_files(r"__weak", ["arch"], ("*.c",)), 28),
    ("STRUCT_SECTION_FOREACH 순회 지점",
     count_lines(r"STRUCT_SECTION_FOREACH", ["kernel", "subsys"], ("*.c",)), 241),
    ("DEVICE_API( 를 쓰는 파일",
     count_files(r"DEVICE_API\(", ["drivers", "include"]), 2012),
    ("DT_INST_FOREACH_STATUS_OKAY 를 쓰는 파일",
     count_files(r"DT_INST_FOREACH_STATUS_OKAY", ["drivers"]), 1884),
    ("__syscall 선언", count_lines(r"^__syscall", ["include/zephyr"], ("*.h",)), 673),
    ("EXPORT_SYMBOL( 호출",
     count_lines(r"EXPORT_SYMBOL\(", ["kernel", "lib", "subsys"], ("*.c",)), 68),
]

fail = 0
print(f"{'항목':<42}{'실측':>8}{'문서':>8}  판정")
for label, actual, documented in results:
    ok = actual == documented
    fail += 0 if ok else 1
    print(f"{label:<42}{actual:>8}{documented:>8}  {'OK' if ok else 'MISMATCH'}")

print(f"\n결과: {'문서 수치와 일치' if fail == 0 else str(fail) + '건 불일치 — 09 문서를 갱신할 것'}")
sys.exit(1 if fail else 0)
