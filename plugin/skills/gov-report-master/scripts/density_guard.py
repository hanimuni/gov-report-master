#!/usr/bin/env python3
"""
density_guard.py — 산출 HWPX ↔ 유형 프로파일 밀도·정합 대조 (Q10~Q13, Q16, Q26)

무엇을 재는가
-------------
생성된 HWPX 의 본문을 뜯어 **레퍼런스 실측 범위 안에 있는가**를 본다.
"레퍼런스와 거의 같은 포맷"을 사후에 보증하는 게이트다.

  Q10 마커 밀도      프로파일 방향별(min/max/both)
  Q11 분량 편차      계열 min~max 범위
  Q12 문단별 급변    ±25% — **HWPX 레퍼런스가 있을 때만**
  Q13 쪽수          kordoc render 의 (N페이지) 파싱
  Q26 쪽당 밀도     쪽당 마커 수 · 쪽당 □ 상한 (references/14 §4)
  Q16 취지 글상자·붙임 존재

지표 신뢰도 (중요)
------------------
프로파일이 **PDF 레퍼런스**에서 나왔으면 문단·글자 지표를 게이트로 쓰면 안 된다.
PDF 조판 줄바꿈이 한 문단을 여러 줄로 쪼개 문단 수를 부풀리고 길이를 1/N 로 낮춘다.
`normalize.py` 가 되붙이지만 완전하지 않다. 프로파일의 `metric_trust` 필드가 정본이다.

  신뢰   marker_counts · table_rows · headings
  불신   lines_nonempty · char_total · char_median   ← Q11·Q12 를 이것으로 판정하지 않는다

또 하나: 한컴 저장본에는 `hp:linesegarray`(조판 캐시)가 있고 생성본에는 없다.
**구조 지문에서 반드시 제외**한다. 바이트·엔트리 비교는 무의미하다(생성 6파일 vs 한컴 11파일).

사용법
------
  python density_guard.py <결과.hwpx> --profile profiles/01-*.profile.json
  python density_guard.py <결과.hwpx> --profile … --reference <레퍼런스.hwpx>
  python density_guard.py <결과.hwpx> --profile … --lineage local_gian --pages 6

출력 계약 (references/09-evaluation.md §8)
  exit 0=PASS · 1=FAIL · 2=PASS-WITH-WARNINGS · 3=실행 오류
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent

NS_P = "http://www.hancom.co.kr/hwpml/2011/paragraph"
MARKERS = "□○-·※*⇒▸"

RE_PARA = re.compile(r"<hp:p\b[^>]*>(.*?)</hp:p>", re.S)
RE_TEXT = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
RE_LINESEG = re.compile(r"<hp:linesegarray\b.*?</hp:linesegarray>|<hp:linesegarray\b[^>]*/>", re.S)
RE_TBL = re.compile(r"<hp:tbl\b")
RE_PAGEBREAK = re.compile(r'pageBreak="1"')
RE_PAGES = re.compile(r"\(([0-9]+)페이지")
# 구조 마커만 밀도 게이트 대상. 주석 계열(※ * ▸ ⇒)은 저자 재량이라 개수로 판정하지 않는다.
STRUCT_MARKERS = "□○-·"
NOTE_MARKERS = "※*▸⇒"

RE_ATTACH = re.compile(r"^\s*(?:붙\s*임|별\s*첨|【\s*별첨)")
RE_END_MARK = re.compile(r"끝\s*\.")


def finding(fid: str, sev: str, phase: str, loc: str, msg: str, fix: str = "") -> dict:
    return {"id": fid, "severity": sev, "phase_to_retry": phase,
            "location": loc, "message": msg, "fix_hint": fix}


def unescape(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))


def read_body(hwpx: Path) -> dict:
    """HWPX 본문에서 문단·마커·표를 뽑는다. linesegarray 는 지문에서 제외."""
    z = zipfile.ZipFile(hwpx)
    sections = sorted(n for n in z.namelist()
                      if re.match(r"Contents/section\d+\.xml$", n))
    if not sections:
        raise RuntimeError("section XML 을 찾지 못했다 — HWPX 가 아니거나 손상")

    paras: list[str] = []
    tbl = pgb = 0
    for name in sections:
        xml = z.read(name).decode("utf-8", errors="replace")
        xml = RE_LINESEG.sub("", xml)          # 조판 캐시 제거
        tbl += len(RE_TBL.findall(xml))
        pgb += len(RE_PAGEBREAK.findall(xml))
        for body in RE_PARA.findall(xml):
            txt = unescape("".join(RE_TEXT.findall(body))).strip()
            if txt:
                paras.append(txt)

    lead: dict[str, int] = {}
    for p in paras:
        c = p.lstrip()[:1]
        if c in MARKERS:
            lead[c] = lead.get(c, 0) + 1

    lens = [len(p) for p in paras]
    return {
        "paragraphs": len(paras),
        "marker_counts": lead,
        "marker_total": sum(lead.values()),
        "tables": tbl,
        "page_breaks": pgb,
        "char_total": sum(lens),
        "char_median": sorted(lens)[len(lens) // 2] if lens else 0,
        "has_attachment": any(RE_ATTACH.match(p) for p in paras),
        "has_end_mark": any(RE_END_MARK.search(p) for p in paras),
        "_paras": paras,
    }


# Windows 의 npx 는 npx.cmd 다 — shell=False 로는 이름만으로 찾지 못한다
NPX = shutil.which("npx") or shutil.which("npx.cmd") or "npx"


def kordoc_pages(hwpx: Path) -> int | None:
    """render 가 stderr 로 내는 '(N페이지 …)' 를 파싱. SVG 는 만들지 않는다."""
    out = hwpx.parent / (hwpx.stem + ".__pagecount.svg")
    try:
        proc = subprocess.run(
            [NPX, "-y", "kordoc@^4", "render", str(hwpx), "-o", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        m = RE_PAGES.search(proc.stdout + proc.stderr)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            out.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def pick_lineage(prof: dict, want: str | None) -> tuple[str | None, dict | None]:
    lins = prof.get("lineages") or {}
    if not lins:
        return None, None
    key = want or prof.get("default_lineage") or sorted(lins)[0]
    return key, lins.get(key)


def check_markers(actual: dict, ref: dict, direction: str,
                  trusted: bool, absolute: bool = True) -> list[dict]:
    """방향(min/max/both)에 따라 마커 밀도를 본다.

    absolute=False 면 개수 자체는 판정하지 않는다. 레퍼런스의 min/max 는 **그 문서의
    절대 개수**여서 쪽수가 다르면 성립하지 않는다(6~8쪽 표본의 상한을 22쪽 문서에 걸면
    항상 초과한다). 쪽수를 아는 경우에는 쪽당으로 재는 Q26 에 판정을 넘긴다.
    """
    f = []
    if not trusted or not absolute:
        return f
    mc = (ref.get("density") or {}).get("marker_counts") or {}
    for mark, band in mc.items():
        if mark not in STRUCT_MARKERS:
            continue          # 주석 계열은 밀도로 판정하지 않는다
        got = actual["marker_counts"].get(mark, 0)
        lo, hi = band.get("min", 0), band.get("max", 0)
        if direction in ("min", "both") and got < lo:
            f.append(finding(
                "Q10", "MAJOR", "Phase 5", "마커 '{}'".format(mark),
                "'{}' {}개 — 레퍼런스 하한 {}개 미달".format(mark, got, lo),
                "항목을 더 세우거나 세부(-)를 붙인다. 한 장이 비어 보이면 실패로 본다"))
        if direction in ("max", "both") and hi and got > hi * 1.5:
            f.append(finding(
                "Q10", "MAJOR", "Phase 5", "마커 '{}'".format(mark),
                "'{}' {}개 — 레퍼런스 상한 {}개의 1.5배 초과".format(mark, got, hi),
                "이 유형은 짧을수록 확실하다. 항목을 줄이고 세부는 버린다"))
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description="산출 HWPX 밀도·정합 대조")
    ap.add_argument("hwpx")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--reference", help="레퍼런스 HWPX (있으면 문단 단위 비교 활성)")
    ap.add_argument("--lineage")
    ap.add_argument("--pages", type=int, help="쪽수를 직접 지정(render 생략)")
    ap.add_argument("--target-pages", help="목표 쪽수 범위 'min-max'")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    try:
        src = Path(args.hwpx)
        prof = json.load(io.open(args.profile, encoding="utf-8"))
        actual = read_body(src)
        findings: list[dict] = []
        notes: list[str] = []

        tid = prof.get("type_id", "?")
        direction = prof.get("gate_direction", "both")
        trust = prof.get("metric_trust", {})
        trusted_set = set(trust.get("trusted", []))
        marker_trusted = "marker_counts" in trusted_set
        para_trusted = "char_total" in trusted_set

        lin_key, lin = pick_lineage(prof, args.lineage)

        # ── Q10 마커 밀도 ────────────────────────────────────
        # 쪽수를 알고 프로파일에 쪽당 밴드가 있으면 절대 개수 비교를 하지 않는다 —
        # 같은 것을 두 기준으로 재면 Q10 과 Q26 이 서로 반대 판정을 낸다.
        per_page_band = (prof.get("density") or {}).get("markers_per_page")
        pages_known = args.pages is not None or not args.no_render
        absolute_ok = not (per_page_band and pages_known)
        if lin and prof.get("measured"):
            findings += check_markers(actual, lin, direction, marker_trusted, absolute_ok)
            if not marker_trusted:
                notes.append("마커 지표가 프로파일에서 신뢰 대상이 아니라 Q10 을 건너뛰었다")
            elif not absolute_ok:
                notes.append(
                    "Q10 절대 개수 비교를 건너뛰었다 — 레퍼런스 min/max 는 표본 문서의 "
                    "절대 개수라 쪽수가 다르면 성립하지 않는다. 쪽당으로 재는 Q26 이 판정한다")
        else:
            notes.append(
                "유형 {} 은 파생 프로파일(measured=false) — 절대값 게이트를 적용하지 않는다. "
                "방향({})만 참고한다".format(tid, direction))

        # ── Q11 분량 편차 · Q12 문단 급변 ────────────────────
        if para_trusted and lin:
            band = (lin.get("density") or {}).get("char_total") or {}
            lo, hi = band.get("min"), band.get("max")
            if lo and hi and not (lo * 0.85 <= actual["char_total"] <= hi * 1.15):
                findings.append(finding(
                    "Q11", "MAJOR", "Phase 5", "분량",
                    "본문 {}자 — 계열 {} 범위 {}~{}자 밖".format(
                        actual["char_total"], lin_key, lo, hi),
                    "범위 안으로 압축하거나 확장한다"))
        else:
            notes.append(
                "Q11·Q12(분량·문단 지표)를 건너뛰었다 — 프로파일이 PDF 레퍼런스 기반이라 "
                "조판 줄바꿈으로 문단 경계가 왜곡돼 있다. "
                "문단 단위 비교는 --reference 로 HWPX 를 줄 때만 수행한다")

        if args.reference:
            ref = read_body(Path(args.reference))
            for key, tol, sev in (("paragraphs", 0.25, "MINOR"),
                                  ("char_total", 0.15, "MAJOR")):
                a, b = actual[key], ref[key]
                if b and abs(a - b) / b > tol:
                    findings.append(finding(
                        "Q12", sev, "Phase 5", key,
                        "{} {} vs 레퍼런스 {} (허용 ±{:.0%})".format(key, a, b, tol),
                        "레퍼런스와 같은 분량·구조로 맞춘다"))
            notes.append("레퍼런스 HWPX 대비 문단 단위 비교 수행 (조판 캐시 제외)")
            # 표 개수는 비교하지 않는다 — 한컴 저작본은 제목상자·강조박스·결재란 같은
            # 레이아웃에도 표를 쓰므로(실측 14 vs 생성 6) 저작 도구가 다르면 비교가 성립하지 않는다.
            notes.append(
                "표 개수 비교 생략 — 생성 {} vs 레퍼런스 {}. 한컴 저작본은 레이아웃에도 "
                "표를 쓰므로 도구가 다르면 비교가 성립하지 않는다".format(
                    actual["tables"], ref["tables"]))

        # ── Q13 쪽수 ────────────────────────────────────────
        pages = args.pages
        if pages is None and not args.no_render:
            pages = kordoc_pages(src)
        if pages is None:
            notes.append("쪽수를 얻지 못했다 — Q13 미판정")
        else:
            actual["pages"] = pages
            if args.target_pages:
                lo_s, _, hi_s = args.target_pages.partition("-")
                lo_p, hi_p = int(lo_s), int(hi_s or lo_s)
                if not (lo_p <= pages <= hi_p):
                    sev = "MAJOR" if tid != "04" else "CRITICAL"
                    findings.append(finding(
                        "Q13", sev, "Phase 5", "쪽수",
                        "{}쪽 — 목표 {}~{}쪽 밖".format(pages, lo_p, hi_p),
                        "본문을 줄이기 전에 먼저 '--no-cover --no-toc' 를 시도한다"))
            if tid == "04" and pages > 1:
                findings.append(finding(
                    "Q13", "CRITICAL", "Phase 5", "쪽수",
                    "④ 개요정리인데 {}쪽 — 1쪽이 실패 조건이다".format(pages),
                    "'짧을수록 확실히·많이 버린다·더 짧게' — 본문을 덜어낸다"))

        # ── Q26 쪽당 밀도 ───────────────────────────────────
        # 프로파일 density 의 쪽 단위 두 값은 여기서만 판정할 수 있다(쪽수를 알아야 한다).
        dens = prof.get("density") or {}
        if pages:
            mpp = actual["marker_total"] / pages
            actual["markers_per_page"] = round(mpp, 1)
            band = dens.get("markers_per_page")
            if band and not (band[0] <= mpp <= band[1]):
                findings.append(finding(
                    "Q26", "MINOR", "Phase 5", "쪽당 마커",
                    "쪽당 {:.1f}개 — 실측 밴드 {}~{}개 밖".format(mpp, band[0], band[1]),
                    "적으면 - 층을 채우고, 많으면 한 쪽에 든 □ 를 나눈다 (references/14 §4)"))
            bmax = dens.get("box_per_page_max")
            nbox = actual["marker_counts"].get("□", 0)
            if bmax:
                bpp = nbox / pages
                actual["box_per_page"] = round(bpp, 1)
                if bpp > bmax:
                    findings.append(finding(
                        "Q26", "MINOR", "Phase 5", "쪽당 □",
                        "쪽당 □ {:.1f}개 — 상한 {}개 초과".format(bpp, bmax),
                        "□ 가 잦으면 각 덩어리가 얕아진다. 묶어서 ○ 로 내린다 (references/14 §4)"))

        # ── Q16 취지 글상자·붙임 ────────────────────────────
        sig = (lin or {}).get("layout_signature") or {}
        if tid in ("01", "02", "10"):
            if not actual["has_attachment"]:
                findings.append(finding(
                    "Q16", "MAJOR", "Phase 5", "붙임",
                    "붙임/별첨 항목이 없다 — 밀도가 맞아도 결재 올릴 문서가 아니다",
                    "【별첨】과 【검증 요약표】를 본문 끝에 붙인다"))
            if not actual["has_end_mark"]:
                findings.append(finding(
                    "Q16", "MINOR", "Phase 5", "끝 표시",
                    "'끝.' 표시가 없다",
                    "본문/붙임 마지막에 2타 띄우고 '끝.' — references/06 §8"))
        if sig.get("attachment_ratio") is not None:
            notes.append("레퍼런스 붙임 보유율 {:.0%}".format(sig["attachment_ratio"]))

        # ── 판정 ────────────────────────────────────────────
        counts: dict[str, int] = {}
        for x in findings:
            counts[x["severity"]] = counts.get(x["severity"], 0) + 1
        if any(x["severity"] in ("CRITICAL", "MAJOR") for x in findings):
            verdict, code = "FAIL", 1
        elif findings:
            verdict, code = "PASS-WITH-WARNINGS", 2
        else:
            verdict, code = "PASS", 0

        metrics = {k: v for k, v in actual.items() if not k.startswith("_")}
        metrics["lineage"] = lin_key
        result = {"verdict": verdict, "type_id": tid, "gate_direction": direction,
                  "counts": counts, "findings": findings,
                  "metrics": metrics, "unverifiable": notes}
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not args.json_only:
            w = sys.stderr
            print("\n[density_guard] {}  유형 {}  계열 {}  →  {}".format(
                src.name, tid, lin_key, verdict), file=w)
            print("  문단 {} · 마커 {} · 표 {} · 쪽 {}".format(
                actual["paragraphs"], actual["marker_counts"], actual["tables"],
                actual.get("pages", "?")), file=w)
            for x in findings:
                print("   [{:8}] {:5} {:10} {}".format(
                    x["severity"], x["id"], x["location"], x["message"][:84]), file=w)
            for n in notes:
                print("  ⓘ " + n, file=w)
        return code
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[density_guard] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
