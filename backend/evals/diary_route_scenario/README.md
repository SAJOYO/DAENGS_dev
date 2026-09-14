# 양재 왕복 산책: 카드 8개를 이어 읽는 실험

2026-09-14, DEV 기준 **154cc610**, PR #508. 초기 실험은 운영 코드·프롬프트·슬롯 정책을 변경하지 않았다.
후속 [LLM 입력 정규화](LLM_INPUT.md)는 현재 카드 작성 경로·프롬프트를 변경한다. 슬롯 선정 정책은 유지한다.
초기 비교에서는 실제 지리 자료 위에 가상 산책을 얹고 현재 카드 오케스트레이션으로 두 번 생성했다.
각 실행은 공간 8개·행동 1개·제목 묶음 1개, 총 10개 Gemini 작성 작업이다.
후속 scene-titles-01은 두 번째 실행의 모든 본문을 함께 읽고 장면별 제목 8개를 한 번에 갱신했다.
별도의 whole-title-01은 같은 본문에서 전체 산책 제목 하나를 생성한 부가 실험이다.
**카드 생성은 연결됐다. 그러나 현재 출력만으로 자연스러운 한 편의 산책 이야기가 된다고 보기는 어렵다.**

## 바로 보기

- **[LLM 전용 입력 정규화](LLM_INPUT.md)** · [새 입력으로 생성한 일기](public-03-normalized-titles/preview.html) · [실제 입력과 크기 비교](public-03-normalized/input-comparison.json)
- **[실제 공공자료 5종을 연결한 후속 실험](PUBLIC_DATA.md)** · [갱신한 일기](public-02-titles/preview.html)
- [전체 본문을 읽고 각 장면 제목 갱신](scene-titles-01/preview.html) · [문장 원문](scene-titles-01/diary.md)
- [자료와 슬롯을 먼저 준비한 결과](yangjae-02/preview.html) · [문장 원문](yangjae-02/diary.md)
- [첫 수집 연결 실행](yangjae-01/preview.html) · [문장 원문](yangjae-01/diary.md)
- 지도 번호·이전/다음 버튼으로 위치를 볼 수 있다. 근거·조건·모델 요청은 접힌 개발자 영역에 있다.
- HTML은 독립 파일이며 외부 지도 타일이나 API를 요청하지 않는다.

## 실제 자료와 가정

| 항목 | 이번 입력 |
| --- | --- |
| 경로 | TMAP 보행 API의 실제 경로 1,014 m / 원래 꼭짓점 75개. 귀로는 같은 선을 반대로 재생 |
| 지리 | 매헌시민의숲 주변 → 양재천로·바우뫼로 주변 → 출발점. 강변 보행로만 걷는 경로는 아님 |
| 좌표·시간 | 선형 보간한 mock GPS 190개, 10초 간격, 왕복 약 2.03 km, 31분 30초 |
| 가상 산책 시각 | 2026-09-14 08:45:15–09:16:45 KST |
| 행동·메모 | 가상의 보리 / 냄새 맡기 1개 / 되돌아가기로 한 메모와 귀로 메모 2개 |
| 움직임 | 가상 속도·밀집 구간을 기존 측정 커널에 넣어 관측 풀 계산. 관측 결과를 손으로 만들지 않음 |
| 공간 | EGIS 토지피복 실제 조회 8/8 known. 지도 분류이며 촉감·숲의 밀도·직접 밟은 지면의 현장 확인이 아님 |
| 다른 자료 | SGIS 키·공원 카탈로그 없음. 상권과 기온 조회 unavailable. 다른 자료로 메우지 않음 |
| 지도 | OpenStreetMap 실제 도형. 화면 전용이며 OSM/TMAP 장소 이름을 서술 근거로 넘기지 않음 |

