<!--
이 PR 은 Project 3 (orgs/SAJOYO/projects/3) 의 카드 한 장입니다.
Iteration 은 3~4일이고, 그 안에서 이 PR 을 열고 끝나면 dev 로 머지합니다.
이슈는 쓰지 않습니다 — 이 PR 본문이 그 작업의 유일한 기록입니다.

담당자 / 상태 / 기간 / Iteration / Size / Priority 는 Project 필드에 있습니다.
여기에 다시 적지 마세요. 두 군데가 되면 반드시 어긋납니다.
이 본문에는 Project 필드가 담을 수 없는 것만 적습니다.

제목이 곧 보드의 카드 이름입니다: `타입: 무엇을` (예: `feat: 산책 기록 API`).
타입은 feat / fix / docs / refactor / build / chore.

Claude Code 에게:
- 작업 전에 `gh pr view --json title,body -q '.title, .body'` 로 이 본문을 먼저 읽으세요.
  Project 필드까지 봐야 하면 `gh project item-list 3 --owner SAJOYO`.
  (`gh auth refresh -h github.com -s project` 를 한 번 해 두어야 돕니다.)
- 아래 `##` 제목은 고정입니다. 제목은 두고 내용만 채우세요.
  `## 착수 절차` 는 카드를 여는 시점이 아니라 **실제로 시작할 때** 체크합니다.
  해당 없는 섹션은 `- 없음` 한 줄로 두고, 섹션 자체를 지우지는 마세요.
- 작업 중 본문이 낡으면 `gh pr edit --body-file <파일>` 로 갱신하세요.
  특히 `## 컨텍스트 메모` 는 다음 세션의 Claude 가 읽는 유일한 인수인계입니다.
- 되돌리기 번거로운 결정은 여기 말고 `docs/decisions.md` 에 적고 번호(D-0xx)만 남기세요.
- 협업 규칙(우선순위 · Iteration · PR 기준 · 회고)은 `docs/collaboration.md` 에 있습니다.
-->

## 착수 절차

<!-- 카드를 여는 시점이 아니라 **실제로 시작할 때** 밟습니다. 며칠 뒤일 수 있습니다. -->

- [ ] Project 카드 Status → **In progress**
- [ ] **이 PR 이 쓰는 브랜치로 이동합니다. 새로 파지 마세요.**
      `gh pr view <번호> --json headRefName -q .headRefName`
      새 이름으로 파면 커밋이 거기 쌓이고 PR 은 **원래의 빈 브랜치를 머지**합니다 —
      에러가 안 나고 `dev` 에는 아무것도 안 들어갑니다 (2026-08-30 `#68`, RAG-047 ⑧)
- [ ] `git fetch origin && git merge origin/dev` — 열어 둔 사이 dev 가 움직였습니다
- [ ] `RAG-` 결정 번호나 랩(`lapN`)을 쓸 카드면 **여기서 예약**합니다.
      `dev` 만 보면 부족합니다 — 남의 예약이 아직 그 사람 브랜치에만 있을 수 있습니다:
      `git fetch origin && git log --all --oneline --grep="예약"`
      예약 커밋 제목은 `chore: 착수 — RAG-0NN 예약 (#카드)` 로 고정합니다
- [ ] 이 본문을 다시 읽습니다 — 열어 둔 사이 다른 카드가 전제를 바꿨을 수 있습니다

## 무엇을 / 왜

<!-- 2~4줄. 커밋 목록 말고 의도. "왜 지금 이게 필요했는지"가 빠지면 안 됩니다. -->

## 작업 목록

<!-- 이 PR 안에서 쪼갠 단위. 영역 태그: FE / BE / DB / AI / INFRA / DOCS -->

- [ ] `BE`
- [ ] `FE`

## 컨텍스트 메모

<!--
코드를 봐도 알 수 없는 것만. 다음 사람(과 다음 세션의 Claude)이 모르면 같은 실수를 할 것들.
예)
- `POST /api/walks` 응답은 프론트 `types/walk.ts` 와 손으로 맞춰 둠. 한쪽만 고치지 말 것.
- pgvector 인덱스는 적재가 끝난 뒤에 `db/indexes.sql` 로 따로 실행.
- 견종 축은 기각하기로 함 → D-011
-->

- 없음

## 배포 영향

<!-- dev 에 머지되는 순간 self-hosted 러너가 배포합니다. 해당하는 것만 체크. -->

- [ ] 없음 — 코드만 바뀜, 추가 조치 불필요
- [ ] `nginx/default.conf` 변경 → 자동 배포에서 `nginx -t` 후 reload
- [ ] backend 의존성 변경 → 서버에서 `docker compose restart backend`
- [ ] compose / 환경 변수 변경 → 서버의 `.env` 를 손으로 갱신해야 함
- [ ] `db/init/` 변경 → **기존 볼륨에는 반영되지 않음.** 조치 방법을 아래에 적을 것
- [ ] 인덱스·마이그레이션을 손으로 실행해야 함

필요한 조치:

## 확인한 것

- [ ] 로컬에서 동작 확인 (`npm run dev` / `uv run dev`)
- [ ] 프론트를 건드렸으면 `npm run lint` 통과
- [ ] 의존성은 `uv add` / `npm install` 로 넣고 lock 파일도 커밋 (`uv.lock`, `package-lock.json`)
- [ ] `.env`·키·비밀번호가 diff 에 없고, `.env.example` 이 실제 변수와 일치

## 남은 것

<!-- 못 끝냈거나 하다 보니 새로 생긴 일. 다음 Iteration 카드가 여기서 만들어집니다. -->

- 없음
