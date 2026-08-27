---
status: exploring
implementation: partial
last_verified: 2026-08-26
depends-on:
  - contracts/walk-record.md
  - explorations/walk/territory-paint.md
  - decisions/2026-08-25-walk-data-retention.md
---
# 산책 세션의 연속성과 붓의 연속성은 다르다 — 중단·재개·체류 설계 초안

이 문서는 산책 중 시설 체류, 수동 일시정지, GPS 중단, 앱 프로세스 종료가 생겼을 때
**무엇을 하나의 산책으로 볼지**, **어디까지를 위치 증거로 볼지**, 그리고 그 증거를
`territory-paint`의 영역 칠하기와 체류 표현에 어떻게 넘길지를 정리한다.

아직 채택된 결정이 아니다. 다만 완전히 백지에서 시작하는 문서도 아니다. Android와 서버에는
이미 pause/resume, `chain_index`, unfinished session, gap/jump 단절, `Segment`, 정지 사건,
`Cellophane`이 일부 구현되어 있다. 이 문서의 역할은 그 조각들의 **제품 의미와 경계**를 맞추고,
아직 없는 recovery·resume UX·dwell 표현을 어디까지 확장할지 정하는 것이다.

핵심 가설은 하나다.

> **산책 세션의 연속성과 GPS/붓의 연속성은 분리한다.**
>
> GPS가 끊겨도 하나의 산책일 수 있다. 그러나 관측이 끊긴 곳을 붓으로 이어 칠하지 않는다.

`territory-paint.md`의 "산책 한 번 = 셀로판 한 장"과 충돌하지 않는다. 한 장의 셀로판 안에는
서로 떨어진 여러 관측 구간이 있을 수 있다. 빈 구간은 **모르는 구간**이지 시작점과 끝점을
연결해야 하는 구간이 아니다.

---

## 0. 이 서비스에서 산책 기록이 왜 특별한가

일반적인 러닝 앱에서 GPS 궤적은 주로 "운동 한 번을 재생하고 거리·페이스를 계산하는 기록"이다.
이 서비스에서는 그것에 하나가 더 붙는다.

**움직이는 위치가 붓이 되어 지도를 칠한다.**

```
GPS 위치/Segment
      ↓
거리 감쇠를 가진 Brush
      ↓
지나간 주변 셀에 물감
      ↓
산책 한 번 = Cellophane 한 장
      ↓
계절·시간대·날씨 같은 조건으로 장을 고름
      ↓
여러 장을 겹쳐 공간 경향을 질의
```

그러므로 누락과 오삽입의 비용이 대칭이 아니다.

- 실제로 걸은 구간을 기록하지 못함 → 그 영역이 덜 칠해진다
- 기록하지 않은 구간을 추정해서 연결함 → 실제로 경험하지 않은 공간이 경험으로 저장된다

두 번째가 더 위험하다. 이 문서의 recovery 원칙은 "완벽한 선을 복원"하는 것이 아니라
**관측한 것과 관측하지 않은 것을 섞지 않는 것**에서 출발한다.

또 `Cellophane`은 최종 색 이미지가 아니다. 산책별 공간 자료를 나중에 조건별로 다시 겹치기 위한
중간 표현이다. 색, 등급, 태그는 저장 진실보다 아래 렌더/판정 층에 둔다.

---

## 1. 이 문제가 생긴 실제 사용자 시나리오

산책은 러닝처럼 시작부터 끝까지 계속 움직이는 활동만이 아니다.

예를 들어 사용자가 반려견과 산책하다 애견카페에 들어갔다고 하자.

```
18:00 집에서 산책 시작
      ↓
18:00~18:25 집 → 애견카페
      ↓
18:25 카페에서 쉼
      ↓
18:25~19:05 40분 체류
      ↓
19:05 다시 산책
      ↓
19:05~19:30 카페 → 공원 → 집
      ↓
19:30 종료
```

사용자 관점에서는 자연스럽게 **한 번의 외출/산책**일 수 있다.

하지만 카페 안에서 40분 동안 고정밀 GPS를 계속 돌리는 것은 다음 이유로 반드시 좋은 선택이 아니다.

- 배터리를 계속 쓴다
- 실내 GPS가 크게 흔들릴 수 있다
- 사용자는 쉬는 동안 위치 기록이 계속 필요하다고 생각하지 않을 수 있다
- 흔들린 raw fix가 체류 지점을 넓은 얼룩으로 만들 수 있다

반대로 GPS를 껐다는 이유만으로 기록을 두 산책으로 자르면 구현의 생명주기가 사용자 경험의
생명주기를 결정한다.

그래서 질문이 이어진다.

```
시설에서 오래 쉰다
    ↓
GPS를 계속 켜둘 필요가 있는가?
    ↓
기록을 잠시 내려놓는 상태가 필요하다
    ↓
그렇다면 세션과 recorder의 생명주기는 같은가?
    ↓
아니다 — 한 세션 안에 여러 관측 구간이 필요하다
    ↓
관측 공백은 territory에서 어떻게 다룰까?
    ↓
연결하지 않는다
    ↓
그런데 "오래 머무름" 자체도 공간 경험 아닌가?
    ↓
반복 통과와 체류를 같은 진함으로 표현해도 되는가?
    ↓
아니다 — 증거와 시각 의미를 분리해서 본다
```

