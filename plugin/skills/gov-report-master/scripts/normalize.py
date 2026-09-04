#!/usr/bin/env python3
"""
normalize.py — kordoc 파싱 산출물 정규화 (모든 프로파일 작업의 선행 조건)

왜 필요한가
-----------
kordoc으로 정부 보고서 PDF를 파싱하면 글머리표가 깨진다. 실측(법제처 2019 결과보고서):

    ○ (U+25CB) 0회         ← 마커 카운트가 0으로 나온다
    ᄋ (U+110B 한글자모) 7회  ← 실제로는 이것
    · (U+00B7) 0회 / ᆞ (U+119E) 2회
    "#### □ 시스템 개요"    ← □가 마크다운 헤딩으로 승격
    "- - 입법계획…"         ← 대시 이중화
    "…, 법제 / 업무 평가…"   ← PDF 조판 줄바꿈이 문단 안에 잔존
    "### ✚"                ← 도형 잔재가 헤딩화

정규화 없이 뽑은 숫자를 프로파일에 넣으면 모든 후속 게이트가 잘못된 기준으로 작동한다.

사용법
------
  python normalize.py <input.pdf|hwp|hwpx|md> [-o out.md] [--stats] [--json-only]

  입력이 문서 파일이면 kordoc으로 먼저 파싱한다(`npx -y kordoc@^4 <file>`).
  .md/.txt 이면 바로 정규화한다.

출력 계약
---------
  exit 0 = 정상 / 3 = 실행 오류
  --json-only : stdout 에 통계 JSON 만
  기본        : stdout 에 정규화된 마크다운, stderr 에 사람용 리포트
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# 실행 환경이 cp949 일 수 있다(확인됨: stdout=cp949, locale=cp949).
# 한글 경로·본문을 출력하는 순간 UnicodeEncodeError 가 나므로 첫머리에서 강제한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
ALIASES_PATH = HERE.parent / "profiles" / "marker-aliases.json"

DOC_EXT = {".pdf", ".hwp", ".hwpx", ".hml", ".docx", ".xlsx"}


def load_aliases(path: Path = ALIASES_PATH) -> dict:
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def kordoc_parse(src: Path) -> str:
    """문서 파일을 kordoc으로 마크다운 변환. stdout 을 그대로 받는다."""
    cmd = ["npx", "-y", "kordoc@^4", str(src)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        shell=(os.name == "nt"),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"kordoc 파싱 실패 (exit {proc.returncode})\n{proc.stderr[:800]}")
    return proc.stdout


# ── 구조 판정 ────────────────────────────────────────────────────────────
MARKER_CHARS = "□○◻☐■ᄋㅇ❍●◦〇-−–—－·ᆞ‧・•･ㆍ※*＊⇒➡⇨▸▶►‣"

RE_STRUCTURAL = re.compile(
    "^\\s*(?:"
    "#{1,6}\\s"                       # 헤딩
    "|\\|"                            # 표 행
    "|```"                            # 코드펜스
    "|>"                              # 인용
    "|[0-9]+[.)]\\s"                  # 번호 목록
    "|[가-힣][.)]\\s"                  # 가. 나. 목록
    "|\\([0-9가-힣]+\\)\\s"            # (1) (가)
    "|[①-⑮㉮-㉵]"                     # 원문자
    "|[Ⅰ-Ⅹ]\\s*\\."                   # 로마숫자 장
    "|[" + re.escape(MARKER_CHARS) + "]\\s"   # 글머리표
    ")"
)
RE_HANGUL = re.compile("[가-힣]")
RE_BLOCK_PASSTHROUGH = re.compile("^\\s*(?:\\||```|>|#{1,6}\\s)")


def is_structural(line: str) -> bool:
    return not line.strip() or bool(RE_STRUCTURAL.match(line))


# ── 단계별 변환 ──────────────────────────────────────────────────────────
def strip_artifacts(lines: list[str], cfg: dict) -> tuple[list[str], int]:
    pats = [re.compile(p) for p in cfg["artifact_patterns"]["drop_line_regex"]]
    out, dropped = [], 0
    for ln in lines:
        if any(p.match(ln) for p in pats):
            dropped += 1
            continue
        out.append(ln)
    return out, dropped


def demote_headings(lines: list[str], cfg: dict) -> tuple[list[str], int]:
    """'#### □ 제목' → '□ 제목' (글머리표가 헤딩으로 승격된 것을 되돌림)."""
    if not cfg["heading_demotion"]["enabled"]:
        return lines, 0
    lvl = cfg["heading_demotion"]["max_heading_level"]
    pat = re.compile(
        "^(#{1," + str(lvl) + "})\\s+([" + re.escape(MARKER_CHARS) + "])\\s+(.*)$"
    )
    out, n = [], 0
    for ln in lines:
        m = pat.match(ln)
        if m:
            out.append(m.group(2) + " " + m.group(3))
            n += 1
        else:
            out.append(ln)
    return out, n


def collapse_doubled(lines: list[str], cfg: dict) -> tuple[list[str], int]:
    pat = re.compile(cfg["doubled_marker"]["pattern"])
    rep = cfg["doubled_marker"]["replace"]
    out, n = [], 0
    for ln in lines:
        new = pat.sub(rep, ln)
        if new != ln:
            n += 1
        out.append(new)
    return out, n


def build_marker_map(cfg: dict) -> dict[str, str]:
    m = {}
    for canon, alist in cfg["aliases"].items():
        for a in alist:
            if a != canon:
                m[a] = canon
    return m


RE_LEAD = re.compile("^(\\s*)([^\\s])(\\s+)(.*)$")


def normalize_markers(lines: list[str], cfg: dict) -> tuple[list[str], Counter]:
    """줄머리 글머리표를 정본으로. 본문 중간 나열 구분자도 통일."""
    mmap = build_marker_map(cfg)
    sepmap = {a: "·" for a in cfg["separator_aliases"]["·"]}
    changed = Counter()
    out = []
    for ln in lines:
        m = RE_LEAD.match(ln)
        if m and m.group(2) in mmap:
            canon = mmap[m.group(2)]
            changed[m.group(2) + "→" + canon] += 1
            ln = m.group(1) + canon + m.group(3) + m.group(4)
        for a, c in sepmap.items():
            if a in ln:
                changed["sep " + a + "→" + c] += ln.count(a)
                ln = ln.replace(a, c)
        out.append(ln)
    return out, changed


def looks_finished(buf: str, cfg: dict) -> bool:
    s = buf.rstrip()
    if not s:
        return True
    if s[-1] in cfg["sentence_end"]["punctuation"]:
        return True
    return any(s.endswith(e) for e in cfg["sentence_end"]["nominal_endings"])


def rejoin_paragraphs(lines: list[str], cfg: dict) -> tuple[list[str], int]:
    """PDF 조판 줄바꿈으로 잘린 논리 문단을 되붙인다.

    한글-한글 경계는 공백 없이, 그 외는 공백 하나로 잇는다
    (한국어 PDF는 줄바꿈 자리에 공백을 남기지 않는 것이 일반적).
    """
    out = []
    buf = None
    joins = 0

    def flush():
        nonlocal buf
        if buf is not None:
            out.append(buf)
            buf = None

    for ln in lines:
        if is_structural(ln):
            if ln.strip() and not RE_BLOCK_PASSTHROUGH.match(ln):
                # 글머리표 줄 = 새 논리 문단의 시작 → 버퍼로 받아 이어붙일 수 있게
                flush()
                buf = ln.rstrip()
            else:
                flush()
                out.append(ln)
            continue
        if buf is not None and not looks_finished(buf, cfg):
            left, right = buf.rstrip(), ln.strip()
            glue = ""
            if not (left and right and RE_HANGUL.match(left[-1]) and RE_HANGUL.match(right[0])):
                glue = " "
            buf = left + glue + right
            joins += 1
        else:
            flush()
            buf = ln.rstrip()
    flush()
    return out, joins


# ── 통계 ─────────────────────────────────────────────────────────────────
RE_LEAD_MARK = re.compile("^\\s*([^\\s])\\s")


def marker_stats(text: str) -> dict:
    lines = text.split("\n")
    lead = Counter()
    for ln in lines:
        m = RE_LEAD_MARK.match(ln)
        if m and m.group(1) in MARKER_CHARS:
            lead[m.group(1)] += 1
    body = [l for l in lines if l.strip()]
    lens = [len(l.strip()) for l in body]
    return {
        "lines_total": len(lines),
        "lines_nonempty": len(body),
        "marker_counts": dict(lead),
        "tables": sum(1 for l in lines if l.lstrip().startswith("|")),
        "headings": sum(1 for l in lines if re.match("^#{1,6}\\s", l)),
        "char_total": sum(lens),
        "char_median": sorted(lens)[len(lens) // 2] if lens else 0,
    }


def normalize_text(raw: str, cfg: dict) -> tuple[str, dict]:
    lines = raw.replace("\r\n", "\n").split("\n")
    before = marker_stats("\n".join(lines))

    lines, dropped = strip_artifacts(lines, cfg)
    lines, demoted = demote_headings(lines, cfg)
    lines, doubled = collapse_doubled(lines, cfg)
    lines, changed = normalize_markers(lines, cfg)
    lines, joins = rejoin_paragraphs(lines, cfg)

    text = "\n".join(lines)
    report = {
        "operations": {
            "artifacts_dropped": dropped,
            "headings_demoted": demoted,
            "doubled_markers_collapsed": doubled,
            "markers_normalized": dict(changed),
            "paragraphs_rejoined": joins,
        },
        "before": before,
        "after": marker_stats(text),
    }
    return text, report


def main() -> int:
    ap = argparse.ArgumentParser(description="kordoc 파싱 산출물 정규화")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--stats", action="store_true", help="stderr 에 통계 리포트")
    ap.add_argument("--json-only", action="store_true", help="stdout 에 통계 JSON 만")
    ap.add_argument("--aliases", default=str(ALIASES_PATH))
    args = ap.parse_args()

    try:
        cfg = load_aliases(Path(args.aliases))
        src = Path(args.input)
        if not src.exists():
            raise FileNotFoundError(src)
        if src.suffix.lower() in DOC_EXT:
            raw = kordoc_parse(src)
        else:
            raw = io.open(src, encoding="utf-8").read()
        text, report = normalize_text(raw, cfg)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[normalize] 실행 오류: " + str(e), file=sys.stderr)
        return 3

    if args.output:
        io.open(args.output, "w", encoding="utf-8").write(text)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif not args.output:
        sys.stdout.write(text)

    if args.stats or args.json_only:
        b, a, op = report["before"], report["after"], report["operations"]
        w = sys.stderr
        print("[normalize] " + src.name, file=w)
        print("  줄       {:>5} → {:>5}   (문단 재결합 {}건)".format(
            b["lines_nonempty"], a["lines_nonempty"], op["paragraphs_rejoined"]), file=w)
        print("  헤딩     {:>5} → {:>5}   (글머리표 복원 {}건)".format(
            b["headings"], a["headings"], op["headings_demoted"]), file=w)
        print("  도형잔재 제거 {}건 · 이중대시 {}건".format(
            op["artifacts_dropped"], op["doubled_markers_collapsed"]), file=w)
        print("  마커 before: " + str(b["marker_counts"]), file=w)
        print("  마커 after : " + str(a["marker_counts"]), file=w)
        if op["markers_normalized"]:
            print("  치환: " + str(op["markers_normalized"]), file=w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
