# 피부 병변 스크리닝 (skin-screening)

사진 한 장 → **① 정상/이상 → ② 이상이면 병변 6종 분포**.
**진단이 아닙니다.** "이건 좀 의심되니 병원 가보세요" 까지가 목적입니다.

```
사진 → [가이드 프레임으로 크롭] → 1단계(정상/이상) ─ 낮으면 → "정상으로 보입니다"
                                                  └ 높으면 → 2단계(6종 분포) → 진료 권함
```

**이 폴더는 아직 배포에 붙어 있지 않습니다.** compose·nginx·deploy.yml 어디에도
없어서, `dev` 에 머지돼도 서버에서 아무것도 안 돕니다. 의도한 것입니다 (D-022).

---

## 띄우기

### 화면과 계약만 보기 — torch 불필요 (~30초)

```powershell
cd skin-screening
uv sync
uv run python serve.py --mock
```

* `http://127.0.0.1:8000/` — 데모 화면 (앱 챗봇 카드를 그대로 옮긴 것)
* `http://127.0.0.1:8000/docs` — FastAPI 자동 생성 API 문서

⚠️ `--mock` 의 숫자는 **사진 바이트의 해시로 만든 가짜**입니다. 응답의
`meta.mock` 이 `true` 로 옵니다. 응답 모양·재촬영 밴드 검사·화면 동선은
진짜와 같으므로 **앱을 붙여보는 데는 이걸로 충분합니다.**

### 진짜 가중치로 돌리기

```powershell
uv sync --extra model                                  # torch·timm (~2GB)
uv run python serve.py --release C:\path\to\release
```

**가중치는 이 저장소에 없습니다.** `best.pt` 한 개가 163MB / 189MB 이고
GitHub 은 파일당 100MB 가 하드 리밋입니다. 라이선스 문제는 아닙니다 —
**학습된 가중치는 약관상 자유롭게 배포할 수 있습니다** (아래 "출처 표기").
순전히 파일 크기 때문입니다.
→ **암호화 키 3개와 같은 방식으로 팀 채널로 받으세요.**

`release/` 폴더 안에 이렇게 들어 있습니다:

```
release/
  stage1_threshold.json                          ← 임계값 · 지표
  checkpoints/stage1_effnetv2_s_f320_384_moderate_photometric/best.pt
  checkpoints/stage2_resnet50_m2.5_384_moderate/best.pt
```

CPU 로 사진 한 장에 0.6~3초입니다 (실측 615ms). 데모에는 충분합니다.

### 서버에 켜기 — compose·nginx 에 이미 붙어 있습니다 (D-024)

`docker-compose.yml` 에 `skin-screening` 서비스가, `nginx/default.conf` 의
`listen 8000` 블록에 `location /screen/` 이 **이미 들어가 있습니다.**
다만 서비스가 **profile 뒤에 있어 안 뜹니다.**

앱이 부를 주소는 이렇게 됩니다 — **새 포트도 DNS 도 방화벽도 필요 없습니다.**

```
http://daengback.~/screen/v1/screen
```

가중치는 **코드와 따로** 갑니다 (`.env`·암호화 키와 같은 취급).

```
git (이 저장소)   코드만 — serve.py · src/ · demo/
서버 PC 디스크     C:\deploy\daengs\modelselease\   ← 배포 폴더 밖. 재배포해도 안 지워짐
개발 PC           필요한 사람만 팀 채널로 받아 --release 로 지정
```

켜는 순서 (서버 PC 에서):

```powershell
# 1) 가중치를 배포 폴더 **밖**에 풀어 둡니다
#    release\stage1_threshold.json  +  release\checkpoints\*est.pt

# 2) 최상단 .env
#    SCREENING_RELEASE_DIR=C:\deploy\daengs\modelselease

# 3) 켜기
docker compose --profile screening up -d

# 4) nginx 가 새 경로를 읽게 (설정 파일 내용만 바뀌면 자동 반영이 안 됩니다)
docker compose restart nginx

# 5) 확인
curl http://daengback.weareithero.cloud/screen/healthz
#    {"ok":true,"mock":false,"contract_version":"1.0",...}
```

⚠️ 4번에서 nginx 가 **몇 초 끊깁니다** (80·8000). 사람이 적은 시간에 하세요.

⚠️ **켜는 순간 인증 없는 업로드 엔드포인트가 열립니다.** `serve.py` 는 데모 서버라
인증·레이트 리밋이 없습니다. 인증을 붙이는 것은 별도 카드입니다.

⚠️ **웹 데모 화면은 서버에서 안 돕니다.** `demo/index.html` 이 오리진 루트를 부르는데
`/screen/` 아래에서는 어긋나기 때문입니다 (그 파일은 사본이라 못 고칩니다).
화면을 보려면 각자 PC 에서 `serve.py --mock` 으로 띄우세요.

첫 기동은 `uv sync --extra model` 이 torch 를 받느라 몇 분 걸립니다 (두 번째부터는
`uv-cache` 볼륨 덕에 빠릅니다). 리눅스에서는 CPU 판을 받습니다.