그래서 session continuity와 dwell이 한 문서에 있다. 둘은 별개의 기능처럼 보이지만,
**"이동 기록을 잠시 멈출 수 있는 산책"**을 인정하는 순간 같은 경계에서 나온다.

---

## 2. 현재 구현은 어디까지 와 있나

이 문서는 새 상태 머신을 코드에 바로 추가하자는 제안이 아니다. 이미 있는 것과 아직 없는 것을
먼저 구분한다.

| 축 | 현재 구현 | 이번 문서에서 논의하는 것 |
|---|---|---|
| Android 산책 기록 | `OFF / RECORDING / PAUSED` | 이 상태를 사용자 세션 의미와 어떻게 대응할지 |
| pause | 서비스의 고정밀 GPS 구독을 중단 | pause를 세션 종료와 분리하는 제품 의미 |
| resume | 서비스가 고정밀 GPS를 다시 획득 | 같은 세션의 새 관측 chain으로 이어지는지 |
| client `chainIndex` | 명시적 pause/resume마다 증가 | derived continuity와 용어를 분리 |
| 서버 연속성 | client chain 변경, gap, jump, reject에서 `Segment` 단절 | territory에서도 단절을 불변조건으로 유지 |
| process death | `START_NOT_STICKY`, 자동 복원 안 함 | unfinished session 복구 UX / 만료 정책 |
| unfinished session | Room에 `endedAtMillis IS NULL`로 남고 조회 가능 | Resume / Finish / Discard 표면 |
| 정지 사건 | `MotionEventOccurrence` 생성 | observed dwell 표현의 후보 증거 |
| territory | `Segment[] → Cellophane`, `occupancy/peak`, `stack()` | 여러 연속 구간과 dwell을 어떻게 읽을지 |
| upload recovery | 개발용 bundle 재전송은 있음 | 제품 WorkManager/retry는 별도 운영 결정 |

따라서 front matter의 `implementation: partial`은 이 뜻이다.

- **수집 메커니즘 일부는 이미 동작한다.**
- **이 문서의 제품 의미와 recovery/dwell 정책은 아직 exploring이다.**

---

## 3. 용어 사전 — 같은 말을 다른 층에서 쓰지 않는다

이 문서는 여러 기존 타입과 설명용 개념을 같이 사용한다. 처음 읽는 사람이 새 저장 타입을
추측하지 않도록 구분한다.

### `WalkSession`

사용자 관점에서 한 번의 산책/외출을 묶는 상위 단위.

현재 서버 계약의 `WalkSession`에는 `started_at`, `ended_at`과 처리 상태
`open → sealed → derived → purged`가 있다. 뒤의 네 상태는 서버 finalize 처리 단계이지,
사용자가 보는 pause/resume 상태와 같은 축이 아니다.

### `WalkFix`

기기가 보고한 위치 원본 한 점. `client_seq`, `chain_index`, 시각, 좌표, 정확도 등을 가진다.

### client/source chain

기기가 명시적으로 만든 연속성 경계. 현재 Android는 pause/resume 때 `chainIndex`를 증가시킨다.

즉:

```
chainIndex 0   기록
pause
chainIndex 1   resume 후 기록
```

여기서 "client chain"이라고 부른다.

### derived continuity chain

서버가 raw fix를 검증해 만든 **유효 `Segment` 연속열**이다. client chain 변경뿐 아니라
60초 초과 gap, 200m 초과 jump, 저정확도 reject, out-of-order 같은 사건에서도 끊긴다.

따라서 client chain과 derived continuity chain은 같은 것이 아니다.

### `Segment`

서버가 받아들인 연속 두 점 `a → b` 사이의 유효 구간. 시간 `dt`, 거리 `dist`, 이동 여부,
서버가 만든 continuity `chain_index`를 가진다.

현재 territory paint가 실제로 소비하는 것은 raw fix가 아니라 이 `Segment[]`다.

### paint stroke

**설명용 개념어다. 현재 별도 `PaintStroke` 저장 타입은 없다.**

하나의 derived continuity chain이 지도에 남긴 연속 자국을 이 문서에서 paint stroke라고 부른다.

### `Cellophane`

산책 한 번에서 만들어진 셀 맵. 현재 `occupancy`, `peak`, brush/grid 버전 정보를 가진다.
여러 장을 `stack()`해서 빈도와 공간 노출을 질의한다.

### coverage

붓에 의해 칠해진 공간 범위를 가리키는 일반적인 말. 별도 영속 타입을 뜻하지 않는다.

### `occupancy`

현재 `paint_sheet()`가 계산하는 **시간 가중 spatial exposure**다. Segment를 일정 간격으로
쪼개고 각 조각의 `seg.dt`를 붓 가중치와 곱해 셀에 누적한다.

즉 이름과 달리 "몇 번 방문"도 아니고 "정지 시간"도 아니다.

### `walks`

여러 `Cellophane`을 겹칠 때 `min_peak` 조건을 통과해 해당 셀을 칠한 **서로 다른 산책 수**.
반복 방문/친숙도에 가까운 축이다.

### `peak`

한 산책이 한 셀에 남긴 최대 붓 세기. 자주 지나갔는지가 아니라 그 산책이 해당 셀 중심에
얼마나 가깝게 들어왔는지를 구별하는 재료다.

