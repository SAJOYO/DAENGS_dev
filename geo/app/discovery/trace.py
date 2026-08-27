"""resolver 가 무엇을 왜 정했는지의 기록.

두 가지를 위해 있다.

1. **상황이 사용자 설정을 눌렀으면 말해줘야 한다.** 지금 `changes` 는 "사용자가 바꾼 것"만
   보고한다. 긴급도 때문에 도보 제한이 무시됐는데 화면에 아무 말이 없으면, 사용자는 자기가
   건 조건이 살아 있다고 믿는다. `diff.py` 가 이미 반대 방향으로 같은 판단을 했다 —
   "숨기지는 않는다 — 사용자가 바꿨는데 아무 반응이 없으면 그게 더 나쁘다".

2. **디버깅.** 어긋난 배선은 값이 아니라 경로를 봐야 잡힌다. 어느 사실이 어느 계획을
   먹였는지가 남아 있으면 "왜 밤 라우팅이 됐지"에 답할 수 있다.

trace 는 판정을 하지 않는다. 이미 내려진 결정을 적을 뿐이다.
"""

from dataclasses import dataclass, field

Axis = str  # context | target | journey | view


@dataclass(frozen=True)
class TraceEntry:
    axis: Axis
    what: str                 # 무엇이 정해졌나 — 사람이 읽는 한 줄
    because: str = ""         # 무엇이 그렇게 시켰나
    overrode: str = ""        # 사용자가 걸어둔 설정을 눌렀다면 그 이름. 비어 있으면 덮어쓴 게 없다


@dataclass
class ResolutionTrace:
    entries: list[TraceEntry] = field(default_factory=list)

    def note(self, axis: Axis, what: str, because: str = "", overrode: str = "") -> None:
        self.entries.append(TraceEntry(axis=axis, what=what, because=because, overrode=overrode))

    def overrides(self) -> list[TraceEntry]:
        """사용자 설정을 누른 것만. **이건 화면에 반드시 나가야 한다.**"""
        return [e for e in self.entries if e.overrode]

    def by_axis(self) -> dict[Axis, list[str]]:
        out: dict[Axis, list[str]] = {}
        for e in self.entries:
            out.setdefault(e.axis, []).append(e.what)
        return out
