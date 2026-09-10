# 산책 공공자료 운영 연결

공공자료는 사용자 행동·글의 배경으로 기존 일기 생성기에 들어간다. 이 단위는
도곡동 중심 **37.4878, 127.052 / 반경 1,200m** 한 곳의 운영 연결이다.
여러 지역 자동 선택·자동 갱신·과거 기록 backfill은 포함하지 않는다.

## 실행 구조

- 웹은 기존 DB outbox에 예약한다. `walk-context-worker`가 `walk-entry-context` 큐만 처리한다.
- `walk-context-beat` 하나가 기존 30초 스케줄을 보낸다. 크롤러 큐와 Beat는 공유하지 않는다.
- 웹·워커·관리 CLI는 같은 서버 설정 파일과 `walk-public-catalogs` 볼륨을 읽는다.
  웹·워커는 읽기 전용, 명시적 CLI만 캐시를 갱신한다.
- 각 프로세스의 venv는 분리한다. 기존 backend의 `--group ml` 및 ML 볼륨을 바꾸지 않는다.
- 두 데몬은 `walk-diary` 프로파일로 명시적으로 시작한다. 일반 배포는 두 데몬이 이미
  실행 중일 때에만 재생성한다. 운영자가 정지한 런타임을 배포가 다시 켜지 않는다.
- 활성화 전 캐시 유효기간·해시·해당 좌표의 검색 범위, 키 존재, 실제 SGIS 응답,
  DB 스키마, Redis를 검사한다. DB는 기존 `verify_*.sql`을 **read-only transaction**에서 실행한다.
  기존 v2/사진 flag가 켜졌으면 그 verifier도 포함한다. 실패 시 키·오류 원문을 출력하지 않는다.
- 데몬 재시작에서는 설정·DB·Redis만 검사한다. 캐시가 만료되어도 기존 수집기가
  `unavailable`로 끝낼 수 있어야 하므로 캐시 만료 때문에 큐 전체를 정지시키지 않는다.

## 최초 준비 — 서버에서만

1. 실행 중인 backend와 DB를 확인한다. 진단은 `DB 마이그레이션 적용` workflow의
   `inspect_walk=true`로 수행한다. 별도 하위 checkout에서 도구만 받아 실행하므로 서버의
   `backend/src`를 교체하지 않는다. 이 모드는 migration 입력을 모두 무시한다.
2. [마이그레이션 절차](../../db/migrations/README.md)에 따라 백업 후 빠진 SQL만 순서대로 적용한다.
   아래 순서는 기존 산책/entries/storyboards가 있는 서버의 추가 전제다.

   | 순서 | SQL | 조건 |
   | --- | --- | --- |
   | 1 | `2026-09-08_walk_entry_contexts.sql` | 수집 작업·봉투·삭제 트리거 |
   | 2 | `2026-09-09_walk_entry_pins.sql` | v2 핀을 사용하는 경우, 이미 적용됐으면 verify |
   | 3 | `2026-09-09_walk_photo_manifests.sql` | 사진 메타데이터 기능을 사용하는 경우 |
   | 4 | `2026-09-09_walk_public_context.sql` | 다섯 태그 |
   | 5 | `2026-09-09_walk_public_context_commerce.sql` | 최종 여섯 태그 |

   각 파일 적용 후 짝 verifier를 실행한다. 최종 상태에서는 commerce verifier를 사용한다.
   5를 적용한 DB에 4를 다시 적용하면 태그 제약이 좁아지므로 순서를 뒤집지 않는다.
3. 운영 연결 코드가 검증·배포된 뒤 `backend/.env.walk-public.example`을 서버 checkout 밖
   `C:/deploy/daengs/walk-public.env` 등에 복사한다. 실제 SGIS 키/secret과 공공데이터 키를 채운다.
   **Gemini 키는 기존 `backend/.env` 설정을 유지**한다. 관리 도구는 웹 재생성 시 이 값을 보존한다.
   Start는 v2 읽기·쓰기와 사진 메타데이터도 함께 활성화하므로 2·3의 SQL이 필요하다.
4. 서버 최상단 `.env`에 `WALK_PUBLIC_ENV_FILE=C:/deploy/daengs/walk-public.env`를 지정한다.
   기본값은 무시되는 `backend/.env.walk-public.local`이다. 예제의 public/area/context/diary
   flag는 준비 중에는 false로 둔다. Compose의 `COMPOSE_PROFILES`에 프로파일을 상시 추가하지 않는다.
5. 서버 배포 폴더에서 아래를 실행한다. 전국 공원, 해당 지역 상가, EGIS 하천을 순차 준비한다.

```powershell
.\tools\walk-diary-runtime.ps1 -Action Prepare -Latitude 37.4878 -Longitude 127.052 -Radius 1200
.\tools\walk-diary-runtime.ps1 -Action Check -Latitude 37.4878 -Longitude 127.052
```

`Prepare`는 캐시별로 완성된 파일만 원자적으로 교체한다. 세 캐시의 동시 교체는 아니므로
하나라도 실패하면 Check/Start까지 진행하지 않는다. 기존 유효 캐시는 유지한다.
Check는 공공자료 및 스키마 검증이며, 사용자 기록이나 Gemini 요청을 생성하지 않는다.
상가 검색 원 125m·하천 검색 원 250m가 카탈로그 영역에 모두 포함되어야 한다.
따라서 중심의 검사 통과가 반경 1,200m 안의 모든 좌표에서 검색 가능하다는 뜻은 아니다.