### observed dwell

위치 기록이 켜진 동안 정지 사건으로 실제 관측된 체류. 현재 `MotionEventOccurrence`가 후보 증거다.

### suspended interval

사용자가 기록을 내린 시각과 재개한 시각 사이. **위치를 관측하지 않았다는 사실**만 안다.
그 시간 내내 같은 곳에 있었다는 증거는 아니다.

### declared visit

향후 사용자가 "이 카페에서 쉬었다"처럼 명시적으로 확인한 방문. 자동 관측과 구별되는 별도
provenance 후보이며 아직 계약이 아니다.

---

## 4. 대표 산책 하나를 처음부터 끝까지 따라간다

앞의 카페 사례를 데이터 관점에서 다시 본다.

```
18:00  사용자가 산책 시작
        WalkSession = OPEN
        recorder = RECORDING
        client chain = 0

18:00~18:25
        집 → 카페
        WalkFix(chain=0) 수집
        서버가 유효 Segment 생성
        derived continuity chain 0
        territory에 stroke A 생성 가능

18:25  사용자가 "기록 잠시 멈춤"
        WalkSession = OPEN 유지
        recorder = SUSPENDED_BY_USER
        고정밀 GPS OFF

18:25~19:05
        위치 관측 없음
        Segment 없음
        territory paint 없음
        observed dwell도 아님
        단지 suspended interval 40분이라는 사실만 존재 가능

19:05  사용자가 Resume
        WalkSession = OPEN 유지
        recorder = RECORDING
        client chain = 1

19:05~19:30
        카페 → 공원 → 집
        WalkFix(chain=1) 수집
        새 derived continuity chain
        territory에 stroke B 생성 가능

19:30  사용자가 Finish
        WalkSession 사용자 lifecycle 종료
        서버 finish 시 수집 원본 → 파생 → purge
```

결과적인 공간 의미는:

```
Cellophane #17

stroke A   [unknown / no evidence]   stroke B
█████████             ···            █████████
집 → 카페                            카페 → 집
```

가운데 40분은 "카페를 칠한 40분"이 아니다. 이 사용자가 카페에서 실제로 쉬었을 가능성이 높더라도,
**현재 관측만으로는 그 추론을 공간 증거로 승격하지 않는다.**

사용자가 resume를 잊고 카페에서 집까지 돌아왔다면 후반 coverage는 빠진다. 그것은 제품 사용감의
손실이지 과거 trajectory를 추정해서 채워야 할 데이터 결함은 아니다.

---

## 5. 세션 lifecycle과 recorder lifecycle을 분리한다

기존 초안처럼 `RECORDING / SUSPENDED / FINISHED`를 하나의 상태 머신으로 그리면 문제가 생긴다.

예를 들어 프로세스가 죽은 경우:

- 사용자는 Finish를 누르지 않았다
- 세션은 아직 끝났다고 볼 근거가 없다
- GPS recorder는 실행 중이 아니다
- 사용자가 의도적으로 suspend한 것도 아니다

한 enum에 넣으면 이 상태를 표현하기 어렵다.

그래서 개념상 최소 두 축으로 본다.

### A. 사용자 세션 lifecycle

```
OPEN
  │
  ├─ recording 가능
  ├─ suspend 가능
  ├─ interrupted 가능
  │
  └──────────→ FINISHED
```

`OPEN`은 "사용자가 이 산책을 아직 끝냈다고 확정하지 않았다"에 가깝다.

### B. recorder 실행 상태

```
RECORDING
SUSPENDED_BY_USER
INTERRUPTED
OFF
```

여기서 `INTERRUPTED`를 당장 DB enum으로 저장하자는 뜻은 아니다.

예를 들어 다음 사실에서 **계산할 수 있는 상태**일 수 있다.

```
unfinished session 있음
+ foreground service / active recorder 없음
= interrupted candidate
```

중요한 것은 저장 상태 수가 아니라 개념 축을 섞지 않는 것이다.

### 상황 표

| 상황 | 사용자 session | recorder | 위치 증거 |
|---|---|---|---|
| 평소 산책 | OPEN | RECORDING | 계속 생성 |
| 카페에서 수동 중단 | OPEN | SUSPENDED_BY_USER | 중단 |
| resume | OPEN | RECORDING | 새 chain으로 생성 |
| 앱/서비스 process death | OPEN 추정 | INTERRUPTED | 중단 |
| 사용자가 종료 | FINISHED | OFF | 더 이상 생성 안 함 |
| 나중에 unfinished를 종료 처리 | FINISHED | OFF | 기존 관측만 사용 |

서버의 `OPEN → SEALED → DERIVED → PURGED`는 이 표와 별개다. 그것은 **finish 요청 이후의 서버
처리 lifecycle**이다.

---

## 6. client chain과 derived continuity chain을 분리한다

`chain`이라는 단어를 하나만 쓰면 현재 구현을 잘못 이해하기 쉽다.

### client/source chain

Android가 명시적 pause/resume 의도를 서버에 전달한다.

```
fix 0   client chain 0
fix 1   client chain 0
pause
resume
fix 2   client chain 1
fix 3   client chain 1
```

서버는 서로 다른 client chain의 점 사이에 Segment를 만들지 않는다.

### derived continuity chain

