# DAENGS Python 백엔드

FastAPI + uv (Python 3.12).

```powershell
Copy-Item .env.example .env   # 처음 한 번
uv sync --extra place  # 전체 로컬 테스트용 .venv 동기화
uv run dev     # 개발 서버 http://127.0.0.1:8000 (reload)
uv run run     # 운영 서버 http://0.0.0.0:8000
uv run pytest  # 테스트
```

모든 팀 소유 Python 패키지는 이 프로젝트의 `src/`와 단일 `uv.lock`을 공유합니다.
코드 위치와 배포 프로세스는 같은 개념이 아닙니다. `daengs_place`와 `daengs_journey`는
여기에 설치되지만 각각 `place-search`, `journey-service` 컨테이너로 계속 실행됩니다.

| 패키지 | 역할 | 실행 단위 |
| --- | --- | --- |
| `daengs_backend` | 인증·회원·공통 API | `backend` |
| `daengs_life` | 생활비서·실시간 산책 | `backend`/crawler |
| `daengs_training` | 훈련 RAG | `backend` |
| `daengs_place` | Place 검색·적재, 중립 점령지 읽기 | `place-search` |
| `daengs_journey` | 장소 선택 뒤 이동 스냅샷 | `journey-service` |

Place 개발에는 전용 의존성을 함께 동기화합니다.

```powershell
uv sync --extra place
uv run alembic -c infra/place/alembic.ini upgrade head
uv run pytest tests/place
uv run pytest tests/journey
```

`.env` 의 암호화 키 3개는 기본값이 없습니다. 비워 두면 서버가 뜨지 않습니다 —
만드는 법은 `.env.example` 에 적어 뒀습니다.

- `src/daengs_backend/main.py` — FastAPI 앱, CORS, `GET /health`
- `src/daengs_place/main.py` — Place 검색 FastAPI 진입점
- `src/daengs_journey/main.py` — Journey FastAPI 진입점
- `infra/place/` — Place DB Alembic 설정과 리비전
- `src/daengs_backend/config.py` — 설정. 환경 변수 접두사는 `DAENGS_`, `backend/.env` 를 읽습니다.
- `src/daengs_backend/core/crypto.py` — 개인정보 암복호화(AES-GCM) · blind index
- `src/daengs_backend/core/password.py` — 관리자 비밀번호 해시(Argon2id)
- `tests/` — pytest. 패키지 밖에 두고 도메인별 하위 폴더로 나눕니다

자세한 규칙은 루트 [CLAUDE.md](../CLAUDE.md) 참고.
