#!/usr/bin/env python3
"""
analyze_form.py — 첨부 서식(HWPX) 해부 → FormProfile (Phase 0.5)

왜 필요한가
-----------
`kordoc profile` 은 **표 서식**(테두리·음영·열폭·셀 글꼴)만 준다. 실측 확인된 사실이다.
"간트 표가 4행이다 / SWOT가 4분면이다 / 과제 배지가 6개다" 같은 **의미론적 칸 구조**는
주지 않는다. 그런데 기획을 앞에서 구속하는 건 바로 그쪽이다.

  [hard] 서식이 첨부됐다 → 칸이 기획을 **앞에서** 구속한다
         간트 4행인데 과제를 6개 기획하면 다시 쓴다. 사후 검증은 늦다.

그래서 이 스크립트는 **제약 문장(constraints)** 을 만들어 낸다. Phase 0.5 의 G1 게이트에서
사용자에게 그대로 읽어 주기 위한 것이다 —
"서식이 과제 4칸을 요구하므로 개선과제를 4개로 잡겠습니다."

무엇을 재는가
-------------
  slots             SWOT 4분면 · 과제 배지 · 간트 행/열 · 참고 칸 · 현행☞개선 박스 · 표 목록
  layout_signature  취지 글상자 · 강조 박스 · 라벨 밀도 · 결론 화살표 · 각주 체계 ·
                    붙임 · 결재란 · 제목 자간 벌리기 · 러닝헤더  (references/07 §3)
  styles            글꼴·charPr·paraPr·borderFill **ID 목록**
                    (실제 주입은 kordoc `--profile` 이 한다 — 여기서는 세어만 둔다)

사용법
------
  python analyze_form.py 서식.hwpx -o .gov-report/01-form-profile.json
  python analyze_form.py 서식.hwpx --binding soft      # 레퍼런스일 뿐 구속하지 않음

출력 계약: exit 0=정상 · 3=실행 오류
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from lxml import etree  # noqa: E402

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
}

# ── 칸을 알아보는 낱말 ────────────────────────────────────────────────────
KW_SWOT = ("강점", "약점", "기회", "위협")
KW_GANTT = ("월", "주차", "분기", "일정", "기간", "추진일정")
KW_TASK = re.compile(r"(?:추진)?과제\s*[0-9①-⑩]|\[과제")
KW_APPROVAL = ("담당", "주무관", "팀장", "과장", "국장", "실장", "부구청장",
               "결재", "전결", "협조")
KW_BEFORE = ("현행", "As-Is", "현재")
KW_AFTER = ("개선", "To-Be", "향후")
KW_REF = ("참고", "비고", "붙임")

RE_ATTACH = re.compile(r"^\s*(붙\s*임|별\s*첨|첨\s*부)\s*[0-9]*\s*[.:]?")
RE_END_MARK = re.compile(r"끝\s*\.")
RE_LABEL = re.compile(r"^\s*[○◦❍]\s*\([^)]{1,20}\)")     # ○ (민원 집중) …
RE_DETAIL = re.compile(r"^\s*[-·]\s*\([^)]{1,20}\)")
RE_SPACED_TITLE = re.compile(r"^(?:\S\s){3,}\S\s*$")       # 정 책 기 획 보 고 서
EMPHASIS = ("◈", "◇", "▣", "■")
NOTE_MARKS = {"*": "word", "※": "block", "▸": "citation"}


def text_of(el) -> str:
    """문단 단위로 줄을 나눈다 — 칸 안이 몇 줄인지를 세야 하기 때문."""
    ps = el.findall(".//hp:p", NS)
    if ps:
        return "\n".join("".join(t.text or "" for t in q.findall(".//hp:t", NS))
                         for q in ps)
    return "".join(t.text or "" for t in el.findall(".//hp:t", NS))


def read_xml(z: zipfile.ZipFile, name: str):
    return etree.fromstring(z.read(name))


def cell_grid(tbl) -> list[list[str]]:
    """표를 [행][열] 문자열 격자로 편다. 병합은 펴지 않고 있는 셀만 담는다."""
    grid: list[list[str]] = []
    for tr in tbl.findall("hp:tr", NS):
        grid.append([text_of(tc).strip() for tc in tr.findall("hp:tc", NS)])
    return grid


def classify_table(grid: list[list[str]]) -> str:
    flat = " ".join(c for row in grid for c in row)
    head = " ".join(grid[0]) if grid else ""
    if sum(1 for k in KW_SWOT if k in flat) >= 3:
        return "swot"
    if any(k in head for k in KW_GANTT) and len(grid) >= 2:
        return "gantt"
    if any(k in flat for k in KW_APPROVAL) and len(grid) <= 3:
        return "approval"
    # 현행↔개선은 나란한 칸이어야 대비 박스다. 한 칸짜리 제목에 '개선방안'이 든 것과 다르다
    if (max((len(r) for r in grid), default=0) >= 2
            and any(b in flat for b in KW_BEFORE) and any(a in flat for a in KW_AFTER)):
        return "before_after"
    if any(k in head for k in KW_REF):
        return "reference"
    if len(grid) == 1 and len(grid[0]) == 1:
        return "box"
    return "content"


def analyze_slots(tables: list[dict]) -> dict:
    slots: dict = {"swot_slots": 0, "swot_items_per_quadrant": 0,
                   "gantt_rows": 0, "gantt_cols": 0,
                   "task_badges": 0, "ref_cells": 0, "before_after_boxes": 0,
                   "tables_total": len(tables)}
    for t in tables:
        role, grid = t["role"], t["grid"]
        if role == "swot":
            slots["swot_slots"] = 4
            items = [len([ln for ln in c.split("\n") if ln.strip()])
                     for row in grid for c in row if c.strip()]
            slots["swot_items_per_quadrant"] = max(items) if items else 0
        elif role == "gantt":
            slots["gantt_rows"] = max(0, len(grid) - 1)     # 헤더 제외
            slots["gantt_cols"] = max((len(r) for r in grid), default=0)
        elif role == "reference":
            slots["ref_cells"] += 1
        elif role == "before_after":
            slots["before_after_boxes"] += 1
    return slots


def box_lines(txt: str) -> list[str]:
    """글상자가 몇 행인지 센다.

    한 칸 안의 □ 항목 여럿이 **하나의 문단**으로 들어있는 경우가 실제로 있다
    (골든 서식의 취지 상자가 그렇다 — 문단 1개에 □ 3개). 문단 수로만 세면 1행이
    되어 '취지 글상자 4행 이내' 규칙을 잴 수 없다. 그래서 마커로도 끊는다.
    """
    lines = [ln for ln in txt.split("\n") if ln.strip()]
    if len(lines) > 1:
        return lines
    parts = [s for s in re.split(r"(?=[□○])", txt) if s.strip()]
    return parts if len(parts) > 1 else lines


def analyze_layout(paras: list[str], tables: list[dict]) -> dict:
    body = [p for p in paras if p.strip()]
    n = len(body) or 1

    # 취지 글상자 — 문서 앞머리 1칸짜리 표 중 '제목 상자'가 아닌 것
    # (제목은 짧고 마커가 없다. 취지는 □·○ 항목이 여럿이거나 길다)
    purpose = None
    for t in tables[:5]:
        if t["role"] != "box":
            continue
        txt = t["grid"][0][0]
        lines = box_lines(txt)
        if len(lines) < 2 and len(txt) < 60:
            continue                          # 제목 상자다
        purpose = {"present": True, "max_lines": len(lines) or 1,
                   "no_period": not txt.rstrip().endswith(".")}
        break
    if purpose is None:
        purpose = {"present": False, "max_lines": 0, "no_period": None}

    approval = {"present": False, "labels": [], "delegation": None}
    for t in tables:
        if t["role"] != "approval":
            continue
        labels = [c for row in t["grid"] for c in row
                  if c and any(k in c for k in KW_APPROVAL) and len(c) <= 8]
        approval = {"present": True, "labels": labels,
                    "delegation": "전결" if any("전결" in c for c in labels) else None}
        break

    att = [p for p in body if RE_ATTACH.match(p)]
    notes: dict[str, int] = {}
    for p in body:
        c = p.lstrip()[:1]
        if c in NOTE_MARKS:
            notes[c] = notes.get(c, 0) + 1

    return {
        "purpose_box": purpose,
        "approval_line": approval,
        "emphasis_boxes": {e: sum(1 for p in body if p.lstrip().startswith(e))
                           for e in EMPHASIS},
        "summary_block": any("<요약>" in p or "《요약》" in p for p in body),
        "label_density": {
            "item_pct": round(100 * sum(1 for p in body if RE_LABEL.match(p)) / n),
            "detail_pct": round(100 * sum(1 for p in body if RE_DETAIL.match(p)) / n)},
        "conclusion_arrow": {
            "marker": "⇒",
            "count": sum(1 for p in body if p.lstrip().startswith(("⇒", "☞", "➡")))},
        "footnote_scheme": {NOTE_MARKS[k]: v for k, v in notes.items()},
        "attachment": {"count": len(att),
                       "end_mark": any(RE_END_MARK.search(p) for p in body[-6:])},
        "title_letter_spacing": any(RE_SPACED_TITLE.match(p) for p in body[:12]),
    }


def build_constraints(slots: dict, layout: dict) -> list[str]:
    """G1 게이트에서 사용자에게 그대로 읽어 줄 제약 문장."""
    out: list[str] = []
    if slots["gantt_rows"]:
        out.append("간트 표가 {}행이다 — 추진과제를 {}개로 맞춘다".format(
            slots["gantt_rows"], slots["gantt_rows"]))
    if slots["swot_slots"]:
        out.append("SWOT 4분면 · 분면당 최대 {}줄".format(
            slots["swot_items_per_quadrant"] or "?"))
    if slots["before_after_boxes"]:
        out.append("현행↔개선 대비 박스 {}개 — 문제와 방안을 1:1로 짝짓는다".format(
            slots["before_after_boxes"]))
    if slots["task_badges"]:
        out.append("과제 배지 {}개".format(slots["task_badges"]))
    if layout["approval_line"]["present"]:
        out.append("결재선 {} — 직제와 맞는지 확인 필요".format(
            "·".join(layout["approval_line"]["labels"]) or "(라벨 미상)"))
    if layout["purpose_box"]["present"]:
        out.append("보고 취지 글상자 {}행 — 이 안에 목적을 담는다".format(
            layout["purpose_box"]["max_lines"]))
    if layout["attachment"]["count"]:
        out.append("붙임 {}건 칸 — 본문 언급 순서와 번호를 맞춘다".format(
            layout["attachment"]["count"]))
    if not out:
        out.append("칸 구조가 잡히지 않았다 — 서식이 아니라 일반 문서일 수 있다")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="첨부 서식 HWPX 해부 → FormProfile")
    ap.add_argument("hwpx")
    ap.add_argument("-o", "--output")
    ap.add_argument("--binding", choices=["hard", "soft"], default="hard",
                    help="hard=칸이 기획을 구속 · soft=참조값 (references/07 §2)")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    try:
        src = Path(args.hwpx)
        z = zipfile.ZipFile(src)
        sect_names = sorted(n for n in z.namelist()
                            if re.match(r"Contents/section\d+\.xml$", n))
        if not sect_names:
            raise RuntimeError("section XML 이 없다 — HWPX 가 아니거나 손상")

        paras: list[str] = []
        tables: list[dict] = []
        badges = 0
        for name in sect_names:
            root = read_xml(z, name)
            for p in root.iter("{%s}p" % NS["hp"]):
                # 표 안 문단은 표 쪽에서 따로 센다
                if p.find(".//hp:tbl", NS) is not None:
                    continue
                t = text_of(p).strip()
                if t:
                    paras.append(t)
                    badges += len(KW_TASK.findall(t))
            for tbl in root.iter("{%s}tbl" % NS["hp"]):
                grid = cell_grid(tbl)
                if not grid:
                    continue
                tables.append({"rows": len(grid),
                               "cols": max((len(r) for r in grid), default=0),
                               "header": grid[0][:8],
                               "role": classify_table(grid),
                               "grid": grid})

        slots = analyze_slots(tables)
        slots["task_badges"] = badges + sum(
            len(KW_TASK.findall(c)) for t in tables for row in t["grid"] for c in row)
        layout = analyze_layout(paras, tables)

        styles: dict[str, int] = {}
        if "Contents/header.xml" in z.namelist():
            h = read_xml(z, "Contents/header.xml")
            for tag, key in (("font", "fonts"), ("charPr", "char_props"),
                             ("paraPr", "para_props"), ("borderFill", "border_fills")):
                styles[key] = len(h.findall(".//hh:" + tag, NS))

        profile = {
            "schema_version": "1.0",
            "source": src.name,
            "binding": args.binding,
            "_binding_note": ("hard: 이 칸 수가 Phase 3·4 기획을 앞에서 구속한다. "
                              "soft: 사후 대조용 참조값일 뿐이다."),
            "slots": slots,
            "layout_signature": layout,
            "styles": styles,
            "tables": [{k: v for k, v in t.items() if k != "grid"} for t in tables],
            "constraints": build_constraints(slots, layout),
            "kordoc_profile_hint": ("표 테두리·음영·열폭은 `kordoc profile <서식> -o "
                                    ".gov-report/form.kordoc.json` 으로 따로 뽑아 "
                                    "generate --profile 로 주입한다"),
        }
        out_json = json.dumps(profile, ensure_ascii=False, indent=2)
        if args.output:
            p = Path(args.output)
            p.parent.mkdir(parents=True, exist_ok=True)
            io.open(p, "w", encoding="utf-8").write(out_json)
        print(out_json)

        if not args.json_only:
            w = sys.stderr
            print("\n[analyze_form] {}  binding={}".format(src.name, args.binding), file=w)
            print("  문단 {} · 표 {} ({})".format(
                len(paras), len(tables),
                ", ".join("{}:{}".format(t["role"], t["rows"]) for t in tables[:8])),
                file=w)
            print("  ▶ 이 서식이 거는 제약", file=w)
            for c in profile["constraints"]:
                print("     · " + c, file=w)
        return 0

    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[analyze_form] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
