# DAENGS 백엔드

FastAPI + uv (Python 3.12).

```powershell
Copy-Item .env.example .env   # 처음 한 번
uv sync        # .venv 동기화
uv run dev     # 개발 서버 http://127.0.0.1:8000 (reload)
uv run run     # 운영 서버 http://0.0.0.0:8000
uv run pytest  # 테스트
```

`.env` 의 암호화 키 3개는 기본값이 없습니다. 비워 두면 서버가 뜨지 않습니다 —
만드는 법은 `.env.example` 에 적어 뒀습니다.

- `src/daengs_backend/main.py` — FastAPI 앱, CORS, `GET /health`
- `src/daengs_backend/config.py` — 설정. 환경 변수 접두사는 `DAENGS_`, `backend/.env` 를 읽습니다.
- `src/daengs_backend/core/crypto.py` — 개인정보 암복호화(AES-GCM) · blind index
- `src/daengs_backend/core/password.py` — 관리자 비밀번호 해시(Argon2id)
- `tests/` — pytest. 패키지 밖에 둡니다

자세한 규칙은 루트 [CLAUDE.md](../CLAUDE.md) 참고.
