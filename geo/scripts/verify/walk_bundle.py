"""실측 세션을 **개발자가 지정한 폴더**로 모은다 — 기기 원본 + 서버 파생 + 재전송.

보는 것과 디버깅은 다르다. 보고서는 요약이지만, "거리가 왜 이렇게 나왔지"를 파려면 그 세션의
원본 fix 가 파일로 손에 있어야 한다. 서버는 finish 에서 원좌표를 지우므로 원본은 기기 Room
에만 있고, debug 빌드가 종료 시 내부 저장소에 남긴 export(`WalkSessionExporter`)가 그 통로다.

    uv run python -m scripts.verify.walk_bundle pull  --out C:/dev/walks   # 기기 → 폴더 (adb)
    uv run python -m scripts.verify.walk_bundle fetch --out C:/dev/walks   # 서버 파생 → 폴더
    uv run python -m scripts.verify.walk_bundle push  --out C:/dev/walks   # 서버에 없는 세션 재전송

폴더 구조 — 세션 하나가 파일 두 개다:

    <out>/device/walk-<started>-<id8>.json    원본 (기기 export 그대로)
    <out>/server/<session_id>.json            파생 (GET /walk/sessions/{id} 응답 그대로)

`push` 가 업로드 실패의 복구 경로다. 판정 기준은 **"서버에 세션이 있나"가 아니라 "finish 까지
가서 파생 사실이 확정됐나"** 다 — 실제로 가장 흔한 실패가 부분 업로드이기 때문이다:

    POST /sessions   OK   →  POST /fixes(1)  OK   →  POST /fixes(2)  끊김   →  finish 못 함

이때 세션 행은 서버에 있고 상태는 `open` 이며 `WalkFacts` 는 없다. 존재만 보고 건너뛰면
이 도구가 복구해야 할 바로 그 세션을 놓친다. 모든 엔드포인트가 멱등이라(같은 `client_seq`
는 duplicate, 같은 start 는 그대로) 처음부터 다시 보내는 것이 안전하다.

**범위**: `push` 는 **명시적으로 종료됐지만 서버 업로드에 실패한** 세션의 개발자 복구
경로다. process-death 로 닫히지 않은 세션(`endedAtMillis IS NULL`, 결정 #55)은 포함하지
않는다 — 기기 export 가 그런 세션을 내보내지 않고, 그 산책의 `ended_at` 을 누가 정할지가
아직 정책 문제다.

**수명**: 기기 export 도 `--out` 폴더도 **제품 보관 정책(결정 #57) 밖의 개발 artifact** 다.
둘 다 원좌표를 담으므로 실측이 끝나면 지운다 — 기기 쪽은 `pull --delete` 또는 `clear`,
PC 쪽은 폴더째 지운다. 레포에 커밋하지 않는다(`--out` 은 레포 밖을 줄 것).

기기 export 는 Room 밖의 **두 번째 원궤적 사본**이라, Room 세션을 지워도 같이 사라지지
않는다. 그래서 수명을 여기 적어 둔다.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE = "com.daengs.geo"
EXPORT_DIR = "files/walk-exports"
BATCH = 2000                      # FixBatchIn.fixes 상한 — 업로더와 같은 값


# ------------------------------------------------------------------ 순수 부분
def session_id_of(export: dict) -> str:
    return export["session"]["id"]


def batches(fixes: list[dict], size: int = BATCH) -> list[list[dict]]:
    return [fixes[i:i + size] for i in range(0, len(fixes), size)]


def needs_push(status: int, derived: dict | None) -> bool:
    """이 세션을 (다시) 보내야 하나.

    **존재가 아니라 완료를 본다.** 부분 업로드로 끊긴 세션은 `GET` 이 200 을 주지만 `facts`
    가 없다 — 그게 이 도구가 복구해야 하는 상태다. 200 도 404 도 아니면 판단을 포기한다:
    서버가 아픈 것을 "미업로드" 로 읽고 재전송하면 상황을 더 나쁘게 만든다.
    """
    if status == 404:
        return True
    if status == 200:
        return not (derived or {}).get("facts")
    raise PushUnavailable(status)


class PushUnavailable(RuntimeError):
    """서버가 200/404 가 아닌 답을 줬다. 미업로드와 구분되지 않으므로 건너뛴다."""

    def __init__(self, status: int):
        super().__init__(f"서버 응답 {status}")
        self.status = status


def facts_summary(derived: dict) -> str:
    """서버 파생 응답 한 줄 요약. 표가 아니라 줄인 이유: 세션 수가 적고 폭이 제각각이다."""
    facts = derived.get("facts")
    if not facts:
        return "사실 없음 (finish 전이거나 계산 실패)"
    parts = [
        f"{facts['duration_s']}s", f"{facts['moving_distance_m']}m(moving)",
        f"정지 {facts['stop_count']}회", f"fix {facts['fix_count']}",
        facts.get("evidence_origin", "?"),
    ]
    encounters = derived.get("encounters") or []
    if encounters:
        parts.append(f"조우 {len(encounters)}건")
    return " · ".join(str(p) for p in parts)


# ------------------------------------------------------------------ adb / http
def adb(args: list[str]) -> bytes:
    result = subprocess.run(["adb", *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} 실패: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def http(method: str, url: str, payload: dict | None = None) -> tuple[int, dict | None]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, None


# ------------------------------------------------------------------ 하위 명령
def device_exports() -> list[str]:
    """기기의 export 파일 이름들. **폴더가 아직 없는 것은 오류가 아니다** — 첫 실행이다."""
    result = subprocess.run(["adb", "shell", "run-as", PACKAGE, "ls", EXPORT_DIR],
                            capture_output=True, check=False)
    if result.returncode != 0:
        return []
    return sorted(n for n in result.stdout.decode(errors="replace").split()
                  if n.endswith(".json"))


def pull(out: Path, *, delete: bool = False) -> list[Path]:
    """기기 export → <out>/device. run-as 로 읽는다 — 내부 저장소는 debug 빌드만 열린다.

    `delete` 는 **PC 에 쓰기가 끝난 뒤에만** 기기 사본을 지운다. 기기 export 는 Room 밖의
    두 번째 원궤적 사본이라 오래 두면 지운 산책이 거기 남는다 (독스트링의 수명 절).
    """
    device = out / "device"
    device.mkdir(parents=True, exist_ok=True)
    pulled = []
    for name in device_exports():
        content = adb(["exec-out", "run-as", PACKAGE, "cat", f"{EXPORT_DIR}/{name}"])
        target = device / name
        target.write_bytes(content)
        pulled.append(target)
        if delete:
            adb(["shell", "run-as", PACKAGE, "rm", f"{EXPORT_DIR}/{name}"])
        print(f"  {name}{'  (기기에서 삭제)' if delete else ''}")
    if not pulled:
        print("  기기에 export 가 없다 - debug 빌드로 산책을 종료했는지 확인")
    return pulled


def clear() -> None:
    """기기의 export 를 전부 지운다. 개발 artifact 수명 관리 — PC 폴더는 손대지 않는다."""
    names = device_exports()
    for name in names:
        adb(["shell", "run-as", PACKAGE, "rm", f"{EXPORT_DIR}/{name}"])
        print(f"  {name} 삭제")
    if not names:
        print("  지울 export 가 없다")


def fetch(out: Path, base: str) -> None:
    """폴더의 각 세션에 대해 서버 파생을 받아 <out>/server 에 둔다. 404 = 미업로드."""
    server = out / "server"
    server.mkdir(parents=True, exist_ok=True)
    for path in sorted((out / "device").glob("*.json")):
        export = json.loads(path.read_text(encoding="utf-8"))
        sid = session_id_of(export)
        status, derived = http("GET", f"{base}/walk/sessions/{sid}")
        if status == 404 or derived is None:
            print(f"  {sid}  서버에 없음 (push 로 재전송 가능)")
            continue
        (server / f"{sid}.json").write_text(
            json.dumps(derived, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {sid}  {facts_summary(derived)}")


def push(out: Path, base: str) -> None:
    """서버가 모르는 세션만 원래 id 로 올린다. 업로드 실패(밖에서 Wi-Fi 끊김 등)의 복구 경로."""
    for path in sorted((out / "device").glob("*.json")):
        export = json.loads(path.read_text(encoding="utf-8"))
        sid = session_id_of(export)
        session = export["session"]
        if session.get("dog_id") is None:
            print(f"  {sid}  dog_id 없음 - 귀속을 모르는 세션은 올리지 않는다 (결정 #58)")
            continue
        status, derived = http("GET", f"{base}/walk/sessions/{sid}")
        try:
            if not needs_push(status, derived):
                print(f"  {sid}  이미 완료 / {facts_summary(derived)}")
                continue
        except PushUnavailable as unavailable:
            print(f"  {sid}  {unavailable} - 미업로드와 구분이 안 되므로 건너뛴다")
            continue
        if status == 200:
            print(f"  {sid}  서버에 있으나 finish 전 - 처음부터 재전송한다")

        status, _ = http("POST", f"{base}/walk/sessions", {
            "id": sid, "dog_id": session["dog_id"], "started_at": session["started_at"]})
        if status == 409:
            print(f"  {sid}  서버에 다른 시작 데이터로 존재 - 손대지 않는다")
            continue
        for batch in batches(export["fixes"]):
            status, _ = http("POST", f"{base}/walk/sessions/{sid}/fixes", {"fixes": batch})
            if status != 200:
                raise SystemExit(f"  {sid}  fixes 업로드 실패 ({status})")
        status, finished = http("POST", f"{base}/walk/sessions/{sid}/finish",
                                {"ended_at": session["ended_at"]})
        if status != 200 or finished is None:
            raise SystemExit(f"  {sid}  finish 실패 ({status})")
        print(f"  {sid}  전송 완료 / {facts_summary({'facts': finished.get('facts'), 'encounters': finished.get('encounters')})}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("pull", "fetch", "push", "all", "clear"))
    parser.add_argument("--out", type=Path,
                        help="번들 폴더. 원좌표가 담기므로 레포 밖을 줄 것 (clear 외 필수)")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--delete", action="store_true",
                        help="PC 에 쓴 뒤 기기 사본을 지운다 (pull/all)")
    args = parser.parse_args()
    if args.command != "clear" and args.out is None:
        parser.error("--out 이 필요하다")

    if args.command == "clear":
        print("== clear (기기 export 삭제)")
        clear()
        return
    if args.command in ("pull", "all"):
        print("== pull (기기 → 폴더)")
        pull(args.out, delete=args.delete)
    if args.command in ("push", "all"):
        print("== push (미업로드 재전송)")
        push(args.out, args.base)
    if args.command in ("fetch", "all"):
        print("== fetch (서버 파생 → 폴더)")
        fetch(args.out, args.base)


if __name__ == "__main__":
    sys.exit(main())