그러나 client가 pause하지 않았어도 서버는 관측 품질 때문에 연속성을 끊을 수 있다.

현재 `compute_facts()`의 대표 단절은:

- client `chain_index` 변경
- `dt > 60s` gap
- `dist > 200m` jump
- accuracy reject
- out-of-order
- 세션 시간 범위 밖 fix

이다.

따라서:

```
client chain 0
   ├─ derived chain A
   ├─ 90초 gap
   └─ derived chain B
```

도 가능하다.

### territory가 믿는 경계

`paint_sheet()`가 받는 입력은 서버가 만든 `Segment[]`다. 따라서 영역 칠하기에서 믿어야 할
연속성은 **derived continuity**다.

불변조건:

> **서로 다른 derived continuity chain 사이에는 paint interpolation을 만들지 않는다.**

현재 `paint_sheet()`가 각 `Segment` 내부만 보간하므로 이 원칙과 방향은 맞다. 다만 같은 세션의
여러 chain을 넣었을 때 gap 양쪽이 시각/집계 어디에서도 다시 연결되지 않는 테스트를 명시적으로
두는 것이 좋다.

---

## 7. 누락 허용 원칙 — false negative를 false positive보다 싸게 본다

영역 칠하기에서는 다음 비대칭을 의도적으로 받아들인다.

### A. 실제로 걸었지만 기록을 놓침

```
실제:    A ───────── B
기록:    A ─── X       B 이후 없음
```

결과:

- 실제 경험한 영역 일부가 안 칠해짐
- 다음 산책에서 다시 칠릴 수 있음
- 사용자에게 "이번 산책이 일부 누락됨"이라고 설명 가능

### B. 기록이 없는데 추정해서 연결

```
기록:    A       B
추정:    A ───── B
```

사용자는 실제로:

- 차를 탔을 수도 있고
- 지하철을 탔을 수도 있고
- 다른 경로로 이동했을 수도 있고
- 그 자리에 계속 있었을 수도 있다

그런데 직선을 칠하면 **없던 공간 경험을 영속 파생에 넣는다.**

그래서 원칙을 다음처럼 둔다.

> **놓친 영역은 허용한다. 관측하지 않은 영역을 경험으로 만들지는 않는다.**

GPS gap, process death, 수동 suspend, resume 누락에 동일하게 적용한다.

---

## 8. Pause / Suspend / Resume — 제품 이름보다 의미를 먼저 고정한다

현재 Android UI에는 `PAUSED`와 "일시정지/계속 기록"이 있다. 이 문서에서 `suspend`라는 말을
쓰는 이유는 새로운 기능 이름을 강요하기 위해서가 아니다.

강조하려는 의미는:

> **세션은 유지하지만 고정밀 위치 추적은 의도적으로 내린다.**

이다.

제품 UI에서는 그대로 "일시정지"라고 불러도 된다. 다만 일반적인 10초 신호 대기와
"애견카페에서 40분 쉬며 GPS도 끈다"를 나중에 UX상 구별할 필요가 생길 수 있으므로,
내부 기획에서는 장기 중단 의미를 `suspend`라고 부른다.

### 기본 재개 정책 후보

1차 후보는 **사용자 명시 resume + reminder 보조**다.

이유:

- 자동 resume하려면 이동을 감지할 무언가를 계속 켜야 한다
- 저전력 위치, geofence, Activity Recognition도 비용과 오판이 있다
- 산책은 느린 이동과 잦은 정지가 많아 단순 속도 기반 auto-pause/resume에 불리하다
- resume를 놓쳐도 coverage 누락은 아쉽지만 진실성 파괴는 아니다

따라서 우선:

```
지속 알림
"산책 기록이 일시정지 중입니다"
[계속 기록] [종료]
```

또는 앱 재진입 시:

```
산책이 일시정지되어 있습니다.
[계속 기록] [종료]
```

같은 표면부터 검토한다.

자동 resume는 실제 기기에서 **재개 누락률이 사용자 경험을 얼마나 해치는지** 측정한 뒤에 추가한다.

---

## 9. Process death recovery — 기록 복구가 아니라 사용자 의도 복구

예기치 않은 프로세스 종료는 사용자가 산책을 끝냈다는 뜻이 아니다.

현재 Android는 `START_NOT_STICKY`이고 process death 후 자동 산책 복원은 하지 않는다.
대신 Room에 `endedAtMillis IS NULL`인 unfinished session이 남는다.

그러므로 앱 재진입 시 기획 후보는:

```
완료되지 않은 산책이 있습니다.
마지막 기록: 18:42
기록된 시간: ...
기록된 거리: ...

[계속 기록]
[여기까지 종료]
[버리기]
```

### 계속 기록

- 같은 `WalkSession`을 유지하는 후보
- 새 client chain에서 시작
- 과거 마지막 fix와 현재 위치 사이에 Segment를 만들지 않음

### 여기까지 종료

- 사용자가 세션을 종료했다고 확인
- `ended_at`을 어떤 시각으로 둘지는 별도 정책이 필요
- 마지막 fix 시각을 자동으로 "실제 산책 종료"라고 단정하지 않는다

### 버리기

결정 #57의 삭제 정책으로 이동한다. 로컬 원본과 서버/파생이 있다면 일관되게 제거해야 한다.

### 자동 만료

