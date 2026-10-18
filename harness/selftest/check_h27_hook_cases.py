"""WO-H27 integration findings: Git hook ref decoding and object-id width.

Lead-owned formal regression; all mutations are inside TemporaryDirectory.
Run outside the implementer: python harness/selftest/check_h27_hook_cases.py
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
           GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
ENV.pop("PYTHONDONTWRITEBYTECODE", None)


def run(p, *cmd, data=None, encoding="utf-8"):
    return subprocess.run(cmd, cwd=p, input=data, capture_output=True,
                          env=dict(ENV, PYTHONIOENCODING=encoding), timeout=60)


def git(p, *args):
    r = run(p, "git", *args)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.decode("utf-8").strip()


def fixture(base, name, object_format="sha1", protected="refs/heads/main", passing=True):
    p = base / name
    p.mkdir()
    for d in ("harness", "adapters"):
        shutil.copytree(ROOT / d, p / d, ignore=shutil.ignore_patterns("__pycache__", "selftest", "UPSTREAM"))
    (p / "config").mkdir()
    (p / "config/gates.toml").write_text(
        '[harness]\nadapter = "python"\n[enforcement]\n'
        f'protected_refs = [{json.dumps(protected, ensure_ascii=False)}]\nrole = "implementer"\n'
        '[[gate]]\nid = "G2"\nname = "fixture"\n'
        f'cmd = ["python", "-c", "raise SystemExit({0 if passing else 1})"]\n', encoding="utf-8")
    (p / "src.txt").write_text("a\n", encoding="utf-8")
    git(p, "init", "-q", "-b", "main", "--object-format=" + object_format)
    git(p, "add", "-A")
    git(p, "commit", "-qm", "base")
    old = git(p, "rev-parse", "HEAD")
    (p / "src.txt").write_text("b\n", encoding="utf-8")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "target")
    return p, old, git(p, "rev-parse", "HEAD")


def hook(p, local, remote, ref="refs/heads/main", encoding="utf-8", local_ref="refs/heads/main"):
    data = f"{local_ref} {local} {ref} {remote}\n".encode("utf-8")
    return run(p, sys.executable, "harness/prepush.py", "origin", "local-fixture", data=data, encoding=encoding)


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="h27-hook-red-") as td:
        base = Path(td)
        for encoding in ("utf-8", "cp949"):
            p, old, new = fixture(base, "unicode-" + encoding, protected="refs/heads/기능", passing=False)
            r = hook(p, new, old, ref="refs/heads/기능", encoding=encoding)
            rep = p / "reports/gate_report.json"
            good = r.returncode == 1 and rep.exists() and json.loads(rep.read_text(encoding="utf-8"))["passed"] is False
            results.append(("U-" + encoding, good, {"exit": r.returncode, "gate_report": rep.exists()}))
        p, old, new = fixture(base, "sha256", object_format="sha256")
        r = hook(p, new, "0" * len(new))
        rep = p / "reports/gate_report.json"
        results.append(("S-new", r.returncode == 0 and rep.exists(), {"exit": r.returncode, "gate_report": rep.exists()}))
        if rep.exists():
            rep.unlink()
        r = hook(p, "0" * len(new), old, local_ref="(delete)")
        msg = (r.stdout + r.stderr).decode("utf-8", "replace")
        results.append(("S-delete", r.returncode == 1 and "보호 ref 삭제" in msg and not rep.exists(), {"exit": r.returncode, "reason": msg.strip()}))
        for label, ref in (("R-wide-space", "refs/heads/기\u3000능"), ("R-unicode-line", "refs/heads/기\u2028능")):
            p, old, new = fixture(base, label, protected=ref, passing=False)
            git(p, "check-ref-format", ref)
            remote = base / (label + ".git")
            git(p, "init", "-q", "--bare", str(remote))
            git(p, "remote", "add", "origin", str(remote))
            git(p, "push", "-q", "--no-verify", "origin", old + ":" + ref)
            install = run(p, sys.executable, "harness/prepush.py", "--install")
            assert install.returncode == 0, install.stderr
            pushed = run(p, "git", "push", "-q", "origin", "HEAD:" + ref, encoding="cp949")
            remote_head = git(p, "--git-dir", str(remote), "rev-parse", ref)
            rep = p / "reports/gate_report.json"
            good = pushed.returncode != 0 and remote_head == old and rep.exists() and json.loads(rep.read_text(encoding="utf-8"))["passed"] is False
            results.append((label, good, {"exit": pushed.returncode, "remote_updated": remote_head == new, "gate_report": rep.exists()}))
    for cid, good, detail in results:
        print(("OK " if good else "BAD ") + cid + " " + json.dumps(detail, ensure_ascii=False))
    bad = sum(not good for _, good, _ in results)
    print(f"SUMMARY total={len(results)} OK={len(results)-bad} BAD={bad}")
    return int(bool(bad))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
