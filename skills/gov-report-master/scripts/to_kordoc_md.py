#!/usr/bin/env python3
"""
to_kordoc_md.py — 우리 원고(마크다운) → kordoc 입력 계약으로 변환 (출력측 정규화)

왜 필요한가
-----------
두 계약이 정반대다.

  우리 템플릿·실무 관행 : 글머리표를 **글자로 직접 쓴다**
        ## □ (현행 제도) …
         ○ 신체적 희생 있음 …
           - 최근 3년 사망자 …

  kordoc 입력 계약      : 부호를 **중첩 리스트 깊이**에서 생성한다
        (references/06 §6 — "항목부호는 손으로 타이핑하지 않는다")

그대로 넘기면 실측으로 확인된 파손이 난다.

  1) `## □-1  (1단계) …` + 보고서 프리셋 기본 `--h2-marker box`
     → **`□ □-1  (1단계)`** 이중 마커 (골든 원고에서 3건 발생)
  2) ` ○ …` 는 리스트가 아니므로 paraPr 내어쓰기가 안 붙는다
     → 둘째 줄 정렬이 깨지고 마커는 그냥 글자로 남는다

변환 규칙
---------
헤딩   `## □ X`      → `## X`            (kordoc 이 □ 를 붙인다)
       `## □-N  X`   → `## (N단계) X`     (□-N 은 우리 내부 번호 — 텍스트로 승격)
본문   `□ X`         → `- X`     (깊이 0)
       ` ○ X`        → `  - X`   (깊이 1)
       `   - X`      → `    - X` (깊이 2)
       `     · X`    → `      - X` (깊이 3)
주석   `※ X` · `* X` · `▸ X`  → 그대로 (kordoc 이 주석으로 처리)
상호참조 `☞ 개선방안 □-2` → `☞ 개선방안 (2단계)`
보존   표(`|`) · 인용(`>`) · 코드펜스 · 수평선 · 서명란은 손대지 않는다

사용법
------
  python to_kordoc_md.py <원고.md> -o <kordoc용.md> [--report]
  python to_kordoc_md.py <원고.md> --check      # 변환 없이 위반만 보고

출력 계약: exit 0 정상 / 3 실행 오류. --check 는 변환 필요 시 2.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent

# 깊이 → 우리가 쓰는 글머리표
DEPTH_MARKER = {0: "□", 1: "○", 2: "-", 3: "·"}
MARKER_DEPTH = {"□": 0, "○": 1, "-": 2, "·": 3, "•": 3, "ㆍ": 3}

# 주석 계열은 깊이 변환 대상이 아니다
NOTE_MARKERS = {"※", "*", "▸", "⇒"}

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
RE_H_BOXNUM = re.compile(r"^([□○])-([0-9]+)\s*")      # □-1
RE_H_MARKER = re.compile(r"^([□○\-·])\s+")            # □ / ○ + 공백
RE_BODY = re.compile(r"^(\s*)([□○\-·•ㆍ])\s+(.*)$")
RE_NOTE = re.compile(r"^(\s*)([※*▸⇒])\s+(.*)$")
RE_XREF = re.compile(r"([□○])-([0-9]+)")
RE_PASSTHROUGH = re.compile(r"^\s*(?:\||>|```|-{3,}|\*{3,}|_{3,}|<)")
RE_SIGNATURE = re.compile(r"(작성자\s*[:：]|\(인\)|서명\s*또는\s*인)")


def convert(text: str) -> tuple[str, list[dict]]:
    """우리 원고를 kordoc 입력 계약으로 바꾼다. (결과, 변경내역)."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    changes: list[dict] = []
    in_fence = False

    for i, ln in enumerate(lines, start=1):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(ln)
            continue
        if in_fence or RE_PASSTHROUGH.match(ln) or RE_SIGNATURE.search(ln) or not ln.strip():
            out.append(ln)
            continue

        # ── 헤딩 ──────────────────────────────────────────────
        mh = RE_HEADING.match(ln)
        if mh:
            hashes, body = mh.group(1), mh.group(2)
            mb = RE_H_BOXNUM.match(body)
            if mb:
                rest = body[mb.end():].lstrip()
                # 이미 (N단계) 같은 괄호 라벨이 뒤따르면 번호를 버린다
                new_body = rest if rest.startswith("(") else "({}단계) {}".format(
                    mb.group(2), rest)
                out.append("{} {}".format(hashes, new_body))
                changes.append({"line": i, "kind": "heading_boxnum",
                                "before": ln.strip()[:70], "after": new_body[:70],
                                "why": "'□-N' + --h2-marker box 는 '□ □-N' 이중 마커가 된다"})
                continue
            mm = RE_H_MARKER.match(body)
            if mm:
                new_body = body[mm.end():]
                out.append("{} {}".format(hashes, new_body))
                changes.append({"line": i, "kind": "heading_marker",
                                "before": ln.strip()[:70], "after": new_body[:70],
                                "why": "kordoc 이 --h2-marker 로 부호를 붙인다"})
                continue
            out.append(ln)
            continue

        # ── 주석 줄은 그대로 ─────────────────────────────────
        if RE_NOTE.match(ln):
            out.append(ln)
            continue

        # ── 본문 글머리표 → 중첩 리스트 깊이 ──────────────────
        mbdy = RE_BODY.match(ln)
        if mbdy:
            lead, mark, rest = mbdy.group(1), mbdy.group(2), mbdy.group(3)
            depth = MARKER_DEPTH.get(mark)
            if depth is None:
                out.append(ln)
                continue
            # '-' 는 우리 관행상 깊이 2지만, 이미 리스트 문법일 수도 있다.
            # 선행 공백이 깊이와 어긋나면 마커 기준을 신뢰한다(템플릿이 정본).
            new = "{}- {}".format("  " * depth, rest)
            out.append(new)
            if new != ln:
                changes.append({"line": i, "kind": "body_marker",
                                "before": ln.strip()[:70], "after": new.strip()[:70],
                                "why": "깊이 {} → kordoc 이 '{}' 를 붙이고 내어쓰기를 준다".format(
                                    depth, DEPTH_MARKER[depth])})
            continue

        out.append(ln)

    result = "\n".join(out)

    # ── 상호참조 재작성 ──────────────────────────────────────
    def _xref(m: re.Match) -> str:
        return "({}단계)".format(m.group(2))

    xref_n = len(RE_XREF.findall(result))
    if xref_n:
        result = RE_XREF.sub(_xref, result)
        changes.append({"line": 0, "kind": "xref",
                        "before": "□-N", "after": "(N단계)",
                        "why": "헤딩을 '(N단계)' 로 바꿨으므로 참조도 맞춘다 ({}건)".format(xref_n)})
    return result, changes


