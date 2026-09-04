#!/usr/bin/env python3
"""
build_report.py — Phase 7 오케스트레이터 (원고 → HWPX → 검증 → 리포트)

체인
----
  1) draft_guard.py    원고 게이트. CRITICAL·MAJOR 가 있으면 **빌드하지 않는다**
                       (--force 로만 강행. 깨진 원고를 조판해봐야 반려된다)
  2) to_kordoc_md.py   우리 마커 표기 → kordoc 입력 계약 (이중 마커·내어쓰기 파손 방지)
  3) kordoc generate   프로파일의 preset·options 로 HWPX 생성
  4) kordoc validate   Q14 구조 무결성
  5) density_guard.py  Q10~Q13 · Q16 밀도·쪽수·붙임
  6) 텍스트 리포트     references/06 §9 — **SVG 프리뷰는 만들지 않는다**

쪽수 초과 대응 (references/06 §9 · 09 §7)
  본문을 줄이기 **전에** `--no-cover --no-toc` 로 부속 페이지를 먼저 걷어내고 1회 재빌드한다.
  그래도 넘치면 본문 압축을 사람에게 요구한다 — 자간·장평 축소 같은 가짜 줄이기는 하지 않는다.

사용법
------
  python build_report.py .gov-report/05-final.md --type 01 -o out/report.hwpx \
      [--org 행정안전부] [--approval 담당,팀장,과장] \
      [--form-profile .gov-report/01-form-profile.json]   # kordoc --profile (표 서식)
      [--reference 레퍼런스.hwpx] [--target-pages 5-10] [--lineage central_2015plus]

출력 계약: exit 0=PASS · 1=FAIL · 2=PASS-WITH-WARNINGS · 3=실행 오류
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Windows 의 npx 는 npx.cmd 다 — shell=False 로는 이름만으로 찾지 못한다
NPX = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
KORDOC = [NPX, "-y", "kordoc@^4"]

# 값을 받는 kordoc 플래그 — 프로파일에 이름만 적혀 있고 값은 실행 시 들어온다
VALUE_FLAGS = {"--org": "org", "--approval": "approval"}
FAIL_SEV = {"CRITICAL", "MAJOR"}


def load_json(p: Path) -> dict:
    with io.open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", shell=False)


def run_json(cmd: list[str]) -> tuple[dict, int, str]:
    p = run(cmd)
    try:
        return json.loads(p.stdout), p.returncode, p.stderr
    except Exception:  # noqa: BLE001
        return ({"verdict": "ERROR", "findings": [],
                 "message": (p.stderr or p.stdout)[-400:]}, 3, p.stderr)


def find_profile(type_id: str) -> Path:
    hits = sorted((ROOT / "profiles").glob("{}-*.profile.json".format(type_id)))
    if not hits:
        raise RuntimeError("유형 {} 프로파일을 찾지 못했다".format(type_id))
    return hits[0]


def kordoc_options(prof: dict, args: argparse.Namespace, drop_cover: bool) -> list[str]:
    """프로파일 options 를 실제 CLI 인자로 편다. 값 없는 플래그는 조용히 뺀다."""
    kd = prof.get("kordoc", {})
    out: list[str] = []
    for opt in kd.get("options", []):
        key = VALUE_FLAGS.get(opt)
        if key:
            val = getattr(args, key, None)
            if val:
                out += [opt, val]
            continue
        out.append(opt)
    if drop_cover:
        for f in ("--no-cover", "--no-toc"):
            if f not in out:
                out.append(f)
    if args.form_profile:
        out += ["--profile", str(Path(args.form_profile).resolve())]
    return out


def parse_range(spec: str | None) -> tuple[int, int] | None:
    if not spec:
        return None
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", spec)
    if m:
        return int(m.group(1)), int(m.group(2))
    if spec.strip().isdigit():
        n = int(spec.strip())
        return n, n
    return None


def build_once(md: Path, out: Path, prof: dict, args: argparse.Namespace,
               drop_cover: bool) -> tuple[bool, str, list[str]]:
    preset = prof.get("kordoc", {}).get("preset", "보고서")
    cmd = KORDOC + ["generate", str(md), "-o", str(out), "--preset", preset]
    cmd += kordoc_options(prof, args, drop_cover)
    p = run(cmd)
    ok = p.returncode == 0 and out.exists()
    return ok, (p.stderr or p.stdout)[-600:], cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="원고 → HWPX 파이프라인")
    ap.add_argument("draft")
    ap.add_argument("--type", dest="type_id", required=True)
    ap.add_argument("-o", "--output")
    ap.add_argument("--profile", help="유형 프로파일 (기본: --type 으로 자동 선택)")
    ap.add_argument("--form-profile", help="kordoc profile 로 뽑은 서식 JSON (표 서식 주입)")
    ap.add_argument("--reference", help="레퍼런스 HWPX — density_guard 문단 비교용")
    ap.add_argument("--lineage")
    ap.add_argument("--org", help="표지 기관명")
    ap.add_argument("--approval", help="결재란 직위 라벨 (쉼표 구분)")
    ap.add_argument("--target-pages", help="'5-10' 형식. 없으면 프로파일·유형 기준")
    ap.add_argument("--workdir", help="중간 산출 디렉터리 (기본 <원고>/.gov-report)")
    ap.add_argument("--force", action="store_true", help="원고 게이트 FAIL 이어도 빌드")
    ap.add_argument("--skip-draft-guard", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    steps: list[dict] = []
    findings: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})

    try:
        draft = Path(args.draft).resolve()
        if not draft.exists():
            raise RuntimeError("원고를 찾지 못했다: {}".format(draft))
        prof_path = Path(args.profile) if args.profile else find_profile(args.type_id)
        prof = load_json(prof_path)
        work = Path(args.workdir).resolve() if args.workdir else draft.parent / ".gov-report"
        work.mkdir(parents=True, exist_ok=True)
        out = Path(args.output).resolve() if args.output else work / (draft.stem + ".hwpx")
        out.parent.mkdir(parents=True, exist_ok=True)

        # ── 1) 원고 게이트 ───────────────────────────────────────────────
        if not args.skip_draft_guard:
            dg, _, _ = run_json([sys.executable, str(HERE / "draft_guard.py"), str(draft),
                                 "--profile", str(prof_path), "--type", args.type_id,
                                 "--final", "--json-only"])
            blocking = [f for f in dg.get("findings", [])
                        if f.get("severity") in FAIL_SEV]
            findings += dg.get("findings", [])
            step("draft_guard", not blocking,
                 "{} · 차단 {}건".format(dg.get("verdict", "?"), len(blocking)))
            if blocking and not args.force:
                result = {"verdict": "FAIL", "stage": "draft_guard",
                          "message": "원고 게이트에서 차단됐다 — 조판 전에 본문을 고친다",
                          "steps": steps, "findings": findings,
                          "retry_plan": [{"phase": "Phase 5",
                                          "ids": list(dict.fromkeys(f["id"] for f in blocking))[:8]}]}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if not args.json_only:
                    print("\n[build_report] 중단 — 원고 게이트 차단 {}건".format(
                        len(blocking)), file=sys.stderr)
                    for f in blocking[:8]:
                        print("   [{}] {} {}".format(f.get("severity"), f.get("id"),
                                                     str(f.get("message"))[:78]),
                              file=sys.stderr)
                return 1
        else:
            step("draft_guard", True, "건너뜀")

        # ── 2) kordoc 입력 계약으로 변환 ─────────────────────────────────
        kmd = work / (draft.stem + ".kordoc.md")
        conv, rc, err = run_json([sys.executable, str(HERE / "to_kordoc_md.py"),
                                  str(draft), "-o", str(kmd), "--json-only"])
        if rc == 3 or not kmd.exists():
            raise RuntimeError("마크다운 변환 실패: " + str(conv.get("message", err))[:200])
        step("to_kordoc_md", True, "{} · {}".format(conv.get("verdict"),
                                                    conv.get("counts", {})))

        # ── 3) 생성 ─────────────────────────────────────────────────────
        ok, log, cmd = build_once(kmd, out, prof, args, drop_cover=False)
        step("kordoc generate", ok, " ".join(cmd[3:]))
        if not ok:
            raise RuntimeError("HWPX 생성 실패: " + log[-300:])

        # ── 4) 구조 검증 (Q14) ──────────────────────────────────────────
        v = run(KORDOC + ["validate", str(out)])
        vok = v.returncode == 0
        step("kordoc validate", vok, (v.stdout or v.stderr).strip()[-160:])
        if not vok:
            findings.append({"id": "Q14", "severity": "CRITICAL",
                             "phase_to_retry": "Phase 7", "location": out.name,
                             "message": "구조 검증 실패",
                             "fix_hint": (v.stdout or v.stderr)[-200:]})

        # ── 5) 밀도·쪽수 (Q10~Q13 · Q16) ────────────────────────────────
        def density(target: str | None) -> dict:
            cmd2 = [sys.executable, str(HERE / "density_guard.py"), str(out),
                    "--profile", str(prof_path), "--json-only"]
            if args.reference:
                cmd2 += ["--reference", str(Path(args.reference).resolve())]
            if args.lineage:
                cmd2 += ["--lineage", args.lineage]
            if target:
                cmd2 += ["--target-pages", target]
            d, _, _ = run_json(cmd2)
            return d

        den = density(args.target_pages)
        pages = (den.get("metrics") or {}).get("pages")
        rng = parse_range(args.target_pages)

        # 쪽수 초과 → 본문을 줄이기 전에 부속 페이지부터 걷어낸다 (1회)
        rebuilt = False
        over = bool(rng and isinstance(pages, int) and pages > rng[1])
        if over and prof.get("kordoc", {}).get("preset") == "개조식":
            ok2, log2, cmd2 = build_once(kmd, out, prof, args, drop_cover=True)
            if ok2:
                rebuilt = True
                den = density(args.target_pages)
                pages = (den.get("metrics") or {}).get("pages")
                step("재빌드(--no-cover --no-toc)", True,
                     "쪽수 초과 → 부속 페이지 제거 · {}쪽".format(pages))
        findings += den.get("findings", [])
        step("density_guard", den.get("verdict") != "FAIL",
             "{} · {}쪽".format(den.get("verdict"), pages))

        # ── 판정 ────────────────────────────────────────────────────────
        counts: dict[str, int] = {}
        for f in findings:
            s = f.get("severity", "WARN")
            counts[s] = counts.get(s, 0) + 1
        if any(f.get("severity") in FAIL_SEV for f in findings) or not vok:
            verdict, code = "FAIL", 1
        elif findings:
            verdict, code = "PASS-WITH-WARNINGS", 2
        else:
            verdict, code = "PASS", 0

        # ── 6) 텍스트 리포트 (references/06 §9) ─────────────────────────
        m = den.get("metrics") or {}
        tname = prof.get("type_name", "유형 " + args.type_id)
        tgt = "{}~{}쪽".format(*rng) if rng else "프로파일 기준"
        lines = [
            "{} 산출 {}".format("✅" if code == 0 else "⚠", verdict),
            "   {}   ({}쪽 · 표 {} · 구조검증 {})".format(
                out.name, pages if pages is not None else "?", m.get("tables", "?"),
                "통과" if vok else "실패"),
            "",
            "   한글에서 열어 확인하실 것 6가지",
            "   1. 쪽 넘김 — □ 소제목이 쪽 맨 아래 혼자 남았는지",
            "   2. 표 걸침 — 표 헤더와 첫 행이 갈렸는지",
            "   3. 분량 — 목표 {} (현재 {}쪽){}".format(
                tgt, pages if pages is not None else "?",
                " · 부속 페이지 제거 후" if rebuilt else ""),
            "   4. 제목상자 색 — {} 규정 색상".format(tname),
            "   5. 붙임 순서 ↔ 본문 언급   [{}]".format(
                "붙임 있음" if m.get("has_attachment") else "⚠ 붙임 없음"),
            "   6. 결재선 — {}".format(args.approval or "(미지정)"),
            "",
            "   ※ 4·5·6은 조판을 봐도 알 수 없다 — report-auditor 결과를 함께 볼 것.",
        ]
        report_text = "\n".join(lines)

        result = {"verdict": verdict, "type_id": args.type_id, "output": str(out),
                  "pages": pages, "counts": counts, "steps": steps,
                  "findings": findings, "metrics": m,
                  "rebuilt_without_cover": rebuilt, "report_text": report_text}
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not args.json_only:
            w = sys.stderr
            print("", file=w)
            for s in steps:
                print("   {} {:26} {}".format("✓" if s["ok"] else "✗",
                                              s["step"], s["detail"][:70]), file=w)
            print("\n" + report_text, file=w)
            for f in findings[:12]:
                if f.get("severity") in FAIL_SEV:
                    print("   [{}] {} → {} {}".format(
                        f.get("severity"), f.get("id"), f.get("phase_to_retry"),
                        str(f.get("message"))[:64]), file=w)
        return code

    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e), "steps": steps},
                         ensure_ascii=False))
        print("[build_report] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
