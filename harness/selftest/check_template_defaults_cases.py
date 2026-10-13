"""H-01: 생성된 프로젝트 TZ를 자식 Python에서 검증한다."""
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from check_project_cases import make_upstream, new_project


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="tz-case-") as tmp:
        base = Path(tmp)
        up = make_upstream(base)
        target = base / "generated"
        r = new_project(up, target, "--package", "sample")
        if r.returncode:
            rows.append(("H01-setup", False, r.stdout + r.stderr))
        else:
            text = (target / "config/gates.toml").read_text(encoding="utf-8")
            tz = tomllib.loads(text)["env"]["TZ"]
            probe = "import time; from datetime import datetime; getattr(time, 'tzset', lambda: None)(); print(datetime.now().astimezone().utcoffset().total_seconds())"
            r = subprocess.run([sys.executable, "-c", probe], env={**os.environ, "TZ": tz}, capture_output=True, text=True)
            seconds = float(r.stdout) if r.returncode == 0 else 0
            rows.append(("H01-01 UTC도 정수 시간 오프셋도 아님", r.returncode == 0 and seconds != 0 and seconds % 3600 != 0 and seconds % 60 == 0, f"TZ={tz!r}, offset_seconds={seconds}, exit={r.returncode}"))
            rows.append(("H01-02 기본값 주석", "비교 기준(UTC)과도 서비스 시간대와도 달라야" in text, text[text.find("[env]")-120:text.find("[env]")+40]))
    for name, good, detail in rows:
        print(f"{'OK ' if good else 'BAD'} {name} — {detail}")
    good = all(r[1] for r in rows)
    print("결과: 모든 사례가 기대와 일치" if good else "결과: 기대와 다른 사례가 있다")
    return 0 if good else 1

if __name__ == "__main__":
    sys.exit(main())