def main() -> int:
    ap = argparse.ArgumentParser(description="우리 원고 → kordoc 입력 계약")
    ap.add_argument("src")
    ap.add_argument("-o", "--output")
    ap.add_argument("--check", action="store_true", help="변환 없이 위반만 보고")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    try:
        src = Path(args.src)
        text = io.open(src, encoding="utf-8").read()
        result, changes = convert(text)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[to_kordoc_md] 실행 오류: " + str(e), file=sys.stderr)
        return 3

    kinds: dict[str, int] = {}
    for c in changes:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1

    report = {"verdict": "NEEDS_CONVERSION" if changes else "ALREADY_CONFORMANT",
              "counts": kinds, "changes": changes[:60]}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.check:
        if args.output:
            io.open(args.output, "w", encoding="utf-8").write(result)
        elif not args.json_only:
            sys.stdout.write(result)

    if not args.json_only:
        w = sys.stderr
        print("\n[to_kordoc_md] {} → {}".format(src.name, report["verdict"]), file=w)
        for k, v in kinds.items():
            print("  {:16} {}건".format(k, v), file=w)
        for c in changes[:8]:
            if c["line"]:
                print("   L{:<4} {}".format(c["line"], c["before"][:62]), file=w)
                print("          ↳ {}".format(c["after"][:62]), file=w)
    return 2 if (args.check and changes) else 0


if __name__ == "__main__":
    sys.exit(main())