## 활성화·원격 실행

준비가 끝나면 Start를 실행한다. 공공자료 파일의 context/public/area/diary 및 v2 읽기·쓰기,
사진 메타데이터 flag를 true로 설정한 뒤 검사한다. 검사 실패 시 이전 설정 파일로 되돌린다.
실행 중인 웹의 환경은 파일 수정만으로 바뀌지 않는다. Start가 먼저 별도 CLI로 검사하고,
워커 응답을 확인한 뒤 Beat와 웹 컨테이너를 올린다. 모든 명령은 `--no-deps`로 대상만 변경한다.

```powershell
.\tools\walk-diary-runtime.ps1 -Action Start
```

원격에서는 `Walk diary runtime` workflow의 Configure/Prepare/Check/Start/Stop을 사용한다.
Configure는 저장소 secrets `WALK_SGIS_KEY`, `WALK_SGIS_SECRET`, `WALK_PUBLIC_DATA_KEY`를
서버 전용 파일에 기록하고 최상단 `.env`에 경로를 지정한다. 파일이나 경로 설정이 이미 있으면
덮어쓰지 않고 중단한다. 키를 코드·앱·로그에 넣지 않으며 초기 기능 flag는 false다.
**workflow를 선택한 커밋과 서버 checkout HEAD가 같아야 한다.** workflow는 서버 소스를
checkout하지 않으며, 기존 배포 폴더의 도구와 Compose를 실행한다.
DB migration이나 앱 로그인 계정 생성은 이 도구가 대신하지 않는다.

## 새 산책 한 사이클 확인

대상 지역에서 본인 소유의 새 산책에 확정 위치가 있는 행동·글을 남긴 뒤 종료한다.
그 산책의 인증된 API로 context를 조회해 주소·공원·상권·하천 상태를 확인한다.
`completed`만 보지 말고 봉투의 `known/partial/empty/unavailable`과 이유를 구분한다.
생성 API를 한 번 호출하고 `walk-diary-bundle-v1`으로 같은 산책을 읽는다.
주소와 근거 있는 배경이 사용자 기록과 합쳐진 한 장면으로 나오는지, 반복 조회가
추가 생성하지 않는지 확인한다. 운영자 진단 로그에는 기록 본문·사용자 ID·키를 남기지 않는다.

## 중지와 복구

`Stop`은 **큐 처리 일시 중지**다. 웹은 그대로여서 예약이 계속 쌓일 수 있다.

```powershell
.\tools\walk-diary-runtime.ps1 -Action Stop
```

공공자료 예약까지 끄려면 서버 설정 파일의 context/public/area flag를 false로 바꾸고,
기존 Gemini 키를 셸에 보존한 상태에서 `docker compose up -d --no-deps backend`로 반영한다.
diary/v2 읽기 flag는 저장된 신규 형식이 있을 수 있으므로 장애 범위에 따라 별도로 판단한다.
DB 테이블·트리거·기존 일기·카탈로그 볼륨은 삭제하지 않는다. `docker compose down -v`는 쓰지 않는다.
다시 켤 때는 Check 후 Start를 실행한다. 실패한 Start는 부분 기동될 수 있으므로 실행 중인
전용 데몬과 웹의 실제 설정을 확인한다. 과거 완료 작업은 Start나 캐시 갱신으로 다시 열리지 않는다.

카탈로그는 30일이 지나면 사용할 수 없다. 자동 갱신은 후속 작업이므로 만료 전에 Prepare/Check를
명시적으로 실행해야 한다. 오류 본문 대신 안전한 상태와 카탈로그 메타데이터로 점검한다.

## 이번 적용 결과

2026-09-10 서버 러너에서 확인했다. 첫 점검은 Docker 엔진 연결 실패였고,
재점검에서는 엔진과 기존 컨테이너가 실행 중이었다. 초기에는 모든 산책 확장 flag가 false,
공공자료 키·캐시는 미설정, context/photo 테이블은 미적용 상태였다.

`C:/deploy/daengs/db-backups/vectordb-20260910-095136.dump`(54.4MB) 백업 후
context·photo·public·commerce 네 migration과 짝 verifier를 순서대로 적용했다.
[context](https://github.com/SAJOYO/DAENGS_dev/actions/runs/34423047178),
[photo](https://github.com/SAJOYO/DAENGS_dev/actions/runs/34423220250),
[public](https://github.com/SAJOYO/DAENGS_dev/actions/runs/34423365296),
[commerce](https://github.com/SAJOYO/DAENGS_dev/actions/runs/34423548826) 모두 성공했다.
세 public-source 키는 서버 워크플로 secrets에 등록했다. 이 시점에는 아직 기능을 켜지 않았다.

로컬 수집·사전 점검 31개, 별도 localhost PostgreSQL의 read-only verifier 2개,
PowerShell 동작·Compose 렌더·Configure 검사 7개를 통과했다.
`uv run check`는 Windows 검사를 실행하는 자식 프로세스에만 실행 정책을 지정해 통과했다.
전역 실행 정책을 변경하지 않았다. Linux 컨테이너의 기본 CI 테스트는 배포 전에 별도 확인한다.
