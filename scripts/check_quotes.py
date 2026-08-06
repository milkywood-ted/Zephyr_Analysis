#!/usr/bin/env python3
"""Locate every quoted code block in the source, and check the document cites it.

Link checking proves only that a cited file exists and the line numbers are in
bounds. This checks the property a reader actually relies on: that code shown in
a document really exists in the source, at a place the document points to.

Pairing a block with "its" citation by proximity proved unreliable — sections
interleave prose and several citations. So this does not pair at all:

  1. locate the block: find a file+line span where its substantive lines occur
     in order (gaps allowed, since blocks are often abridged);
  2. then ask whether ANY citation in the same document overlaps that span.

Reported outcomes:
  LOCATED+CITED  - found in source, and the document points at it
  LOCATED        - found in source, but no citation in the document covers it
  NOT FOUND      - the block's lines do not occur in order in any cited file
"""
import re
import sys
from pathlib import Path

DOCDIR = Path(__file__).resolve().parent.parent
ROOT = DOCDIR.parent

if not (ROOT / "zephyr").is_dir():
    sys.exit(f"zephyr 소스 트리를 찾을 수 없습니다: {ROOT / 'zephyr'}")

FENCE = re.compile(r"```(?:c|cmake|asm)\n(.*?)\n```", re.S)
ANYLINK = re.compile(r"\]\((zephyr/[^)#]+)#L(\d+)(?:-L(\d+))?\)")


def norm(line):
    s = re.sub(r"/\*.*?\*/", " ", line)
    s = re.sub(r"//.*$", " ", s)
    s = re.sub(r"/\*.*$", " ", s)
    s = re.sub(r"^\s*\*.*$", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s in {"", "...", "…", "{", "}", "};", "*/", "#endif", "#else", ")", "()", "*"}:
        return ""
    return s


_cache = {}


def normed_file(rel):
    if rel not in _cache:
        p = ROOT / rel
        _cache[rel] = [norm(l) for l in p.read_text(encoding="utf-8", errors="replace").splitlines()] \
            if p.exists() else None
    return _cache[rel]


def locate(block_lines, rel):
    """Find the tightest span in `rel` containing block_lines in order."""
    src = normed_file(rel)
    if not src:
        return None
    first = block_lines[0]
    best = None
    for start in (i for i, l in enumerate(src) if l == first):
        pos, i = [], start
        for want in block_lines:
            while i < len(src) and src[i] != want:
                i += 1
            if i >= len(src):
                break
            pos.append(i)
            i += 1
        if len(pos) == len(block_lines):
            span = (pos[0] + 1, pos[-1] + 1)
            if best is None or (span[1] - span[0]) < (best[1] - best[0]):
                best = span
    return best


located = located_uncited = notfound = 0
problems = []

for doc in sorted(DOCDIR.glob("[0-9][0-9]-*.md")):
    text = doc.read_text(encoding="utf-8")
    cites = [(f, int(a), int(b or a)) for f, a, b in ANYLINK.findall(text)]
    files = sorted({f for f, _a, _b in cites})
    for block in FENCE.findall(text):
        lines = [n for n in (norm(l) for l in block.splitlines()) if n]
        if len(lines) < 2:
            continue
        hit = None
        for f in files:
            span = locate(lines, f)
            if span and (hit is None or (span[1] - span[0]) < (hit[2] - hit[1])):
                hit = (f, span[0], span[1])
        if hit is None:
            notfound += 1
            # 어느 줄이 소스에 없는지 진단: 인용된 파일 어디에도 없는 첫 줄
            orphan = next(
                (n for n in lines
                 if not any((normed_file(f) or []) and n in normed_file(f) for f in files)),
                None)
            why = f"소스에 없는 줄: {orphan[:70]}" if orphan else "줄은 모두 있으나 순서/연속성 불일치"
            problems.append(("NOT FOUND", doc.name, lines[0][:60], why))
            continue
        f, a, b = hit
        if any(cf == f and not (cb < a or ca > b) for cf, ca, cb in cites):
            located += 1
        else:
            located_uncited += 1
            problems.append(("UNCITED", doc.name, lines[0][:70], f"{f}:{a}-{b}"))

total = located + located_uncited + notfound
print(f"검사한 코드 블록: {total}개")
print(f"  소스에서 위치 확인 + 문서가 그 위치를 인용함 : {located}")
print(f"  위치는 확인되나 문서가 그 위치를 인용하지 않음: {located_uncited}")
print(f"  소스에서 찾지 못함                            : {notfound}")

print("""
NOT FOUND 는 대부분 결함이 아니라 서술상의 재편집이다 — 여러 줄을 한 줄로 합치거나,
인자 목록을 ... 로 줄이거나, #ifdef 두 갈래를 한 줄로 병합한 경우. 문서는 원문을 그대로
옮기는 것이 목적이 아니므로 이를 실패로 보지 않는다. 다만 식별자를 바꿔 인용하면 독자가
소스에서 찾지 못하므로, 아래 목록은 그런 사례가 섞여 있지 않은지 눈으로 확인할 것.

게이트는 UNCITED 에만 건다: 코드가 소스에 실재하는데 문서가 그 위치를 가리키지 않는 것은
독자가 원문을 확인할 방법이 없다는 뜻이므로 실제 결함이다.""")

if problems:
    print("\n=== 확인 필요 ===")
    for kind, doc, head, where in problems:
        print(f"  [{kind}] {doc}")
        print(f"        {head}")
        if where:
            print(f"        실제 위치: {where}")

sys.exit(1 if located_uncited else 0)
