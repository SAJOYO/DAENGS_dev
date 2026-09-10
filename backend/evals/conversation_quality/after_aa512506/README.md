# after 랩 v1 — `#415`

| | |
| --- | --- |
| 측정 대상 HEAD | `aa512506869d814e87db6dac8a52a7fcb9bdcc7d` |
| 실행 | 2026-09-10T22:03:59Z ~ 22:04:30Z |
| 명령 | `DAENGS_GENERAL_FALLBACK=true … collect --lap after --adapter-mode real` |
| before | `../lap_before.jsonl` (`#401`, 2026-09-10T08:56Z) |

핀 여섯이 before 와 같습니다 — `cases_sha256 d41e0d41…` · `judge_model gpt-5.4-2026-03-05` ·
judge `prompt_version 3` · `anchor_set dev`(`08af3cfc…`) · `adapter_mode real` ·
`general_fallback true`. 라우터/General 모델과 temperature(0.0 · candidates 1)도 그대로입니다.
General 프롬프트만 `v3 → v6` 이고 **그것이 측정 대상**입니다.

**판정 파일이 없습니다.** 이 실행은 `collect` + `case-report` 까지입니다 — 세 축 점수와
`compare` 집계는 OpenAI 판정이 필요하고, `#401` 이 before 판정 파일을 안 남겨서 before 도
다시 채점해야 합니다. 사람이 그 비용을 승인한 뒤에 붙입니다.

**덮어쓰지 마세요.** 프롬프트를 고쳐 다시 재면 `after_v2_<sha>/` 로 따로 남깁니다.
