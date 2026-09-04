#!/usr/bin/env python3
"""
style_guard.py — 산출 HWPX 의 편집 품질 검사 (T1~T10)

무엇을 재는가
-------------
`density_guard.py` 가 **얼마나 담겼나**(밀도·분량)를 본다면, 이 스크립트는
**어떻게 앉혔나**(글꼴·크기·줄간격·내어쓰기·볼드)를 본다. 내용이 옳아도 편집이
위계를 못 드러내면 반려되기 때문에 별도 게이트로 둔다.

  T1 위계별 글자 크기가 표준표와 일치      MAJOR
  T2 인접 위계 크기 역전                   CRITICAL
  T3 본문 줄간격 160% 벗어난 문단 ≤ 25%    MAJOR
  T4 마커 문단의 내어쓰기 적용             MAJOR
  T5 본문=명조 / 제목·항목=고딕            MINOR
  T6 본문 볼드 비율 30~70%                 MINOR
  T7 문서 전체 글자 크기 종수 ≤ 7          MINOR
  T8 좌·우 여백 20mm · 표 글자 < 본문      MINOR
  T9 서식 목록 id 가 연속 오름차순         CRITICAL
  T10 채움 없는 자리의 흰 글자             CRITICAL

기준값은 `profiles/typography.json` (기재부·인사혁신처·농식품부 3건 실측).
근거와 해설은 `references/10-typography.md`.

왜 마크다운이 아니라 HWPX 를 여는가
-----------------------------------
크기·줄간격·내어쓰기·글꼴은 **원고에 없는 값**이다. kordoc 이 붙이거나, 한글에서
사람이 손대면서 깨진다. 그래서 최종 산출물을 직접 연다.

사용법
------
  python style_guard.py <결과.hwpx>
  python style_guard.py <결과.hwpx> --typography profiles/typography.json
  python style_guard.py <결과.hwpx> --json-only

출력 계약 (references/09-evaluation.md §8)
  exit 0=PASS · 1=FAIL · 2=PASS-WITH-WARNINGS · 3=실행 오류
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

HWPUNIT_MM = 7200 / 25.4
PAPER = "#FFFFFF"          # 종이색. 이 색 글자가 채움 없는 자리에 있으면 안 보인다.

# 줄 첫머리 마커 → 위계 id. 순서가 곧 우선순위다(긴 것부터).
MARKER_LEVEL = [
    ("**", "N2"), ("※", "N1"), ("*", "N1"),
    ("□", "L3"), ("○", "L4"), ("◦", "L4"), ("❍", "L4"), ("ㅇ", "L4"),
    ("ㆍ", "L6"), ("·", "L6"), ("-", "L5"), ("‐", "L5"), ("–", "L5"),
]
# kordoc 은 장 배너를 「Ⅰ」 셀과 「보고 개요」 셀로 쪼개 넣는다.
# 점이 없는 로마숫자 단독 셀도 장으로 인식해야 배너가 표 본문으로 잘못 잡히지 않는다.
RE_CHAPTER = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*(?:[.·]|$)")
# 절(節)은 네모박스 숫자로 조판된다. 텍스트로는 ❶❷❸·【1】·①②③·1. 로 나타난다.
RE_SECTION = re.compile(r"^(?:[①-⑮]|[❶-❿]|【\d{1,2}】|\[\d+\]|\d{1,2}\s*[.)])\s")
# 「2026. 9. 2.」 같은 날짜가 절 번호로 잡히지 않게 막는다
RE_DATE = re.compile(r"^\d{4}\s*\.")

GOTHIC = ("고딕", "헤드라인", "돋움", "윤고딕", "견고딕", "Gothic", "hdr", "Godic")
MYEONGJO = ("명조", "바탕", "Batang", "신명", "Myeongjo")


def load_typography(path: str | None) -> dict:
    if not path:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "profiles", "typography.json")
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# ---------------------------------------------------------------- HWPX 읽기

def _num(el) -> int:
    return int(el.get("value")) if el is not None and el.get("value") else 0


def read_hwpx(path: str):
    """문단마다 (마커위계, 글꼴, 크기, 볼드글자수, 총글자수, 줄간격, 내어쓰기, 표안여부)."""
    z = zipfile.ZipFile(path)
    head = ET.fromstring(z.read("Contents/header.xml"))

    fonts = {f.get("id"): f.get("face") for f in head.iter(HH + "font")}

    # ★한글은 charPrIDRef·paraPrIDRef 를 id 속성이 아니라 **목록의 순번**으로 읽는다.
    # 여기서도 순번으로 키를 잡아야 게이트가 한글과 같은 문서를 본다. 순번과 id 가
    # 어긋나 있으면 T9 가 CRITICAL 로 잡고, 그때는 나머지 측정값을 믿을 수 없다.
    cp_list = list(head.iter(HH + "charPr"))
    id_order = {"charProperties": [cp.get("id") for cp in cp_list]}
    chars = {}
    for i, cp in enumerate(cp_list):
        h, ref = cp.get("height"), cp.find(HH + "fontRef")
        chars[str(i)] = {
            "pt": round(int(h) / 100, 1) if h else None,
            "bold": cp.find(HH + "bold") is not None,
            "font": fonts.get(ref.get("hangul")) if ref is not None else None,
            "color": (cp.get("textColor") or "").upper(),
            "shade": (cp.get("shadeColor") or "none").lower(),
        }
    pp_list = list(head.iter(HH + "paraPr"))
    id_order["paraProperties"] = [pp.get("id") for pp in pp_list]
    paras = {}
    for i, pp in enumerate(pp_list):
        ind = next((e for e in pp.iter() if e.tag.endswith("}intent")), None)
        ls = next((e for e in pp.iter() if e.tag.endswith("}lineSpacing")), None)
        paras[str(i)] = {"indent": _num(ind), "ls": _num(ls)}

    # 칸 배경색. 색 배경 위의 흰 글자는 정상이므로 T10 에서 걸러내는 데 쓴다.
    bf_list = list(head.iter(HH + "borderFill"))
    id_order["borderFills"] = [bf.get("id") for bf in bf_list]
    id_order["styles"] = [st.get("id") for st in head.iter(HH + "style")]
    bf_base = int(bf_list[0].get("id")) if bf_list else 0
    fills = {}
    for i, bf in enumerate(bf_list):
        wb = next((e for e in bf.iter() if e.tag.endswith("}winBrush")), None)
        fills[str(bf_base + i)] = (wb.get("faceColor") or "").upper() if wb is not None else ""

    page = None
    rows = []
    for name in z.namelist():
        if not re.match(r"Contents/section\d+\.xml$", name):
            continue
        sec = ET.fromstring(z.read(name))
        if page is None:
            m = next((e for e in sec.iter() if e.tag.endswith("}pageMargin")), None)
            if m is not None:
                page = {k: int(m.get(k, 0)) for k in
                        ("left", "right", "top", "bottom", "header", "footer")}
        # 장·절 배너는 1행짜리 표로 들어온다. 실제 데이터 표(2행 이상)와 구분해 둔다.
        in_table, in_data_table, cell_fill = set(), set(), {}
        for tb in sec.iter(HP + "tbl"):
            ids = {id(e) for e in tb.iter(HP + "p")}
            in_table |= ids
            if len(tb.findall(HP + "tr")) >= 2:
                in_data_table |= ids
            for tc in tb.iter(HP + "tc"):
                face = fills.get(tc.get("borderFillIDRef"), "")
                for e in tc.iter(HP + "p"):
                    cell_fill[id(e)] = face
        for p in sec.iter(HP + "p"):
            segs = []
            for r in p.findall(HP + "run"):
                t = "".join("".join(x.itertext()) for x in r.findall(HP + "t"))
                if t:
                    segs.append((r.get("charPrIDRef"), t))
            text = "".join(t for _, t in segs).strip()
            if not text:
                continue
            pr = paras.get(p.get("paraPrIDRef"), {})
            first = chars.get(segs[0][0], {})
            nb = sum(len(t) for c, t in segs if chars.get(c, {}).get("bold"))
            # 칸 배경도 글자색도 흰색이면 화면에서 사라진다(글자 음영이 있으면 제외).
            back = (cell_fill.get(id(p)) or "").upper()
            lit = back and back != PAPER
            hidden = sum(
                len(t) for c, t in segs
                if chars.get(c, {}).get("color") == PAPER
                and not lit and chars.get(c, {}).get("shade", "none") == "none")
            rows.append({
                "hidden_chars": hidden,
                "text": text,
                "level": classify(text),
                "font": first.get("font"),
                "pt": first.get("pt"),
                "sizes": {chars.get(c, {}).get("pt") for c, _ in segs} - {None},
                "bold_chars": nb,
                "total_chars": len(text),
                "ls": pr.get("ls"),
                "indent": pr.get("indent"),
                "in_table": id(p) in in_table,
                "in_data_table": id(p) in in_data_table,
            })
    return rows, page, id_order


def classify(text: str) -> str | None:
    if RE_DATE.match(text):
        return None
    if RE_CHAPTER.match(text):
        return "L1"
    if RE_SECTION.match(text):
        return "L2"
    for mk, lv in MARKER_LEVEL:
        if text.startswith(mk):
            return lv
    return None


# ---------------------------------------------------------------- 검사

def family_of(font: str | None) -> str | None:
    if not font:
        return None
    if any(k in font for k in GOTHIC):
        return "gothic"
    if any(k in font for k in MYEONGJO):
        return "myeongjo"
    return None


def check(rows, page, typo, id_order=None) -> tuple[list, dict]:
    spec = {lv["id"]: lv for lv in typo["levels"]}
    g = typo["gates"]
    findings, metrics = [], {}

    body = [r for r in rows if not r["in_table"]]
    by_level = defaultdict(list)
    for r in body:
        if r["level"]:
            by_level[r["level"]].append(r)

    def add(tid, sev, msg, hint, loc=""):
        findings.append({"id": tid, "severity": sev, "phase_to_retry": "Phase 7",
                         "location": loc, "message": msg, "fix_hint": hint})

    # T1 위계별 크기
    tol = g["T1_level_size"]["tolerance_pt"]
    observed = {}
    for lv, items in sorted(by_level.items()):
        sizes = [r["pt"] for r in items if r["pt"]]
        if not sizes:
            continue
        med = sorted(sizes)[len(sizes) // 2]
        observed[lv] = med
        want = spec.get(lv, {}).get("pt")
        if want and abs(med - want) > tol:
            add("T1", g["T1_level_size"]["severity"],
                "%s(%s) 글자 크기 %.1fp — 표준 %.1fp" % (lv, spec[lv]["role"], med, want),
                "kordoc --sizes / --pt 로 %s 를 %.1fp 로 맞춘다" % (lv, want),
                "%s %d문단" % (lv, len(items)))
    metrics["level_pt"] = observed

    # T2 인접 위계 역전 (큰 위계가 작은 위계보다 작으면 안 된다)
    order = [lv["id"] for lv in typo["levels"] if lv["id"] in observed]
    for a, b in zip(order, order[1:]):
        if observed[a] < observed[b]:
            add("T2", g["T2_size_inversion"]["severity"],
                "위계 역전 — %s %.1fp < %s %.1fp" % (a, observed[a], b, observed[b]),
                "상위 위계가 하위보다 작다. 크기 지정을 다시 본다.")

    # T3 본문 줄간격
    # 중앙값으로 재면 모집단이 바뀔 때 문서를 고치지 않아도 판정이 뒤집힌다.
    # (붙임 산문을 표로 옮기자 160% 문단이 105→25개로 줄어 서식이 원래 갖고 있던
    #  145%가 중앙값이 되면서 MAJOR 가 떴다 — 본문 조판은 한 줄도 바뀌지 않았다.)
    # 그래서 「표준을 벗어난 문단이 몇 %인가」로 잰다.
    ls = [r["ls"] for r in by_level.get("L4", []) if r["ls"]]
    if ls:
        t, d = g["T3_body_spacing"]["target"], g["T3_body_spacing"]["tol"]
        off = [v for v in ls if abs(v - t) > d]
        ratio = len(off) / len(ls)
        metrics["body_line_spacing"] = sorted(ls)[len(ls) // 2]
        metrics["body_spacing_off_ratio"] = round(ratio, 2)
        if ratio > g["T3_body_spacing"].get("max_off_ratio", 0.25):
            seen = ", ".join("%d%%×%d" % kv for kv in Counter(off).most_common(3))
            add("T3", g["T3_body_spacing"]["severity"],
                "본문 줄간격이 표준 %d%%를 벗어난 문단 %.0f%%(%d/%d) — %s"
                % (t, ratio * 100, len(off), len(ls), seen),
                "kordoc --line-spacing %d (서식 템플릿마다 값이 다르면 먼저 통일한다)" % t)

    # T4 내어쓰기
    need = [r for r in body if r["level"] in ("L3", "L4", "L5", "L6", "N1", "N2")]
    hung = [r for r in need if (r["indent"] or 0) < 0]
    if need:
        ratio = len(hung) / len(need)
        metrics["hanging_ratio"] = round(ratio, 3)
        if ratio < g["T4_hanging_indent"]["min_ratio"]:
            add("T4", g["T4_hanging_indent"]["severity"],
                "마커 문단 %d개 중 %d개만 내어쓰기 — %.0f%%" % (len(need), len(hung), ratio * 100),
                "둘째 줄이 첫 글자에 맞아야 한다. 한글에서 손댔다면 문단모양을 다시 준다.")

    # T5 글꼴 계열
    bad = []
    for lv, items in by_level.items():
        want = spec.get(lv, {}).get("family")
        if not want:
            continue
        for r in items:
            fam = family_of(r["font"])
            if fam and fam != want:
                bad.append((lv, r["font"], r["text"][:24]))
    metrics["family_violations"] = len(bad)
    if bad:
        add("T5", g["T5_font_family"]["severity"],
            "글꼴 계열 위반 %d건 — 예: %s 에 %s" % (len(bad), bad[0][0], bad[0][1]),
            "본문은 명조(함초롬바탕), 제목·항목은 고딕(HY헤드라인M)",
            bad[0][2])

    # T6 볼드 비율
    tot = sum(r["total_chars"] for r in by_level.get("L4", []) + by_level.get("L5", []))
    nb = sum(r["bold_chars"] for r in by_level.get("L4", []) + by_level.get("L5", []))
    if tot:
        ratio = nb / tot
        metrics["bold_ratio"] = round(ratio, 3)
        lo, hi = g["T6_bold_ratio"]["min"], g["T6_bold_ratio"]["max"]
        if not (lo <= ratio <= hi):
            add("T6", g["T6_bold_ratio"]["severity"],
                "본문 볼드 비율 %.0f%% — 정상 %.0f~%.0f%%" % (ratio * 100, lo * 100, hi * 100),
                "핵심 명사구를 굵게. 조사·어미·문장 전체는 제외."
                if ratio < lo else "볼드가 과하다. 명사구 단위로 줄인다.")

    # T7 크기 종수
    kinds = {s for r in rows for s in r["sizes"]}
    metrics["size_variety"] = len(kinds)
    if len(kinds) > g["T7_size_variety"]["max"]:
        add("T7", g["T7_size_variety"]["severity"],
            "글자 크기 %d종 사용 — 상한 %d종" % (len(kinds), g["T7_size_variety"]["max"]),
            "크기 층은 32/16/15/12 네 개가 기본. 중간값을 없앤다.",
            " ".join("%.1f" % s for s in sorted(kinds, reverse=True)))

    # T8 여백 · 표 글자
    want_mm = g["T8_margin_table"]["margin_mm"]
    if page:
        lr = (round(page["left"] / HWPUNIT_MM), round(page["right"] / HWPUNIT_MM))
        metrics["margin_mm"] = {"left": lr[0], "right": lr[1]}
        if lr != (want_mm, want_mm):
            add("T8", g["T8_margin_table"]["severity"],
                "좌·우 여백 %d/%dmm — 표준 %d/%dmm" % (lr[0], lr[1], want_mm, want_mm),
                "여백을 줄여 분량을 맞추지 않는다. 글을 줄인다.")
    # kordoc 은 장·절 배너를 1행짜리 표로 만든다. 이걸 표 본문으로 세면
    # "표 글자가 본문보다 크다"는 오탐이 난다 — 2행 이상 데이터 표만 센다.
    tsz = [r["pt"] for r in rows if r["in_data_table"] and r["pt"]]
    bsz = observed.get("L4")
    if tsz and bsz:
        med = sorted(tsz)[len(tsz) // 2]
        metrics["table_pt"] = med
        if med >= bsz:
            add("T8", g["T8_margin_table"]["severity"],
                "표 글자 %.1fp ≥ 본문 %.1fp" % (med, bsz),
                "표는 본문보다 1~2p 작게(12~13p)")

    # T9 참조 무결성 — 한글은 참조 번호를 순번으로 읽는다
    gt9 = g.get("T9_id_order")
    if gt9 and id_order:
        broken = []
        for box in gt9["boxes"]:
            ids = [int(i) for i in id_order.get(box, []) if i is not None]
            if not ids:
                continue
            if ids != list(range(ids[0], ids[0] + len(ids))):
                broken.append("%s(%s…)" % (box, ",".join(str(i) for i in ids[-6:])))
        metrics["id_order_ok"] = not broken
        if broken:
            add("T9", gt9["severity"],
                "선언 순서가 번호 순이 아님 — " + " / ".join(broken),
                "한글은 charPrIDRef·paraPrIDRef 를 id 가 아니라 목록의 순번으로 읽는다. "
                "새 서식을 덧붙일 때 id 오름차순으로 이어 붙여라. "
                "이 항목이 깨지면 T1~T8 의 측정값도 믿을 수 없다.")

    # T10 안 보이는 글자 — 채움 없는 자리의 흰 글자
    gt10 = g.get("T10_invisible_text")
    if gt10:
        hid = [r for r in rows if r.get("hidden_chars")]
        n = sum(r["hidden_chars"] for r in hid)
        metrics["invisible_chars"] = n
        if n:
            add("T10", gt10["severity"],
                "채움 없는 자리에 흰 글자 %d자(%d문단)" % (n, len(hid)),
                "글자색이 종이색과 같으면 화면에서 사라진다. 텍스트 추출로는 안 잡히니 "
                "한글로 PDF 를 내보내 픽셀로 확인하라.",
                hid[0]["text"][:40])

    metrics["paragraphs"] = len(rows)
    metrics["body_paragraphs"] = len(body)
    metrics["level_counts"] = {k: len(v) for k, v in sorted(by_level.items())}
    return findings, metrics


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="산출 HWPX 편집 품질 검사 (T1~T10)")
    ap.add_argument("hwpx")
    ap.add_argument("--typography", help="기준 JSON (기본 profiles/typography.json)")
    ap.add_argument("--json-only", action="store_true")
    a = ap.parse_args()

    typo = load_typography(a.typography)
    rows, page, id_order = read_hwpx(a.hwpx)
    if not rows:
        print(json.dumps({"verdict": "ERROR", "message": "본문 문단을 찾지 못했다"},
                         ensure_ascii=False))
        return 3

    findings, metrics = check(rows, page, typo, id_order)
    sev = Counter(f["severity"] for f in findings)
    if sev["CRITICAL"] or sev["MAJOR"]:
        verdict, code = "FAIL", 1
    elif findings:
        verdict, code = "PASS-WITH-WARNINGS", 2
    else:
        verdict, code = "PASS", 0

    print(json.dumps({"verdict": verdict, "findings": findings,
                      "metrics": metrics, "unverifiable": []},
                     ensure_ascii=False, indent=2))

    if not a.json_only:
        w = sys.stderr
        print("\n[style_guard] %s → %s" % (os.path.basename(a.hwpx), verdict), file=w)
        print("  본문 %d문단 · 볼드 %s · 줄간격 %s%% · 크기 %s종 · 내어쓰기 %s"
              % (metrics.get("body_paragraphs", 0),
                 ("%.0f%%" % (metrics["bold_ratio"] * 100)) if "bold_ratio" in metrics else "-",
                 metrics.get("body_line_spacing", "-"),
                 metrics.get("size_variety", "-"),
                 ("%.0f%%" % (metrics["hanging_ratio"] * 100)) if "hanging_ratio" in metrics else "-"),
              file=w)
        for f in findings:
            print("  %-8s %-3s %s" % (f["severity"], f["id"], f["message"]), file=w)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        sys.exit(3)
