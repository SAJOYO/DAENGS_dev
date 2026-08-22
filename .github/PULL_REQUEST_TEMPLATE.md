## 📌 작업 내용

<!-- 무엇을, 왜 바꿨는지 한두 문단으로. 커밋 목록 나열 말고 의도를 적어 주세요. -->

## 📍 작업 영역

<!-- 해당하는 항목에 [x] -->

- [ ] FE (Frontend)
- [ ] BE (Backend)
- [ ] DB (Database)
- [ ] AI (Model/Logic)
- [ ] Infra / Deploy

## 🔗 관련 이슈

<!-- 예: Closes #12 / Refs #34 -->

## ✅ 확인한 것

- [ ] 로컬에서 동작 확인 (`npm run dev` / `uv run dev`)
- [ ] `npm run lint` 통과 (프론트를 건드린 경우)
- [ ] 의존성은 `uv add` / `npm install` 로 추가하고 lock 파일을 함께 커밋 (`uv.lock`, `package-lock.json`)
- [ ] `.env` 나 키 같은 비밀값이 diff 에 들어가지 않음
- [ ] 되돌리기 번거로운 결정이면 `docs/decisions.md` 에 기록

## ⚠️ 배포 영향

<!-- dev 에 머지되면 self-hosted 러너가 바로 배포합니다. 아래 중 해당하는 게 있으면 적어 주세요. -->

- [ ] `nginx/default.conf` 변경 → `docker compose up -d` 로 반영됨
- [ ] `docker-compose.yml` / 환경 변수 변경 → `.env.example` 도 같이 갱신했는지
- [ ] `db/init/` 변경 → **기존 볼륨에는 반영되지 않음**. 팀에 공유 필요
- [ ] backend 의존성 변경 → 서버에서 `docker compose restart backend` 필요
- [ ] 없음

## 📸 스크린샷 (선택)

<!-- UI 변경이면 before / after -->
