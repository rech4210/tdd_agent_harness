"""WO-H25 formal Red: W01-W12. Run outside the worker, Python 3.11+ and Git.

No planner implementation or fallback oracle. CLI cases fail explicitly while
workspace.py is absent; fixture/consumer checks run independently of that absence.
All Git mutations take place in TemporaryDirectory, never in the source repo.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0",
           GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
           GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
           GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
RESULTS = []


def run(p, *args, data=None, ok=True):
    r = subprocess.run(args, cwd=p, input=data, capture_output=True, env=ENV)
    if ok and r.returncode:
        raise AssertionError(f"{args}: rc={r.returncode} {r.stderr.decode('utf-8', 'replace')}")
    return r


def git(p, *args):
    return run(p, "git", *args).stdout.decode("utf-8").strip()


def put(p, path, value):
    f = p / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))


def policy(include, exclude=(), required=("tests/red.py", "work_orders/WO.md")):
    return ("schema = 1\n[profiles.case]\n" + "\n".join(
        f"{k} = {json.dumps(list(v), ensure_ascii=False)}"
        for k, v in (("include", include), ("exclude", exclude), ("required", required))) + "\n")


DEFAULT = policy(["src/**", "tests/red.py", "work_orders/WO.md"], ["src/private/**"])


def fixture(base, name, config=DEFAULT, object_format=None):
    p = base / name
    p.mkdir()
    git(p, "init", "-q", *(["--object-format=" + object_format] if object_format else []))
    git(p, "config", "core.autocrlf", "false")
    git(p, "config", "core.quotePath", "false")
    for f in (ROOT / "harness").glob("*.py"):
        put(p, "harness/" + f.name, f.read_bytes())
    for path, content in {
        "src/a.txt": b"a\x00\xff\n", "src/nested/b.txt": "b\n",
        "src/private/secret": "withheld", "src2/near.txt": "near",
        "tests/red.py": "# public current Red", "work_orders/WO.md": "current WO",
        "dev/redteam/hidden.py": "withheld", "reports/judge.json": "withheld",
        "한글 폴더/공백 파일.txt": "정상\n", "config/workspace.toml": config,
        "src/run.sh": "#!/bin/sh\nexit 0\n",
    }.items():
        put(p, path, content)
    git(p, "add", "-A")
    git(p, "update-index", "--chmod=+x", "src/run.sh")
    git(p, "commit", "-qm", "fixture")
    return p


def commit_policy(p, value):
    put(p, "config/workspace.toml", value)
    git(p, "add", "config/workspace.toml")
    git(p, "commit", "-qm", "policy")


def snapshot(p):
    return {f.relative_to(p).as_posix(): (hashlib.sha256(f.read_bytes()).hexdigest(),
                                        f.stat().st_mode & 0o111)
            for f in p.rglob("*") if f.is_file()}


def plan(p, profile="case", base="HEAD", config=None, fail=False):
    before = snapshot(p)
    cmd = [sys.executable, "harness/workspace.py", "plan", "--base", base,
           "--profile", profile]
    if config is not None:
        cmd += ["--config", config]
    r = run(p, *cmd, ok=False)
    assert snapshot(p) == before, "W09: source/index/config changed"
    assert (p / "harness/workspace.py").is_file(), "MISSING_IMPLEMENTATION (not behavioral Red evidence)"
    if fail:
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
        try:
            output = json.loads(r.stdout)
        except (ValueError, UnicodeDecodeError):
            output = None
        assert not isinstance(output, dict) or "files" not in output, "failure emitted a plan"
        return None
    assert r.returncode == 0, (r.returncode, r.stderr)
    result = json.loads(r.stdout)  # rejects extra JSON or logging on stdout
    assert set(result) == {"schema", "base_sha", "profile", "policy_path", "policy_blob", "files"}
    assert type(result["schema"]) is int and result["schema"] == 1
    sha = git(p, "rev-parse", base + "^{commit}")
    path = config or "config/workspace.toml"
    assert result["base_sha"] == sha and result["profile"] == profile
    assert result["policy_path"] == path
    assert result["policy_blob"] == git(p, "rev-parse", sha + ":" + path)
    paths = [entry["path"] for entry in result["files"]]
    assert paths and paths == sorted(set(paths))
    for entry in result["files"]:
        assert set(entry) == {"path", "blob", "mode"}
        tree = git(p, "ls-tree", sha, "--", entry["path"]).split("\t")[0].split()
        assert tree == [entry["mode"], "blob", entry["blob"]]
        assert entry["mode"] in ("100644", "100755")
    return result


def paths(result):
    return [e["path"] for e in result["files"]]


def case(cid, action):
    try:
        action()
        RESULTS.append((cid, True, ""))
    except Exception as e:
        RESULTS.append((cid, False, f"{type(e).__name__}: {e}"))


def expected(p, names):
    """Explicit fixture paths, metadata from Git; never selects by policy."""
    return [{"path": n, "blob": git(p, "rev-parse", "HEAD:" + n),
             "mode": git(p, "ls-tree", "HEAD", "--", n).split()[0]} for n in sorted(names)]


def verify_copy(src, dest, entries):
    actual = {f.relative_to(dest).as_posix() for f in dest.rglob("*")
              if f.is_file() and ".git" not in f.relative_to(dest).parts}
    assert actual == {e["path"] for e in entries}, "file set mismatch"
    for e in entries:
        assert (dest / e["path"]).read_bytes() == run(src, "git", "cat-file", "blob", e["blob"]).stdout
        if os.name != "nt":
            assert bool((dest / e["path"]).stat().st_mode & 0o111) == (e["mode"] == "100755")
    # Git index mode is also verified on Windows, where POSIX mode is unavailable.
    for e in entries:
        assert git(dest, "ls-files", "--stage", "--", e["path"]).split()[0] == e["mode"]
    assert not (dest / ".git/objects/info/alternates").exists()
    assert not (dest / ".git/commondir").exists()
    assert not git(dest, "remote")
    assert (dest / ".git").is_dir() and not (dest / ".git").is_symlink()


def consume(src, dest, entries):
    """Lead-side fixture for existing extraction procedure, not production tooling."""
    dest.mkdir()
    for e in entries:
        put(dest, e["path"], run(src, "git", "cat-file", "blob", e["blob"]).stdout)
        (dest / e["path"]).chmod(0o755 if e["mode"] == "100755" else 0o644)
    git(dest, "init", "-q")
    git(dest, "config", "core.autocrlf", "false")
    git(dest, "add", "-A")
    for e in entries:
        git(dest, "update-index", "--chmod=" + ("+x" if e["mode"] == "100755" else "-x"), e["path"])
    git(dest, "commit", "-qm", "isolated")
    verify_copy(src, dest, entries)


def main():
    with tempfile.TemporaryDirectory(prefix="workspace-cases-") as tmp:
        b = Path(tmp)
        p = fixture(b, "regular")

        def w01():
            commit_policy(p, policy(["src/**", "src/a.txt", "src/**", "tests/red.py", "work_orders/WO.md"],
                                    ["src/private/**", "src/nested/**"]))
            assert paths(plan(p)) == ["src/a.txt", "src/run.sh", "tests/red.py", "work_orders/WO.md"]
        case("W01", w01)

        def w02():
            commit_policy(p, policy(["src/**", "tests/**", "work_orders/**", "dev/**", "reports/**"],
                                    ["src/private/**", "dev/**", "reports/**"]))
            assert paths(plan(p)) == ["src/a.txt", "src/nested/b.txt", "src/run.sh", "tests/red.py", "work_orders/WO.md"]
        case("W02", w02)

        def w03():
            commit_policy(p, DEFAULT)
            sha = git(p, "rev-parse", "HEAD")
            old = plan(p, base=sha)
            put(p, "src/a.txt", "dirty")
            put(p, "src/untracked", "untracked")
            put(p, "config/workspace.toml", "broken = [")
            assert plan(p, base=sha) == old
            # A newer committed policy must not override the requested old commit.
            git(p, "add", "-A")
            git(p, "commit", "-qm", "new dirty policy committed")
            assert plan(p, base=sha) == old
        case("W03", w03)

        case("W04.ref", lambda: plan(p, base="missing-ref", fail=True))
        case("W04.profile", lambda: plan(p, profile="missing-profile", fail=True))
        case("W04.missing-policy", lambda: plan(p, config="absent.toml", fail=True))
        case("W04.non-commit", lambda: plan(p, base=git(p, "rev-parse", "HEAD:src/a.txt"), fail=True))
        def untracked_policy():
            q = fixture(b, "untracked-policy")
            put(q, "config/local.toml", DEFAULT)
            plan(q, config="config/local.toml", fail=True)
        case("W03.untracked-policy", untracked_policy)
        bad_configs = ["broken = [", DEFAULT.replace("schema = 1", "schema = true"),
                       DEFAULT.replace("schema = 1", "schema = 2"), DEFAULT.replace("schema = 1", 'schema = "1"'),
                       DEFAULT + "unknown = []\n", DEFAULT.replace("schema = 1", "schema = 1\nunknown = 1"),
                       DEFAULT.replace("include = [", "include = [4, "),
                       DEFAULT.replace('exclude = ["src/private/**"]', 'exclude = "src/**"'),
                       DEFAULT.replace('required = ["tests/red.py", "work_orders/WO.md"]', ""),
                       policy([]), "schema = 1\nprofiles = []\n",
                       "schema = 1\n", DEFAULT.replace("exclude = [\"src/private/**\"]", ""),
                       DEFAULT.replace("include = [\"src/**\", \"tests/red.py\", \"work_orders/WO.md\"]", "")]
        for i, value in enumerate(bad_configs):
            def invalid(value=value):
                q = fixture(b, f"bad-{i}", value)
                plan(q, fail=True)
            case(f"W04.schema-{i:02}", invalid)

        invalid_paths = ["/absolute", "C:/absolute", "src\\a.txt", "", ".", "..", "src/../a.txt",
                         "src/./a.txt", "src//a.txt", "src/", ".git/config", "src/.git/x",
                         "src/*.txt", "src/?", "src/[ab]", "**", "src/**/a.txt"]
        for i, value in enumerate(invalid_paths):
            for field in ("include", "exclude", "required"):
                def bad_path(value=value, field=field, i=i):
                    fields = dict(include=["src/**", "tests/red.py", "work_orders/WO.md"], exclude=[], required=[])
                    fields[field] = [value]
                    q = fixture(b, f"path-{i}-{field}", policy(**fields))
                    plan(q, fail=True)
                case(f"W05.{field}-{i:02}", bad_path)
        for i, value in enumerate(["/absolute", "C:/file", "../file", "config\\workspace.toml", "./config/workspace.toml"]):
            case(f"W05.config-{i}", lambda value=value: plan(p, config=value, fail=True))

        def unicode_case():
            q = fixture(b, "unicode", policy(["한글 폴더/**"], required=["한글 폴더/공백 파일.txt"]))
            assert paths(plan(q)) == ["한글 폴더/공백 파일.txt"]
        case("W05.unicode", unicode_case)
        def exact_case():
            q = fixture(b, "exact-case", policy(["src/A.txt"], required=["src/A.txt"]))
            plan(q, fail=True)  # Git tree has a.txt even on case-insensitive Windows.
        case("W05.exact-case", exact_case)
        for i, value in enumerate([
            policy(["src/**"]), policy(["tests/**", "work_orders/**"], ["tests/red.py"]),
            policy(["missing/**"], required=[]), policy(["src/**"], required=["src/**"]),
        ]):
            case(f"W06.{i}", lambda value=value, i=i: plan(fixture(b, f"required-{i}", value), fail=True))

        def w07():
            q = fixture(b, "metadata")
            first = plan(q)
            assert first == plan(q)
            # Compare raw JSON serialization for deterministic output as well.
            argv = (sys.executable, "harness/workspace.py", "plan", "--base", "HEAD", "--profile", "case")
            assert run(q, *argv).stdout == run(q, *argv).stdout
            assert next(e for e in first["files"] if e["path"] == "src/run.sh")["mode"] == "100755"
        case("W07", w07)
        case("W07.sha256", lambda: plan(fixture(b, "sha256", object_format="sha256")))

        for kind, mode in (("link", "120000"), ("module", "160000")):
            def special(kind=kind, mode=mode):
                q = fixture(b, kind)
                oid = git(q, "rev-parse", "HEAD" if kind == "module" else "HEAD:src/a.txt")
                git(q, "update-index", "--add", "--cacheinfo", mode, oid, "src/special")
                git(q, "commit", "-qm", "special tree entry")
                assert git(q, "ls-tree", "HEAD", "--", "src/special").split()[0] == mode
                plan(q, fail=True)
                commit_policy(q, DEFAULT.replace('["src/private/**"]', '["src/private/**", "src/special"]'))
                assert "src/special" not in paths(plan(q))
            case(f"W08.{kind}", special)

        def w09():
            q = fixture(b, "no-write")
            put(q, "dirty", "preserve")
            put(q, "src/a.txt", "staged content")
            git(q, "add", "src/a.txt")
            put(q, "src/a.txt", "unstaged content")
            plan(q)
            plan(q, base="missing-ref", fail=True)
        case("W09", w09)

        def w10(use_plan):
            q = fixture(b, "consumer-plan" if use_plan else "consumer-control")
            entries = plan(q)["files"] if use_plan else expected(q, ["src/a.txt", "src/run.sh", "tests/red.py", "work_orders/WO.md"])
            # A withheld object exists in source history, absent from independent repo.
            secret = git(q, "rev-parse", "HEAD:src/private/secret")
            dest = b / (q.name + "-out")
            consume(q, dest, entries)
            assert run(dest, "git", "cat-file", "-e", secret, ok=False).returncode != 0
            assert (q / ".git/objects").resolve() != (dest / ".git/objects").resolve()
        case("W10.fixture-control", lambda: w10(False))
        case("W10.plan-integration", lambda: w10(True))

        def w11():
            q = fixture(b, "attributes")
            put(q, ".gitattributes", "src/a.txt export-ignore\nsrc/subst.txt export-subst\n")
            put(q, "src/subst.txt", "$Format:%H$\n")
            git(q, "add", "-A")
            git(q, "commit", "-qm", "archive attributes")
            entries = expected(q, ["src/a.txt", "src/subst.txt"])
            archive = run(q, "git", "archive", "HEAD", "--", "src/a.txt", "src/subst.txt").stdout
            dest = b / "archive-out"
            dest.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                # Fixture-owned archive paths only; no arbitrary archive extraction.
                for member in tar.getmembers():
                    if member.isfile():
                        assert member.name == "src/subst.txt"
                        put(dest, member.name, tar.extractfile(member).read())
            assert not (dest / "src/a.txt").exists()
            assert (dest / "src/subst.txt").read_bytes() != run(q, "git", "cat-file", "blob", entries[1]["blob"]).stdout
            try:
                verify_copy(q, dest, entries)
            except AssertionError:
                pass  # mismatch is the required stop signal, no deployment follows
            else:
                raise AssertionError("archive mismatch accepted")
            # Restore only the missing file: equal sets still fail byte comparison.
            put(dest, "src/a.txt", run(q, "git", "cat-file", "blob", entries[0]["blob"]).stdout)
            try:
                verify_copy(q, dest, entries)
            except AssertionError:
                pass
            else:
                raise AssertionError("export-subst byte change accepted")
            consume(q, b / "raw-out", entries)  # raw blobs pass the same comparison
        case("W11.fixture-control", w11)

        def w11_plan():
            q = fixture(b, "attributes-plan")
            put(q, ".gitattributes", "src/a.txt export-ignore\n")
            git(q, "add", "-A")
            git(q, "commit", "-qm", "attributes")
            result = plan(q)
            assert "src/a.txt" in paths(result), "export-ignore must not filter Git tree candidates"
            consume(q, b / "attributes-plan-out", result["files"])
        case("W11.plan-integration", w11_plan)

        def w12():
            text = DEFAULT.replace("profiles.case", "profiles.consumer")
            text += '\n[profiles.harness_dev]\ninclude = ["harness/**", "tests/red.py", "work_orders/WO.md"]\nexclude = []\nrequired = ["tests/red.py", "work_orders/WO.md"]\n'
            q = fixture(b, "profiles", text)
            assert not any(n.startswith("harness/") for n in paths(plan(q, profile="consumer")))
            assert "harness/paths.py" in paths(plan(q, profile="harness_dev"))
        case("W12.profiles", w12)

    for cid, good, detail in RESULTS:
        print(f"{'OK' if good else 'BAD'} {cid}" + (f": {detail}" if detail else ""))
    total = len(RESULTS)
    passed = sum(good for _, good, _ in RESULTS)
    print(f"SUMMARY total={total} OK={passed} BAD={total-passed}")
    return int(passed != total)


if __name__ == "__main__":
    sys.exit(main())
