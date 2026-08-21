# DAENGS 백엔드

FastAPI + uv (Python 3.12).

```powershell
uv sync        # .venv 동기화
uv run dev     # 개발 서버 http://127.0.0.1:8000 (reload)
uv run run     # 운영 서버 http://0.0.0.0:8000
```

- `src/daengs_backend/main.py` — FastAPI 앱, CORS, `GET /health`
- `src/daengs_backend/config.py` — 설정. 환경 변수 접두사는 `DAENGS_`, `.env` 도 읽습니다.

자세한 규칙은 루트 [CLAUDE.md](../CLAUDE.md) 참고.
