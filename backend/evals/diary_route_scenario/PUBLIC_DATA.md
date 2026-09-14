# 실제 공공데이터를 연결한 양재 산책 실험

2026-09-14, PR #508. 인증정보 갱신 후 실제 공공 API를 다시 호출했다.
GPS·행동·메모는 기존 양재 가상 산책이며, TMAP 경로와 OSM 지도는 앞서 받은 실제 응답을 재사용했다.
이번에는 공원·상권·행정동·토지피복·기온의 실제 자료를 기존 장면 슬롯과 Gemini 작성기에 연결했다.
운영 API·DB·발행 설정과 속도 판정 정책은 변경하지 않았다.

- [전체 본문을 읽고 장면 제목까지 갱신한 결과](public-02-titles/preview.html) · [문장 원문](public-02-titles/diary.md)
- [20초 수집 결과 / 기존 제목](public-02/preview.html)
- [4초 수집 결과 / 기존 제목](public-01/preview.html)

## 실제로 받은 자료

| 공급자 | 실제 수집 |
| --- | --- |
| [전국도시공원정보표준데이터](https://www.data.go.kr/data/15012890/standard.do) | 19페이지 / 원본 18,202행. 기존 카탈로그 검증은 16,167행 채택·2,035행 제외. 정규화에 필요한 원래 행은 별도로 보존 |
| [소상공인시장진흥공단 상가·상권 API](https://www.data.go.kr/data/15012005/openapi.do) | 동선 중심 반경 2km / 12페이지 / 11,935개 업소. 자료 기준월 202606. 각 장면에서는 기존 1km 조회 범위로 다시 정규화 |
| [SGIS](https://sgis.kostat.go.kr/developer/html/openApi/api/intro.html) | 인증·좌표변환·행정동 조회. 장면에 따라 양재1동·양재2동 |
| EGIS WMS / EGIS:lv3_2025y | 각 고유 장면 좌표에서 실제 토지피복 GetFeatureInfo 조회 |
| [기상청 단기예보 조회서비스의 초단기실황](https://www.data.go.kr/data/15084084/openapi.do) | 격자 61,125 / 2026-09-14 10:00 KST / 기온 24.2℃. 장면 시각은 10:04–10:35, 8장면 모두 같은 격자·회차 |

공원 카탈로그에서 제외된 행을 유효한 공원으로 세지 않았다. 기존 정규화는 카탈로그의
보존된 원자료 필드를 별도로 읽으므로, 위 검증 통과 수가 곧 장면의 공원 근거 수는 아니다.
각 장면의 실제 채택·탈락 사유는 slots에 있다.

공공데이터·SGIS 인증정보는 사용자 파일에서 필요한 값만 읽었다.
공공 HTTP의 비밀 쿼리와 SGIS 인증 응답의 토큰은 저장하지 않았다.
public-01/public-http에 최초 카탈로그 수집과 장면 요청의 실제 응답·시각·해시가 있다.
public-02는 그 카탈로그를 검증해 재사용하고 SGIS·EGIS를 다시 조회했다.
각 weather-raw에는 기존 Life 전송이 봉투를 벗긴 실제 기상청 응답 본문과 캐시 회차가 있다.

## 같은 입력으로 비교한 결과

| 항목 | public-01 | public-02 |
| --- | ---: | ---: |
| 공간 수집 대기 상한 | 기존 4초 | 실험용 20초 |
| SGIS known | 4/8 | 8/8 |
| 상권 known | 4/8 | 8/8 |
| 공원 known | 4/8 | 8/8 |
| 토지피복 known | 3/8 | 8/8 |
| 기온 known | 8/8 | 8/8 |
| 공간 본문 generated / fallback | 4 / 4 | 8 / 0 |
| 공간·행동·제목 작업 | 8 + 1 + 1 | 8 + 1 + 1 |
| 그래프 및 사전 슬롯 준비 측정 | 16.687초 | 17.562초 |
| 기록한 공간/카탈로그 HTTP 시도 | 43 | 22 |
| 별도 기상청 실황 회차 조회 | 1 | 1 |

같은 GPS·기록·장면 정책과 같은 공원/상권 카탈로그를 사용했다.
첫 실행의 누락 사유는 collection_timeout이다. 20초 실행에서는 다섯 공급자가 모두 known이다.
known은 수집 결과이며 모델의 사용 개수가 아니다.

두 실행 모두 실제 수집 및 슬롯 준비를 기존 카드 그래프 전에 끝내고, 그대로 writer에 전달한다.
그래프 제한은 기존 15초·제목 예약 3초다. 표의 측정에는 사전 슬롯 계산이 포함된다.
공원/상권 카탈로그 최초 준비 7.031초는 별도다.
**20초 수집 설정을 운영에 적용하지 않았으며, 어느 실행도 운영 10초 발행을 통과했다는 뜻이 아니다.**
HTTP 원문 보존과 로컬 파일 입출력의 추가 비용도 있어 4초 실행을 운영 성능 측정으로 취급하지 않는다.

prepared_input 경로에서는 배경을 작성기 전에 결합하므로 result.scene_backgrounds는 null이어도
작성 입력에는 근거가 존재한다. 이 필드는 그래프 내부 수집본이며,
실제 전달 여부는 run.backgrounds_in_writing_input과 jobs의 evidence/materials로 확인한다.

## 원자료가 실제 문장에 이어졌는가

[source-lineage.json](public-02/source-lineage.json)은 각 장면의
선정 근거 → 실제 모델 입력 → 응답의 인용 ID를 대조한 결과다.
뷰어의 접힌 개발자 영역에서도 같은 연결을 볼 수 있다.

- 20초 실행은 장면마다 공간 3개·기온 1개·위치 참고 1개를 모델에 전달했다.
- 실제 인용은 행정동 8장면, 토지피복 8장면, 상권 6장면, 공원 3장면이다.
- **기온은 8장면 모두 입력됐지만 인용·본문 사용은 0장면이었다.** 자료 부족과 모델의 사용 선택을 구분해야 한다.
- 동선 슬롯은 2장면에 있으나 현재 공간/행동 작성 입력에는 연결되지 않았다.
  사용자가 정한 0.5배 미만·1.5배 초과·10초 기준과 동선/속도 통합은 이번 변경 범위 밖이다.
- 공원 이름과 실제 행정동이 등장했지만 주소·상권 설명의 반복이 크다.
- 토지피복 “숲”을 “숲이 우거진”으로 확장한 표현이 남았다. 실제 API 인용만으로 문장 의미가 검증되지는 않는다.

public-02-titles는 완성된 8개 본문·메모·출발/종료 역할을 읽어 제목만 갱신했다.
Gemini 추가 호출 1회, 4.032초이며 본문과 원본 결과는 유지했다.
6번 제목의 “상가들이 늘어선”은 본문의 “산재”보다 배치를 강하게 표현하고,
4번 제목은 되돌아가기로 한 메모를 “돌아가는 길”로 요약했다.
좋아 보이는 제목을 고르려고 재생성하거나 수동 수정하지 않았다.

## 재현

backend에서 기존 uv.lock과 .venv를 사용한다.
카탈로그 폴더는 Git 밖의 실험 전용 폴더로 지정한다. 기존 결과 폴더는 덮어쓰지 않는다.

~~~powershell
uv run --no-sync python -X utf8 tools/run_diary_public_scenario.py --env-file <설정파일> --route evals/diary_route_scenario/tmap-route.json --map evals/diary_route_scenario/yangjae-02/map.json --output evals/diary_route_scenario/public-new --catalog-dir <실험카탈로그폴더>
uv run --no-sync python -X utf8 tools/run_diary_public_scenario.py --env-file <설정파일> --route evals/diary_route_scenario/tmap-route.json --map evals/diary_route_scenario/yangjae-02/map.json --input evals/diary_route_scenario/public-new/input.json --reuse-catalogs --collection-timeout 20 --output evals/diary_route_scenario/public-new-full --catalog-dir <같은실험카탈로그폴더>
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --source-run evals/diary_route_scenario/public-new-full --output evals/diary_route_scenario/public-new-titles --env-file <설정파일>
~~~

시간을 지정하지 않으면 실행 두 시간 전의 가상 산책으로 만든다.
나중의 새 실행은 조회 회차·공급 자료가 바뀌므로 저장된 결과와 같다는 보장은 없다.

실행한 오프라인 확인:

~~~powershell
uv run --no-sync python -X utf8 tools/run_diary_route_scenario.py verify --output evals/diary_route_scenario/public-01
uv run --no-sync python -X utf8 tools/run_diary_route_scenario.py verify --output evals/diary_route_scenario/public-02
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --source-run evals/diary_route_scenario/public-02 --output evals/diary_route_scenario/public-02-titles --replay
uv run --no-sync ruff check tools/run_diary_public_scenario.py tools/run_diary_route_scenario.py
uv run --no-sync ruff format --check tools/run_diary_public_scenario.py tools/run_diary_route_scenario.py
~~~

모두 통과했다. source_lineage()로 두 결과의 연결표도 다시 계산해 저장본과 일치했다.
브라우저에서 새 본문·제목·지도 연결을 확인했고 압축 원문을 포함한 96개 산출물의 키 미포함을 확인했다.
운영 코드·저장 경계를 바꾸지 않아 pytest 전체는 실행하지 않았다.
기존 uv run check의 Windows 실행 정책 차단은 여전히 별도 제약이며 정책을 바꾸지 않았다.
