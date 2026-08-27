"""픽스처 경로를 에뮬레이터에 실시간으로 흘려 산책 한 번을 실제로 시킨다.

**Phase 1 과 같은 좌표를 쓴다** (`walk_fixture.route()`). 다른 건 경로가 아니라 경유지다 —
Phase 1 은 좌표를 서버에 직접 넣었고, 여기서는 GPS → FusedLocationSource → 서비스 → Room →
업로더 → 서버로 흐른다. 두 실행의 기하값이 같아야 그 사이가 옳다는 뜻이다.

시간 축은 재현되지 않는다. 주입은 실시간이라 `dt` 가 저작한 값이 아니라 벽시계다. 그래서
거리·밴드 체류는 Phase 1 과 맞아야 하지만 duration·moving_s 는 근사만 한다.

`WalkTrackingService` 는 `exported="false"` 라 `am start-foreground-service` 로 못 깨운다
(shell uid 권한 없음). 그래서 화면을 눌러 시작한다 — 버튼 좌표는 매번 UI 를 덤프해서 찾는다.

    uv run python -m scripts.verify.walk_emulator_drive
"""

import re
import subprocess
import sys
import time

from scripts.verify.walk_fixture import FIX_INTERVAL_S, PAUSE_AT_M, STOP_AT_M, STOP_S, route

ADB = "adb"
DUMP = "/sdcard/ui.xml"


def sh(*args: str, timeout: int = 60) -> str:
    """adb 출력은 UTF-8 이다. Windows 기본 cp949 로 읽으면 한글 UI 덤프에서 죽는다."""
    done = subprocess.run([ADB, *args], capture_output=True, timeout=timeout, check=False)
    return done.stdout.decode("utf-8", errors="replace")


def find_button(label: str) -> tuple[int, int] | None:
    """UI 덤프에서 라벨의 탭 좌표. Compose 라 resource-id 가 없어 텍스트로 찾는다."""
    sh("shell", "uiautomator", "dump", DUMP)
    xml = sh("shell", "cat", DUMP)
    for node in xml.split("<"):
        if f'text="{label}"' not in node:
            continue
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def tap(label: str) -> bool:
    spot = find_button(label)
    if spot is None:
        print(f"  [!] '{label}' 버튼을 못 찾았다")
        return False
    sh("shell", "input", "tap", str(spot[0]), str(spot[1]))
    print(f"  탭: {label} @{spot}")
    time.sleep(3)
    return True


def geo(lat: float, lng: float) -> None:
    sh("emu", "geo", "fix", f"{lng:.7f}", f"{lat:.7f}")   # 경도가 먼저다


def main() -> int:
    fixes = route()
    step_m = 0.0
    print(f"경로 {len(fixes)} 점, 예상 소요 {fixes[-1]['offset_s']:.0f}s")

    geo(fixes[0]["lat"], fixes[0]["lng"])      # 시작 지점을 먼저 잡아둔다
    time.sleep(3)
    sh("logcat", "-c")
    if not tap("동선 기록 시작"):
        return 1

    previous_offset = 0.0
    paused_done = False
    for index, fix in enumerate(fixes):
        offset = fix["offset_s"]
        wait = offset - previous_offset
        previous_offset = offset

        # 저작한 간격만큼 실제로 기다린다. 정지 구간은 같은 좌표에 시간만 흐르므로
        # 앱의 minUpdateDistance(1m) 필터에 걸려 fix 가 안 쌓이고, 서버가 그 공백을
        # 정지로 읽는다 — 그게 의도다.
        if wait > 0:
            time.sleep(wait)
        geo(fix["lat"], fix["lng"])

        step_m = fix["offset_s"]
        if index % 6 == 0:
            print(f"  [{index:2}/{len(fixes)}] t={offset:5.0f}s chain={fix['chain_index']}")

        # 저작한 pause 지점에서 실제로 UI 를 눌러 chain 을 끊는다
        if not paused_done and fix["chain_index"] == 0 and index + 1 < len(fixes) \
                and fixes[index + 1]["chain_index"] == 1:
            print(f"  --- pause/resume @{PAUSE_AT_M:.0f}m (chain 경계) ---")
            # 이 탭이 실패하면 chain 경계가 없는 산책이 나온다. 그런데 chain 이 앱에서
            # 서버까지 도는지 보는 것이 이 탭의 존재 이유라, 조용히 넘어가면 검증하지
            # 않은 것을 검증했다고 착각한다. 다른 탭과 같이 여기서 세운다.
            if not tap("일시정지") or not tap("계속 기록"):
                return 1
            paused_done = True

    time.sleep(FIX_INTERVAL_S)
    print(f"  정지 {STOP_S}s @{STOP_AT_M:.0f}m 포함, 마지막 t={step_m:.0f}s")
    if not tap("종료"):
        return 1
    time.sleep(8)

    print("\n--- logcat ---")
    print(sh("logcat", "-d", "-s", "WalkTrackingService:*", "WalkUploader:*"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