8시간, 하루, 다음날 같은 숫자를 지금 정하지 않는다. 실제 unfinished session 발생 패턴을 본 뒤 정한다.

중요한 것은 자동 만료가 정해져도 **공백을 trajectory로 복구하지 않는 원칙은 바뀌지 않는다.**

---

## 10. 현재 territory의 `occupancy`는 이미 시간의 영향을 받는다

체류 시각화를 논의할 때 가장 쉽게 혼동되는 부분이다.

현재 `paint_sheet()`는 각 `Segment`를 거리 방향으로 조각내고:

```
share = seg.dt / pieces
```

를 각 조각의 붓 weight와 곱해 `occupancy`에 더한다.

즉 현재 의미는 대략:

```
occupancy
≈ 해당 공간 근처에서 관측된 시간 × 붓의 거리 감쇠
```

이다.

그리고 현재 구현은 `Segment.moving`이 `False`인 유효 정지 후보 Segment도 paint에서 제외하지 않는다.
따라서 "오래 머무르면 기존 territory에서도 어느 정도 진해진다"는 성질이 이미 있다.

이 사실 때문에 `occupancy`와 `dwell`을 같은 것으로 부르면 안 된다.

### `occupancy`가 높은 이유는 여러 가지다

- 정말 한 곳에서 오래 정지했다
- 같은 공간을 아주 천천히 걸었다
- 짧은 구간을 여러 번 왕복했다
- 붓 중심에 가까운 경로에서 시간이 많이 걸렸다

그러므로 `occupancy`는 **spatial exposure**이지 "여기에서 쉼"이라는 행동 분류가 아니다.

---

## 11. 공간 경향을 세 축으로 본다

현재/향후 의미를 다음처럼 분리하면 겹침과 체류를 같은 색에 억지로 넣지 않아도 된다.

### 11.1 `walks` — 반복 방문 / 친숙도

질문:

> 서로 다른 산책에서 이 공간을 얼마나 반복해서 이용했나?

재료:

- 산책별 `Cellophane`
- 산책별 `peak`
- `stack(min_peak=...)`

예:

```
1회   ░
3회   ▒
10회  ▓
20회  █
```

이 축이 "셀로판이 여러 장 겹쳐 진해진다"는 비유에 가장 직접적이다.

### 11.2 `occupancy` — 관측된 spatial exposure

질문:

> 선택된 산책들에서 이 공간 주변에 얼마나 많은 시간 가중 노출이 있었나?

같은 산책 안에서 오래 머물러도 증가할 수 있고, 여러 산책이 겹쳐도 증가한다.

따라서 친숙도와 완전히 같지 않다.

### 11.3 dwell — 정지/체류 행동

질문:

> 실제 위치 증거로 이곳에서 정지 상태가 얼마 동안 관측됐나?

후보 재료:

- `MotionEventOccurrence`
- 향후 별도 dwell aggregation

이 축은 행동 사건이다. occupancy가 높다고 자동으로 dwell이라고 부르지 않는다.

### 세 축을 미리 하나로 접지 않는다

| 축 | 주 질문 | 현재 재료 | 색/표현 후보 |
|---|---|---|---|
| 반복 `walks` | 자주 왔나 | Cellophane + peak | opacity/saturation |
| `occupancy` | 공간 노출이 많나 | Segment 시간 × brush | 별도 등급 또는 분석값 |
| dwell | 여기서 머물렀나 | stop occurrence | halo/번짐/질감/contour |

구체 색은 아직 정하지 않는다. 중요한 것은 **세 값을 하나의 "진함" 숫자로 영속화하지 않는 것**이다.

---

## 12. "싸인펜을 대고 있으면 진해진다"를 그대로 raw GPS에 적용하지 않는다

시각적 비유는 유용하지만 센서 모델과 동일하지 않다.

폰이 벤치 위에 가만히 있어도 GPS는 한 점에 고정되지 않는다.

```
실제 위치
    ●

raw GPS
  x    x
     x
 x      x
    x
```

raw fix마다 brush를 계속 찍어 "한 자리에 오래 있었으니 진해짐"을 만들면 실제 벤치 하나가
수십 미터 얼룩으로 확장될 수 있다.

따라서 dwell 표현은 raw point 밀도보다 **정지 사건을 먼저 만든 후 그 사건을 렌더링하는 방향**을
우선 검토한다.

현재 `MotionEventOccurrence`는:

- 시작/종료 시각
- duration
- 중심 lat/lng
- `accuracy_p50_m`
- fix count

를 가진다.

즉 단순한 점 하나보다 "얼마나 오래, 어느 정도 위치 확신으로 정지가 관측됐는가"를 표현하기
좋은 후보 증거다.

구체 dwell 반경/최소 시간은 현재 10초 stop 문턱을 그대로 제품 의미로 승격하지 않는다.
그 값은 계산 정책 v4의 잠정 수집 문턱이고, 실제 시각화/태그 기준은 실측 후 별도로 고른다.

---

## 13. Suspend 시간은 observed dwell이 아니다

이 문서에서 가장 중요한 provenance 경계 중 하나다.

예:

```
18:25 카페 앞에서 GPS OFF
19:05 카페 근처에서 GPS ON
```

우리가 아는 사실:

