"""영역 — 산책 사실을 지도 위의 **장면**으로 바꾼다. walk 사실의 소비자다.

`features/walk/` 이 `Segment`·`WalkFacts` 를 생산하고 여기가 그것을 `geo.cells` 의 육각
격자에 얹는다. `features/scene/` 과 같은 자리다 — 생산자는 판정도 서술도 하지 않고
(결정 #51), 소비자가 규칙과 버전을 달고 만든다.

    paint.py       움직이는 점이 붓이 되어 칠한다 — Cellophane · stack
    region.py      사용자가 그린 면 안의 체류 — walk/encounter.py 의 면 버전
    layers.py      어떤 산책을 골라 어떤 장을 겹칠지 — 위 둘의 질의층
    experience.py  그 질의를 화면·문장이 쓸 근거로 조합한다 — 방문률·추세·후보

`paint`·`region`·`layers` 는 `geo/` 에 있었다. 셋 다 `features.walk.facts.Segment` 를
소비하는데 도메인 패키지에 앉아 있어서 `geo → features` 역방향이었고, 그 두 줄이
`discovery → journey → geo → features → discovery` 4-패키지 순환을 닫는 마지막 고리였다
(결정 #67). `geo/` 에는 산책을 모르는 공간 원시(`cells.py` 의 육각 격자 수학)만 남는다.

`experience.py` 는 반대쪽에서 여기로 왔다. 처음엔 `features/walk/experience.py` 로 뒀다가
`test_walk_package_has_no_judgment_modules` 에 걸렸다. **토큰이 걸린 것은 우연이지만**
(`xp` 가 `e-xp-erience` 안에 있다) 자리는 실제로 틀렸다 — walk 패키지 docstring 이 "그 위에
얹을 것은 이 사실을 **소비하는** 별도 결정" 이라고 이미 적어 뒀고, 방문률로 "익숙한 곳" 을
고르는 것은 수집이 아니라 의미 부여다. 이름만 바꿔 피하지 않고 소비자 쪽으로 옮겼다.

## 경계

여기서 나가는 것은 **숫자와 후보**지 문장이 아니다. 문장은 응답 에이전트 몫이다
(#53 — 판단은 근거를 가진 쪽, 실행은 데이터를 가진 쪽). 그리고 여기서 만드는 근거는
**질의 결과지 저장물이 아니다** — 지도가 저장된 진실이 아닌 것과 같은 이유다
(`docs/explorations/walk/evidence-layer.md`).
"""
