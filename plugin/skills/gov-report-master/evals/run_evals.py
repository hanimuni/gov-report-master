# -*- coding: utf-8 -*-
"""evals/evals.json 의 12 케이스를 그대로 돌려 exit·assert 를 대조한다."""
import io, json, os, re, subprocess, sys

ROOT = sys.argv[1]            # ...\gov-report-master-final\skills\gov-report-master
EV = json.load(io.open(os.path.join(ROOT, "evals", "evals.json"), encoding="utf-8"))
only = sys.argv[2] if len(sys.argv) > 2 else None

env = dict(os.environ)
env.update(EV["env"].get("vars", {}))

SEL = re.compile(r"\[\?([A-Za-z_.]+)=='([^']*)'\]")


def dig(obj, path):
    """a.b[?k=='v'].c 형태 경로를 따라간다. 없으면 KeyError."""
    cur = obj
    for tok in re.split(r"\.(?![^\[]*\])", path):
        m = SEL.search(tok)
        if m:
            key = tok[: m.start()]
            if key:
                cur = cur[key]
            k, v = m.group(1), m.group(2)
            hit = [x for x in cur if str(dig(x, k)) == v] if isinstance(cur, list) else []
            if not hit:
                raise KeyError(path)
            cur = hit[0]
            continue
        idx = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)?\[(\d+)\]$", tok)
        if idx:
            if idx.group(1):
                cur = cur[idx.group(1)]
            cur = cur[int(idx.group(2))]
            continue
        if isinstance(cur, list):
            cur = cur[int(tok)]
        else:
            cur = cur[tok]
    return cur


def check(op, got, want):
    if op == "==":
        return got == want
    if op == "!=":
        return got != want
    if op == ">=":
        return got >= want
    if op == "<=":
        return got <= want
    if op == ">":
        return got > want
    if op == "<":
        return got < want
    if op == "contains":
        return want in got
    if op == "not_contains":
        return want not in got
    if op == "absent_or":
        return got == want
    if op in ("length>=", "len>="):
        return len(got) >= want
    raise ValueError(op)


npass = nfail = 0
for case in EV["cases"]:
    cid = case["id"]
    if only and only not in cid:
        continue
    p = subprocess.run(case["cmd"], shell=True, cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    probs = []
    if "exit" in case and p.returncode != case["exit"]:
        probs.append(f"exit {p.returncode} != {case['exit']}")
    data = None
    try:
        s = p.stdout.strip()
        data = json.loads(s[s.index("{"):s.rindex("}") + 1]) if "{" in s else None
    except Exception as e:
        data = None
    for a in case.get("assert", []):
        path, op, want = a["path"], a["op"], a.get("value")
        if path == "stdout":
            got = p.stdout.strip()
            if not check(op, got, want):
                probs.append(f"stdout: {got!r} {op} {want!r} 아님")
            continue
        if data is None:
            probs.append(f"{path}: stdout 이 JSON 이 아님")
            continue
        try:
            got = dig(data, path)
        except (KeyError, IndexError, TypeError):
            if op == "absent_or":
                continue
            probs.append(f"{path}: 경로 없음")
            continue
        if not check(op, got, want):
            probs.append(f"{path}: {got!r} {op} {want!r} 아님")
    if probs:
        nfail += 1
        print(f"  FAIL {cid}")
        for x in probs:
            print(f"        {x}")
        if p.stderr.strip():
            print("        stderr:", p.stderr.strip().splitlines()[-1][:160])
    else:
        npass += 1
        print(f"  PASS {cid}")

print(f"\n  {npass} 통과 / {nfail} 실패")
sys.exit(1 if nfail else 0)