- 18:25에 해당 위치 근처에서 기록이 중단됐다
- 19:05에 해당 위치 근처에서 다시 기록됐다
- 두 시각 사이에 40분이 지났다

모르는 것:

- 40분 내내 카페 안에 있었는가
- 카페에서 나와 다른 곳을 다녀왔는가
- 차를 타고 이동했다가 돌아왔는가
- 폰만 그곳에 두었는가

그러므로:

```
suspended interval 40m
≠ observed dwell 40m
```

이다.

### 증거 종류를 구분한다

| 종류 | 뜻 | 위치/체류 주장 강도 |
|---|---|---|
| observed dwell | GPS ON 상태의 정지 사건 | 실제 관측 |
| suspended interval | 추적 OFF~ON 사이 시간 | 중간 위치 모름 |
| declared visit | 사용자가 장소/휴식을 확인 | 사용자 선언 |
| facility encounter | 시설 대표점 주변 동선 관측 | 시설 주변에 있었음, 방문 판정과는 별개 |

향후 "애견카페에서 40분 쉬었음"을 제품에서 쓰려면 suspended interval만으로 만들지 않는다.

예를 들어:

- 사용자가 시설 카드를 열고 "여기서 쉬기"를 눌렀다
- resume 시 "이 장소에서 쉬었나요?"를 확인했다
- 별도 visit 이벤트가 생겼다

같은 추가 근거가 있어야 declared visit으로 다룰 수 있다.

이것을 observed dwell과 같은 provenance로 합치지 않는다.

---

## 14. 시간도 하나의 숫자가 아니다

세션과 recorder를 분리하면 "산책 시간"도 최소 네 가지 뜻을 가질 수 있다.

### `session_elapsed_s`

```
ended_at - started_at
```

사용자 외출의 벽시계 시간 후보.

카페 suspend 40분도 포함된다.

### `observed_s`

현재 `WalkFacts.duration_s`에 가까운 뜻.

서버가 유효 `Segment`로 받아들인 `dt` 합이다. 60초 초과 gap이나 reject, 명시적 chain 단절을
건너뛴 시간은 포함하지 않는다.

### `moving_s`

현재 `WalkFacts.moving_s`.

유효 Segment 중 이동 임계(`>= 0.5m/s`)를 통과한 시간이다.

### `suspended_s`

사용자가 명시적으로 위치 기록을 내린 시간 합계 후보. 아직 현재 outbound 계약의 필드는 아니다.

### 왜 `active duration`이라는 말을 피하나

`active`는 사람마다:

- GPS가 켜진 시간
- 실제 이동 시간
- 산책 세션이 열린 시간

으로 읽힐 수 있다. 이미 `moving_s`가 있으므로 이 문서에서는 가능한 한 `observed`, `moving`,
`elapsed`, `suspended`를 분리한다.

어떤 값을 앱에서 큰 숫자로 "산책 시간"이라고 보여줄지는 UX 결정이다. 하지만 원 데이터/파생을
하나의 숫자로 먼저 접지 않는다.

---

## 15. 반복 통과와 체류를 시각적으로도 같은 채널에 굽히지 않는다

예를 들어 다음 두 셀을 생각한다.

```
A: 같은 길을 20번 통과
B: 한 번 갔지만 벤치에서 30분 정지
```

둘을 단순한 "진한 파랑" 하나로 표시하면 사용자는 둘을 구별할 수 없다.

그래서 렌더링 후보는 의미별 채널을 나누는 것이다.

### 반복 방문

- 셀로판 겹침
- opacity
- saturation
- discrete familiarity grade

### dwell

- 중심 halo
- 잉크가 스며든 듯한 번짐
- 별도 texture
- contour/ring
- 지도 레이어 토글

이것은 디자인 확정안이 아니다.

불변조건은:

> **반복 방문과 dwell을 같은 영속 숫자로 합쳐서 나중에 분리할 수 없게 만들지 않는다.**

색은 마지막 단계다.

```
관측/파생 사실
      ↓
집계
      ↓
판정/등급
      ↓
렌더 정책
      ↓
색/질감
```

---

## 16. 한 Cellophane 안에 여러 stroke가 있어도 된다

`territory-paint.md`의 "산책 한 번 = 셀로판 한 장"을
"산책 한 번 = 하나의 연속 polyline"으로 읽으면 안 된다.

예:

```
WalkSession #17
├─ derived chain 0
│    └─ stroke A
├─ unknown gap
├─ derived chain 1
│    └─ stroke B
├─ GPS quality reject gap
└─ derived chain 2
     └─ stroke C
```

개념적으로:

```
Cellophane #17
= A + B + C의 관측 공간 집계
```

이다.

`Cellophane`은 "이 산책에서 관측된 공간"을 묶는 장이지, 빈 공간을 메워 완전한 이동 경로를
만드는 geometry가 아니다.

향후 산책별 셀 맵을 영구 보관하기로 결정하더라도 gap provenance를 어떻게 보존할지는 별도 검토가
필요하다. 현재는 원좌표 purge 이후 무엇을 남길지 자체가 `territory-paint`의 열린 질문이다.

---

## 17. 외부 서비스 선례 — 세션과 GPS recorder 분리는 이상한 모델이 아니다

이 문서가 외부 서비스를 그대로 복제하려는 것은 아니다. 다만 "GPS 추적을 멈췄는데 같은 activity를
나중에 계속한다"는 사용자 모델이 실제 제품에도 존재하는지 확인했다.

