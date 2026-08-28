# skin-screening

반려견 피부 병변 **스크리닝 보조**. 사진 한 장 → ① 정상/이상 → ② 병변 6종 분포.
**진단이 아닙니다.** 띄우는 법과 지표는 [README.md](README.md), 응답 계약은
[docs/API.md](docs/API.md).

## 명령어

```powershell
uv sync                                        # 화면·계약만 (torch 없음)
uv run python serve.py --mock                  # http://127.0.0.1:8000/
uv sync --extra model                          # torch·timm (~2GB)
uv run python serve.py --release <경로>        # 진짜 가중치
uv run python tests/test_agent.py              # 계약 감시
uv run --extra model python tests/test_screening_message.py   # 문구 감시 (torch 필요)
```

## 규칙

- **이 폴더는 사본입니다.** 원본은
  [gayeoniee/deeplearning_test](https://github.com/gayeoniee/deeplearning_test)(공개)
  이고 복사 시점 커밋은 `a38e6fe` 입니다.
  **고칠 일이 생기면 원본을 고치고 다시 복사하세요** — 여기서 고치면
  갈라지고, 갈라져도 아무도 모릅니다.
  재동기화 절차는 README 맨 아래에 있습니다.
- **`backend/` 에 넣지 않습니다** (D-022). torch 가 backend 컨테이너로 들어가면
  이미지가 몇 GB 가 되고, `backend` 는 포트도 안 열고 MVC2 계층 규칙(D-011)이
  걸려 있는 자리입니다. 여기는 그 규칙을 따르지 않습니다 — 원본 구조 그대로입니다.
- **응답에 "1등 병변" 필드를 추가하지 마세요** (D-023). 주는 순간 앱은 그걸 제일
  크게 띄웁니다 — holdout 에서 **56.6% 틀리는** 이름을요.
  `tests/test_agent.py` 가 `top1` `predicted` `diagnosis` 같은 키를 감시합니다.
- **크롭 창을 직접 계산하지 마세요.** `src/crop.py` 의 `crop_window()` 가
  크롭 창을 정하는 **단 하나의** 함수입니다. 서빙에서 "중앙 몇 %" 를 따로 계산하면
  학습과 갈라집니다 — 실제로 그렇게 갈라진 적이 있습니다. `agent.crop_for()` 처럼
  그 함수를 부르세요.
- **`f320` 과 `m2.5` 는 bbox 를 다르게 씁니다.** 1단계 `f320` 은 **중심만**
  (창 320px 고정), 2단계 `m2.5` 는 **크기**(긴 변 × 2.5)를 씁니다. 그래서 가이드
  프레임이 부정확하면 1단계는 멀쩡하고 2단계만 흔들립니다. 하나로 뭉뚱그리면 틀립니다.
- **`serve.py` 에 `from __future__ import annotations` 를 넣지 마세요.**
  `UploadFile` 이 문자열 어노테이션이 되는데 라우트가 `build_app` 안에 있어
  pydantic 이 이름을 못 찾고 **500** 으로 죽습니다 (실제로 당했습니다).
- **`Prediction.topk` 를 화면에 그대로 띄우면 안 됩니다.** 2단계 파이프라인에서 이
  값은 1단계 확률을 곱해 낮춰둔 **거절 판정용**입니다. 보호자에게 보여줄 분포는
  깎기 전 원본인 `Prediction.stage2_probs` 입니다 — 깎은 값을 띄우면 여섯 개가 다
  작아져 "다 낮네, 별 거 아닌가" 로 읽힙니다.
- **`calibrate.apply()` 는 확률을 돌려줍니다 — logits 가 아닙니다.** 거기에
  `stages.stage1_scores()` 를 걸면 softmax 가 **두 번** 먹어 전부 0.5 로 뭉개집니다.
  보정된 점수가 필요하면 **`logits / T` 를 넘기세요**.
- **두 단계가 서로 다른 백본을 쓸 수 있습니다** — 지금은 1단계 `effnetv2_s` /
  2단계 `resnet50` 입니다. 백본은 하드코딩하지 말고 **체크포인트 폴더 이름에서**
  읽습니다 (`agent.crop_tag_from_exp`, `train.model_key_from_exp`).
- **가중치(`*.pt`)와 사진은 커밋하지 않습니다.** `.gitignore` 가 막고 있습니다.
  `best.pt` 하나가 163MB / 189MB 라 GitHub 의 파일당 100MB 리밋에 걸려 push 가
  통째로 거부됩니다. **가중치 자체는 약관상 자유롭게 배포할 수 있습니다** —
  막히는 건 파일 크기입니다. 반면 **크롭 이미지는 "원본을 단순 가공한 형태" 라
  원본과 같이 제3자 제공이 금지됩니다.** 출처 표기는 README 참고.
- **이 폴더는 아직 배포에 안 붙어 있습니다.** compose·nginx·deploy.yml 어디에도
  없습니다. 붙이는 건 별도 카드입니다 — README "출시할 때" 참고.