---

## 폴더

| 경로 | 내용 |
| --- | --- |
| `serve.py` | FastAPI 서버 + 데모 화면. HTTP 만 봅니다 |
| `demo/index.html` | 데모 UI — 앱 챗봇 카드 재현. 자족적인 파일 하나 |
| `src/agent.py` | 파이프라인 + **응답 계약**. HTTP 를 모릅니다 |
| `src/message.py` | 보호자에게 보여줄 문구. torch 를 안 씁니다 |
| `src/crop.py` | `crop_window()` — **크롭 창을 정하는 단 하나의 함수** |
| `src/infer.py` `models.py` `stages.py` `calibrate.py` `data.py` `evaluate.py` | 진짜 추론 경로 (torch) |
| `tools/box_error.py` | 사람이 그린 네모 vs 정답 bbox 오차를 교란으로 환산 |
| `tests/` | 계약 감시. `uv run python tests/test_agent.py` (87개). `test_screening_message.py` 는 `--extra model` 이 필요합니다 |
| `docs/API.md` | **응답 계약 v1.0** — 앱이 볼 문서 |

---

## 지금 모델이 어느 정도인가

숫자는 전부 실측입니다 (holdout = 학습에도 검증에도 안 쓴 개체들).

| | 값 |
| --- | ---: |
| 1단계 정상/이상 AUROC (holdout) | **0.9304** |
| 1단계 recall / 임계값 | 0.9458 / **0.1823** |
| 2단계 병변 6종 macro-F1 (holdout) | 0.5645 |
| **2단계가 고른 이름이 틀릴 확률** | **56.6%** |

**그래서 화면에 병변 이름을 띄우지 않습니다.** 1단계(이 사진이 이상한가)는 쓸 만하고,
2단계(무슨 병변인가)는 이름을 말할 수준이 아닙니다 → 분포만 보여주고
"판단할 수 없습니다 → 진료 권함" 으로 끝냅니다. 자세한 근거는 `docs/decisions.md` D-023.

⚠️ 지금 릴리즈의 2단계 백본은 `resnet50` 입니다. 비교 실험에서 이긴
`convnextv2_base` 로 재학습이 남아 있어 **가중치는 갈아끼울 예정**입니다.
계약과 화면은 안 바뀝니다.

---

---

## 출처 표기 (의무)

이 모델은 AI 허브 데이터로 학습했습니다. **약관이 출처 표기를 조건으로 겁니다** —
앱 화면 어딘가(설정 > 정보 등)에 반드시 들어가야 합니다.

> 학습 데이터: **반려동물 피부 질환 데이터** — 출처 [AI 허브](https://aihub.or.kr) (`dataSetSn=561`)

### 무엇이 되고 무엇이 안 되나

약관을 그대로 옮기면 이렇습니다.

| | |
| --- | --- |
| **학습된 모델·서비스** (`*.pt`, 이 API, 앱) | ✅ **영리·비영리 자유.** 판매·배포까지 됩니다 |
| **원본 데이터** (AI Hub zip, 라벨 JSON) | ❌ 제3자 제공·공개 금지 |
| **크롭 이미지** | ❌ "원본을 단순 가공(편집·수정)한 형태" 라 원본과 같은 취급입니다 |

**가중치는 팀에게 줘도 되고, 크롭은 안 됩니다.** 크롭이 필요한 사람은 각자
AI 허브에서 활용신청을 해서 직접 받는 것이 안전합니다.

---

## 이 폴더는 사본입니다

원본은 **[gayeoniee/deeplearning_test](https://github.com/gayeoniee/deeplearning_test)** (공개)
이고, 데이터 전처리·학습·평가 노트북이 전부 거기 있습니다. 여기 있는 건 **서빙에
필요한 부분만** 잘라온 사본입니다.

* 복사 시점 원본 커밋: **`a38e6fe`** (2026-08-28)
* 파일은 **한 글자도 안 고치고** 그대로 가져왔습니다. 폴더 루트가 원본 저장소 루트와
  같은 역할을 하도록 배치해서, 상대 경로가 그대로 맞습니다
* **고칠 일이 생기면 원본을 고치고 다시 복사하세요.** 여기서 고치면 갈라지고,
  갈라져도 아무도 모릅니다

재동기화:

```powershell
$src = "C:\path\to\deeplearning_test"
Copy-Item "$src\serve.py" . -Force
Copy-Item "$src\demo\index.html" demo\ -Force
Copy-Item "$src\tools\box_error.py" tools\ -Force

# 원본 src\ 에는 학습·전처리 모듈이 20개 더 있습니다. 통째로 복사하지 말고
# 여기 있는 파일 이름만 골라 덮어씁니다.
Get-ChildItem src\*.py   | ForEach-Object { Copy-Item "$src\src\$($_.Name)"   src\   -Force }
Get-ChildItem tests\*.py | ForEach-Object { Copy-Item "$src\tests\$($_.Name)" tests\ -Force }

uv run python tests\test_agent.py            # 계약이 안 깨졌는지
```