위치 설명은 [서초구 시민의숲 안내](https://www.seocho.go.kr/site/seocho/04/10405010100002015062601.jsp)를 참고했다.
TMAP 응답은 [tmap-route.json](tmap-route.json)에 보존했다.
지도는 [OpenStreetMap API](https://api.openstreetmap.org/api/0.6/map?bbox=127.029,37.468,127.041,37.477)의 2026-09-14 응답에서 필요한 도형을 추렸다.
지도 데이터 © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright).
EGIS는 기존 수집기의 https://api.mcee.go.kr/geoserver/wms / EGIS:lv3_2025y를 사용했다.

## 실행을 두 개 남긴 이유

| 항목 | yangjae-01 | yangjae-02 |
| --- | --- | --- |
| 경로 | write_board → 현재 그래프 | 자료·슬롯 준비 후 write_cards → 같은 그래프 |
| 입력·선정·수집 결과 | 같은 동결 입력 | 같은 동결 입력 |
| 공간 스냅샷 | 수집됐지만 그래프가 채택하지 않음 | 그래프 실행 전에 슬롯 입력으로 준비 |
| 실제 공간 본문 | fallback 8개 | generated 8개 |
| 행동 본문 | generated 1개 | generated 1개 |
| 제목 | generated 8개 | generated 8개 |
| 모델 | gemini-3.1-flash-lite | 같은 모델·프롬프트 |
| 시간 제한 | 기본 15초, 제목 예약 3초 | 같은 제한, 자료·슬롯 준비는 그래프 제한 밖 |
| 측정 시간 | 14.688초 | 15.172초, 사전 슬롯 준비 포함 |

첫 실행의 space 작업 8개는 검증상 accepted지만 text가 빈 문자열이라 실제 카드는 fallback이다.
따라서 작업 성공 개수와 생성 본문 개수를 구분해야 한다.
첫 실행은 result.scene_backgrounds=null이고 실제 space 요청 materials도 비었다.
수집 결과를 별도로 with_scene_backgrounds에 넣는 재생은 성공했다.
**첫 실행에서 채택하지 않은 정확한 원인은 아직 특정하지 않았다.**
그래프는 수집 예외/시간 초과 결과를 최종 결과에 노출하지 않아 저장 결과만으로 둘을 구별할 수 없다.

두 번째 실행은 서비스 문제를 해결한 실행이 아니라, 준비된 근거로 문장 품질을 보는 별도 실험이다.
수집은 둘 다 별도 단계였으며 운영의 10초 확정·발행 성능을 검증한 결과가 아니다.
좋은 문장을 고르기 위한 추가 생성이나 수동 수정은 하지 않았다.

## 실제로 이어 읽었을 때

1. **장면 선정은 작동했다.** 기록 3개를 지키고 경로 체크포인트 3개, 출발·종료 2개를 합쳐 8개가 됐다.
   관측 4개는 모두 기록과 시각이 가까워 별도 장면에서 제외됐다. 계획과 사유는 각 plan.json에 있다.
2. **동선 슬롯과 동선 서술은 아직 같은 것이 아니다.** 2번에 상대 저속, 4번에 동선 밀집 근거가 남았다.
   현재 space_job은 공간·환경만 넘기므로 두 움직임은 문장에 들어가지 않았다.
   movement_observation 중심 카드도 0개다. 빠른 이동을 입력했다고 observed_fast가 반드시 만들어지지도 않는다.
3. **왕복을 읽게 하는 핵심은 메모다.** 4번의 “왔던 길로 돌아가기로 했다”, 7번의 “돌아오는 길”은 가상 사용자 원문이다.
   AI가 전체 동선을 이해해 연결한 성과로 계산하면 안 된다. 현재 그래프에는 카드들을 잇는 이야기 작성 단계가 없다.
4. **첫 장면과 마지막 장면의 제목·공간 본문이 같다.** “넓게 펼쳐진 풀밭 위를 지나가는 관측”이 반복된다.
   모든 제목에 “관측”이 붙고 본문 시제도 섞인다. 개별 생성 성공만으로 연속 읽기의 품질은 보장되지 않았다.
5. **근거보다 구체적인 표현이 나왔다.** 활엽수림에 “울창한”, 기타초지에 “부드러운”, 도로에 “주변을 둘러봅니다”가 붙었다.
   공간 프롬프트에는 이미 행동·신체·시야를 쓰지 말라는 조건이 있지만 실제 출력은 경계를 지키지 못했다.
   근거 ID 반환만으로 문장 의미까지 검증됐다고 보면 안 된다.

## 모든 장면을 읽은 뒤 장면별 제목 갱신: scene-titles-01

완성된 본문 8개와 보호자 메모, 시각·순서, 기존 선정기의 출발/종료 역할을 하나의 요청에 넣었다.
Gemini는 전체 흐름을 읽고 **각 장면의 제목 8개**를 반환한다. 장면을 따로 호출하거나 본문을 다시 쓰지 않는다.
예전 제목과 뷰어의 사람이 붙인 제목은 입력에서 제외했다.

| 장면 | 실제 새 제목 |
| --- | --- |
| 1 | 넓은 풀밭에서 시작하는 산책 |
| 2 | 길을 따라 냄새를 맡으며 걷는 보리 |
| 3 | 울창한 숲길을 지나며 |
| 4 | 풀밭에서 물을 마시고 돌아갈 준비 |
| 5 | 주변을 둘러보며 걷는 길 |
| 6 | 길을 따라 이어지는 발걸음 |
| 7 | 풀밭을 지나며 남기는 기록 |
| 8 | 풀밭을 지나며 마치는 산책 |

Gemini gemini-3.1-flash-lite, temperature 0, 후보 1개, 추가 호출 1회, 측정 3.438초다.
[요청과 설정](scene-titles-01/request.json), [원응답](scene-titles-01/raw-response.json),
[채택 결과](scene-titles-01/scene-titles.json)에 입력과 출력 전체를 남겼다.
같은 장면 ID·순서를 빠짐없이 한 번씩 반환했는지 확인하고 뷰어의 제목만 갱신했다.
기존 yangjae-02 결과와 본문은 보존하며 갱신 전 제목은 접힌 개발자 영역에서 비교할 수 있다.

제목을 이어 읽으면 시작·중간 기록·마무리가 더 구별된다. 이 차이는 전체 본문을 읽은 효과뿐 아니라
새 프롬프트, 원문 메모와 출발/종료 역할을 포함한 입력 변화가 함께 만든 결과다.
출발/종료를 AI가 전체 동선에서 추론한 성과로 계산하면 안 된다.
또한 “울창한”, “주변을 둘러보며”처럼 이전 본문의 근거 밖 표현이 제목에도 이어졌다.
이 단계는 제목 편집이며 원자료에 대한 재검증은 아니다. 동선·속도 통합이나 운영 마감 연결은 아직 하지 않았다.

~~~powershell
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --source-run evals/diary_route_scenario/yangjae-02 --output evals/diary_route_scenario/scene-titles-01 --replay
~~~

--replay는 LLM 없이 응답 형식·장면 ID/순서·본문 입력·원본 결과 해시를 확인한 뒤 화면을 다시 조립한다.
새 생성은 --replay를 빼고 --env-file <provider-env-path>와 새 출력 폴더를 지정한다.
기본 범위는 scenes이며 운영 카드 작성기의 제목 단계는 변경하지 않았다.

## 별도 전체 제목 실험: whole-title-01

결과는 **“풀밭과 숲길을 따라 보리와 함께한 산책”**이다.
yangjae-02의 완성된 8개 본문과 보호자 메모를 시간순으로 읽히고 전체 산책 제목 하나를 생성했다.
기존 장면별 제목과 사람이 붙인 뷰어 제목은 요청에서 제외했다. 본문·순서·기존 결과 파일은 바꾸지 않았다.
화면에서는 장면별 생성 제목 대신 장면 번호를 표시한다.

기존 그래프도 본문 생성 뒤 제목을 요청했다. 이번 차이는 호출 순서를 뒤집는 것이 아니라,
각 카드를 독립적으로 요약하던 작업을 **완성된 일기 전체의 제목 하나를 만드는 작업**으로 바꾼 것이다.
실험용 도구에만 새 프롬프트를 두었으며 운영 제목 작성기는 변경하지 않았다.

- Gemini gemini-3.1-flash-lite, temperature 0, 후보 1개, 추가 호출 1회, 측정 1.5초.
- [요청과 설정](whole-title-01/request.json), [실제 응답](whole-title-01/raw-response.json), [채택 결과](whole-title-01/whole-title.json)을 보존했다.
- 제목이 산책 전체를 대표하는 형태로 읽히지만, 메모에 있는 물 마시기·귀로의 특징은 담지 않았다.
- 새 프롬프트는 “관측”을 금지하지 않았다. 다만 출력 범위·프롬프트·메모 포함 여부가 함께 바뀌었으므로,
  제목 표현의 변화가 전체 장면을 읽은 효과만이라고 단정할 수 없다.
- 기존 본문의 반복과 근거 밖 표현은 그대로다. 동선·속도를 합친 서술 입력은 아직 구현하지 않았다.

재생은 저장된 제목과 입력 본문·순서의 결속 및 원래 결과의 해시를 확인한 뒤 HTML을 조립한다.
LLM을 다시 호출하지 않는다.

~~~powershell
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --scope walk --source-run evals/diary_route_scenario/yangjae-02 --output evals/diary_route_scenario/whole-title-01 --replay
~~~

새 실험은 다른 출력 폴더와 키 파일을 지정한다. 기존 채택 결과가 있으면 덮어쓰지 않는다.

~~~powershell
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --scope walk --source-run evals/diary_route_scenario/yangjae-02 --output evals/diary_route_scenario/whole-title-new --env-file <provider-env-path>
~~~

## 다음 논의의 출발점

이 8개를 기준으로 **출발/중간/종료의 서술 역할, 같은 공간 재등장, 공간 분류가 허용하는 표현 범위**를 정하자.
출발·종료는 이미 코드가 가진 사실이다. 왕복/재방문은 좌표·시간 관계로 따로 판정해야 하며 LLM에게 추측시키지 않는다.
선택된 장면의 동선 슬롯을 실제 문장에 사용할지도 명시적으로 결정할 필요가 있다.
슬롯 선정은 룰로 유지하면서 검증된 관계만 작성 입력에 더하는 작은 비교가 가능하다.
이 PR은 운영 정책이나 프롬프트를 바꾸지 않고 비교할 결과와 실험 도구를 남기는 범위다.

## 재현

backend에서 기존 uv.lock을 사용한다.

~~~powershell
uv sync --frozen
uv run --no-sync python tools/run_diary_route_scenario.py verify --output evals/diary_route_scenario/yangjae-01
uv run --no-sync python tools/run_diary_route_scenario.py verify --output evals/diary_route_scenario/yangjae-02
uv run --no-sync python tools/run_diary_route_scenario.py render --output evals/diary_route_scenario/yangjae-02
~~~

verify는 원문·장면·관측·슬롯 일치를 재생하며 DB·공급자·LLM을 호출하지 않는다.
render는 저장된 결과만 HTML로 조립한다. 기준 코드가 바뀌어 정책 해시가 달라지면 verify가 실패할 수 있다.
과거 결과를 바꾸지 말고 기준 커밋에서 확인한다. 새 자료·문장은 **새 출력 폴더**에 받는다.

~~~powershell
uv run --no-sync python tools/run_diary_route_scenario.py acquire --route evals/diary_route_scenario/tmap-route.json --env-file <provider-env-path> --output evals/diary_route_scenario/new-run
uv run --no-sync python tools/run_diary_route_scenario.py generate --prepared-input --env-file <provider-env-path> --output evals/diary_route_scenario/new-run
~~~

--prepared-input을 생략하면 첫 실행처럼 스냅샷을 그래프에 전달한다.
같은 경로이면 기존 map.json을 복사한 뒤 render한다. 다른 경로의 지도는 그 범위의 자료를 준비한다.
키 파일에서 Gemini/공공데이터 키만 읽고 파일·키를 복사하지 않는다. DB/Redis 주소는 실험 프로세스에서 격리한다.
실제 기온은 최근 가상 시각을 조회하므로 나중에 다시 실행하면 상태·값이 달라질 수 있다.

긴 EGIS 형상 배열 때문에 큰 JSON은 **내용 손실 없는 .json.gz**로 보관했다. 도구는 두 형식을 읽는다.
화면에서는 긴 진단 배열만 항목 수·해시로 축약하며 실제 작성 입력과 원본 파일에는 적용하지 않는다.
스냅샷은 장면별 계산 결과이며 장면 간 영속 TTL 슬롯·교체 이력을 뜻하지 않는다.

## 확인 범위

- 두 결과의 GPS 재생, 장면·원자료 버전 결속, 원문/관측 핵심 보존, 슬롯 재생 확인.
- 새 Python 도구 Ruff 통과. 브라우저의 지도·카드·다음 장면 전환·접힌 개발자 영역 확인.
- whole-title-01 오프라인 재생, 제목 도구와 렌더 도구 Ruff check/format --check 통과.
  브라우저에서 전체 제목과 장면 번호 표시, 기존 본문·메모 유지 확인.
- scene-titles-01 오프라인 재생, 변경 Python 도구 Ruff check/format --check 통과.
  브라우저에서 장면 8개의 갱신 제목·지도 선택 제목·기존 본문·메모 유지 확인.
- 전체 pytest 미실행: 운영 코드 변경 없는 도구·결과 추가이며 검사를 재생 경계로 제한했다.
- uv run check는 마이그레이션 이름/짝 검사 후 Windows 검사에서 실패했다.
  현재 PC의 PowerShell 정책이 기존 validate.ps1을 차단했다. 정책이나 관련 코드를 바꾸지 않았다.