### COROS — Resume Later

COROS의 공식 도움말(2026-06-26 업데이트)은 `Resume Later`를 제공한다.

- 활동을 pause한 뒤 `Resume Later` 선택
- GPS tracking을 pause하고 watch는 standby로 돌아감
- unfinished activity는 유지됨
- 같은 activity mode를 열면 `Resuming unfinished activity?`로 재개 가능
- 같은 activity 안에서 여러 번 Resume Later 가능
- 최종 저장 시 여러 구간을 하나의 activity summary로 합침
- 30분 이상 쉴 것으로 예상할 때 사용을 권장

참고:
https://support.coros.com/hc/en-us/articles/4409363080980-How-to-Use-Resume-Later

우리에게 주는 근거는 "COROS처럼 구현하자"가 아니다.

> **activity/session lifecycle과 GPS recorder lifecycle을 분리하는 사용자 모델은 이미 성립한다.**

반려견 산책은 스포츠 기록보다 정지·시설 체류가 더 잦을 수 있으므로, 이 분리가 오히려 더 중요할
가능성이 있다.

---

## 18. 프라이버시/보관 결정과의 관계

결정 #57은 이미 다음을 정했다.

- 연속 raw trajectory는 finish 이후 서버에 남기지 않는다
- `WalkFacts` 같은 무좌표 집계는 장기 보관 후보
- 좌표를 가진 `MotionEventOccurrence`, `FacilityEncounter`는 기본이 짧은 보관
- 구체 보관 기간 전에는 실사용 업로드를 켜지 않는다

여기에 결정 #69 가 층을 하나 더 얹었다 — **셀로판 장은 무좌표 집계가 아니다.** 칸은 반올림된
좌표이고, 이 층은 민감도가 레코드 하나가 아니라 **몇 장을 갖고 있나**로 정해진다. 한 장으로는
집을 220m 로밖에 못 찍지만 겹치면 12m 다. 그리고 격자·붓을 고르는 것이 업로드 게이트에 들어왔다.

이 문서는 그 결정들을 바꾸지 않는다.

오히려 dwell을 추가할수록 좌표 민감도가 커진다는 점을 강조한다.

반복되는 정지 위치는:

- 집
- 직장
- 자주 가는 공원
- 자주 방문하는 시설

을 드러낼 수 있다.

따라서 dwell layer를 오래 보관하거나 공유 지도에 노출하려면 territory의 `home_bias` 문제와 함께
별도 privacy 검토가 필요하다.

특히 suspended interval을 "카페 체류"로 쉽게 승격하지 않는 것은 진실성뿐 아니라 privacy에도
도움이 된다. 불필요한 개인 행동 의미를 자동 생성하지 않는다.

---

## 19. 구현에 미치는 영향 — 당장 새 타입을 많이 만들지 않는다

이 문서가 탐색 단계에서 요구하는 최소 구현 방향은 작다.

### 이미 유지할 것

- pause/resume 때 client `chainIndex` 증가
- 서버에서 chain/gap/jump/reject를 넘어 Segment를 만들지 않음
- `paint_sheet()`는 유효 Segment만 소비
- unfinished session을 로컬에 남김
- raw GPS와 파생 사실의 provenance를 섞지 않음

### 추가하기 전에 검증할 것

- `SUSPENDED_BY_USER`, `INTERRUPTED`를 실제 persisted enum으로 둘 필요가 있는가
- session elapsed/suspended 시간을 별도 이벤트 없이 계산할 수 있는가
- pause/resume event log가 필요한가
- recovery UI만으로 충분한가
- WorkManager retry가 언제 필요한가
- dwell aggregate를 `Cellophane`에 넣을지 별도 레이어로 둘지

### 만들지 말아야 할 것

이 문서만 보고 바로 다음을 만들지 않는다.

- `PaintStroke` ORM 테이블
- 자동 trajectory 보간
- suspended interval 기반 자동 facility visit
- 근거 없는 auto-resume 센서 조합
- 하나의 `intensity` 값으로 walks/occupancy/dwell 합치기

---

## 20. 현재 합의된 불변조건 후보

아직 `adopted decision`은 아니지만, 구현/실험을 진행할 때 우선 지킬 후보를 모으면 다음과 같다.

### 세션

1. **GPS OFF는 곧 session finish가 아니다.**
2. 한 사용자 산책 안에 여러 관측 chain이 존재할 수 있다.
3. process death는 사용자 finish 의도로 간주하지 않는다.

### 공간 증거

4. **derived continuity chain 사이를 연결하지 않는다.**
5. 관측하지 않은 구간을 직선/추정 경로로 paint하지 않는다.
6. 놓친 coverage는 허용한다.

### 체류

7. `occupancy`를 dwell과 동일시하지 않는다.
8. raw GPS point 밀도만으로 dwell heat를 만들지 않는다.
9. suspended interval을 observed dwell로 승격하지 않는다.
10. observed / declared / inferred evidence의 provenance를 섞지 않는다.

### 렌더링

11. 반복 방문과 dwell을 되돌릴 수 없는 단일 intensity로 저장하지 않는다.
12. 색/질감은 파생 사실보다 아래 렌더 정책이다.

### 시간

13. elapsed / observed / moving / suspended 시간을 같은 숫자로 미리 접지 않는다.

이 중 어떤 것을 실제 결정 문서로 승격할지는 실기기 측정과 후속 리뷰 뒤 정한다.

---

## 21. 아직 결정하지 않는 것

### 세션 lifecycle

- 열린 세션을 몇 시간/며칠 뒤 자동 만료할지
- 자정이 지나도 같은 산책으로 resume할 수 있는지
- 다른 산책을 시작하려 할 때 unfinished session을 강제로 처리할지

### recovery

- 재부팅 뒤에도 recovery 표면을 보일지
- process death 후 자동으로 foreground service를 복원할지
- `START_NOT_STICKY`를 유지할지
- "여기까지 종료"의 `ended_at`을 무엇으로 둘지

### resume UX

- 지속 알림 reminder만 둘지
- 이동 감지 reminder를 붙일지
- auto-resume까지 갈지
- UI 명칭을 "일시정지", "휴식", "나중에 계속" 중 무엇으로 할지

### dwell

- observed dwell의 최소 시간
- 공간 반경
- GPS accuracy를 시각 반경에 반영할지
- facility 내부/주변 체류를 어떻게 구분할지
- declared visit UX가 필요한지

### territory

- `walks`, `occupancy`, dwell을 실제 지도에서 어떤 채널로 표시할지
- dwell을 `Cellophane` 내부에 넣을지 별도 파생으로 둘지
- ~~어떤 공간 자료를 원좌표 purge 뒤 영구 보관할지~~ → [결정 #69](../../decisions/2026-08-26-walk-permanent-spatial-form.md) (집계 셀 맵 하나, 궤적은 안 남긴다)

### 운영

- 종료 후 제품 upload retry/WorkManager 정책
- 재시도 backoff
- 로컬 세션 보관 기간

---

## 22. 구현으로 넘어가기 전 측정할 것

### 1. 실제 정지 분포

실산책에서 다음 구간의 duration 분포를 본다.

- 신호 대기
- 냄새 맡기/잠깐 멈춤
- 벤치 휴식
- 공원 장기 정지
- 반려동물 시설 체류

목적은 행동 이유를 자동 분류하려는 것이 아니라 **정지 시간 스케일이 실제로 얼마나 갈리는지** 보는 것이다.

### 2. pause → GPS OFF → resume 배터리 효과

실기기에서:

- 30분 GPS 계속 ON
- 30분 pause/GPS OFF

를 비교한다.

절약량이 미미하면 복잡한 suspend UX의 가치가 낮아진다.

### 3. resume 누락률

사용자가 시설을 나온 뒤 얼마나 자주 resume를 잊는지 본다.

누락률이 낮으면 수동 resume가 충분하다. 높으면 reminder를 붙이고, 그 뒤에도 문제면 자동화를 검토한다.

### 4. multi-chain territory 테스트

한 세션에 의도적인 pause/gap/jump를 넣고:

- 각 Segment 내부는 칠해지는지
- chain 사이 빈 구간은 절대 칠해지지 않는지
- 여러 stroke가 한 Cellophane에 정상 집계되는지

테스트한다.

### 5. `occupancy`와 dwell 비교

같은 raw 실측을:

- 현재 `occupancy`
- `MotionEventOccurrence` 기반 dwell

로 각각 그린다.

천천히 걷는 길과 실제 장기 정지가 얼마나 다르게 나타나는지 본다.

### 6. GPS jitter 시각 실험

정지한 폰을 10분 이상 두고:

- raw point brush accumulation
- event center + accuracy 기반 dwell

을 비교한다.

"싸인펜 번짐"이 실제 의미를 표현하는지, 단순 센서 오차를 예쁘게 그린 것인지 확인한다.

### 7. 시각 채널 비교

최소 세 샘플을 만든다.

```
A. 자주 통과하지만 거의 안 멈춤
B. 드물게 가지만 오래 머묾
C. 자주 가고 오래 머묾
```

그리고:

- 같은 색 농도 하나
- 반복 방문 + dwell 분리 채널

두 방식의 오독률을 비교한다.

---

## 23. 이 문서가 바뀌어야 하는 신호

다음이 데이터로 확인되면 가설을 다시 본다.

- pause로 얻는 배터리 이득이 사실상 없다
- 사용자 resume 누락이 너무 커서 수동 모델이 실패한다
- 정지 GPS가 예상보다 안정적이어서 event-based dwell의 비용이 이득보다 크다
- `occupancy` 하나만으로도 반복/체류 패턴이 충분히 구별된다
- 세션을 장시간 유지하는 사용자가 거의 없어 recovery 복잡도가 가치가 없다
- 반대로 시설 체류가 매우 흔해 declared visit 같은 별도 사용자 행위가 필요하다

그때는 "이미 문서에 썼으니 유지"가 아니라 측정 결과로 이 초안을 수정한다.

---

## 24. 한 문장 요약

> **한 산책은 여러 번 끊겨도 하나일 수 있다. 하지만 지도에 칠하는 것은 오직 실제로 관측된 연속 구간뿐이다. 반복해서 지나간 흔적, 그 공간에 노출된 시간, 실제로 머문 사건은 서로 다른 의미로 보존하고 마지막 렌더 단계에서 조합한다.**
