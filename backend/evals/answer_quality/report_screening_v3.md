# 판정 계층 — D17 뒤 같은 28문항 재수집 (v4) · 경계 라벨이 걷힌 뒤의 수치

**카드 #337 (2026-09-08) · RAG-078.** `report_screening_v2.md` 의 ④(v3, #330)를 잇습니다. v3 과 v4 사이에
Life 쪽에서 바뀐 것은 **`generate.PROMPT` 의 `boundary` 규칙 하나**(VERSION 3 → 4)입니다 — 코퍼스 9,838 · 임베딩
`qwen3-embedding-0.6b` · 생성 `gemini-3.1-flash-lite` · 질문 파일(sha `39ce3c95…`) 그대로. v2 · v3 표는 지우지
않습니다. 정정 사슬입니다.

⚠ 라우터는 `semantic-router-ko-v9 → v10` 으로 움직였습니다(#323 · D-061 이 dev 에 먼저 들어갔습니다).
`record_diff` 의 `plan` 칸이 다섯 조건 전부 0 이라 24문항의 라우팅 계획은 같고, Life 는 같은 질문·같은 판정
컨텍스트를 받으므로 **Life 축의 비교는 섭니다.**

## 결론 셋

1. **Life 거절 15 → 0.** 다섯 조건 어디에도 `REFUSED` 가 없습니다. `B4`~`B6`(골든셋, lap31)은 여전히 3/3
   거절이라 규칙이 경계를 없앤 것이 아니라 **묻는 것으로 옮긴 것**입니다.
2. **거절 → 답 12건의 인용 집합이 v3 의 거절 인용과 전부 같습니다.** 답은 같았고 라벨만 달랐다는 RAG-077 의
   판독이 라벨 쪽에서 확인됐습니다. 조항 집합이 갈리는 사례는 여전히 0 입니다.
3. **남은 것은 `covered` 축입니다.** `casual_03`·`noisy_02` 가 판정 블록 아래에서 `no_evidence` 기권 — #330 이
   "판정 블록이 `covered` 판단까지 흔든다" 고 적어 둔 자리가, 거절이 걷히자 기권으로 보입니다.

## ① Life 상태 — 조건별 (multi_intent 제외 24문항)

| | `off` | `off_ctl` | `abn3` | `abn200` | `normal3` |
| --- | --- | --- | --- | --- | --- |
| v3 REFUSED | 4 | 4 | 6 | 2 | 1 |
| **v4 REFUSED** | **0** | **0** | **0** | **0** | **0** |
| v4 OK | 21 | 21 | 20 | 20 | 21 |
| v4 ABSTAINED (`no_evidence`) | 3 | 3 | 4 | 4 | 3 |

## ② D17 대상 7문항 — `d17_targets.json` 의 기대와 대조

| 대상 (태그) | `off` | `off_ctl` | `abn3` | `abn200` | `normal3` | 기대 (a)/(b) |
| --- | --- | --- | --- | --- | --- | --- |
| `noisy_03` (판정 블록이 붙으면 emergency) | REFUSED/medical → **OK** | 같음 | REFUSED/emergency → **OK** | 같음 | 같음 | (a) ✅ |
| `polite_02` (off 에서만 medical) | REFUSED → **OK** | REFUSED → **OK** | OK | OK | OK | (a) ✅ |
| `noisy_01` (abn3 에서만 medical) | OK | OK | REFUSED → **OK** | OK | OK | (a) ✅ |
| `no_location_01` (abn3 에서만 medical) | OK | OK | REFUSED → **OK** | OK | OK | (a) ✅ |
| `casual_03` (거절 ↔ 기권) | REFUSED → **OK** | REFUSED → **OK** | REFUSED/emergency → **ABSTAINED** | ABSTAINED | ABSTAINED | (b) — 거절이 기권으로 |
| `noisy_02` (거절 ↔ 기권) | REFUSED → **ABSTAINED** | REFUSED → **ABSTAINED** | REFUSED → **ABSTAINED** | ABSTAINED | OK | (b) — 같음 |
| `abbrev_typo_04` (기권 ↔ 답) | ABSTAINED | ABSTAINED | ABSTAINED → **OK** | ABSTAINED → **OK** | OK | (b) — 답으로 |

(a) 넷은 전부 `none` 으로 돌아왔습니다. (b) 셋은 **거절 ↔ 기권의 갈림이 기권 ↔ 답의 갈림으로 바뀌었을 뿐
설명되지 않았습니다** — `boundary` 가 아니라 `covered` 가 갈리는 자리입니다 (결론 3).

대상 밖에서 움직인 것: `abbrev_typo_03` 이 `off`·`off_ctl` 에서 ABSTAINED → OK ("가입 전 증상" 을 묻는 문항 —
골든셋 `B8` 과 같은 자리). `multi_intent` 4문항도 거절 3건이 답/기권으로 갔습니다(비교 밖, 관찰용).

## ③ 인용 — 갈리는 조항은 여전히 0

`cited_diff.compare` 의 `comparable` / `identical` / `different`:

| 비교 | v3 | **v4** | v4 `different` 의 정체 |
| --- | --- | --- | --- |
| `off` 대 `off_ctl` | 21 / 21 | **21 / 21** | — |
| `off` 대 `abn3` | 21 / 21 | **22 / 19** | `casual_03`(답 5 ↔ 기권 0) · `abbrev_typo_03`(답 5 ↔ 기권 0) · `abbrev_typo_04`(기권 0 ↔ 답 5) |
| `abn3` 대 `abn200` | 21 / 19 | **20 / 20** | — |
| `abn3` 대 `normal3` | 22 / 20 | **21 / 20** | `noisy_02`(기권 0 ↔ 답 5) |
| `off` 대 `normal3` | 22 / 20 | **23 / 19** | 위 넷 |

`different` 는 **전부 한쪽이 기권(인용 0)** 인 자리입니다. 양쪽에 인용이 있는데 조항이 갈린 사례는 v2 · v3 ·
v4 를 통틀어 0 입니다.

**거절 → 답 레코드의 인용 집합** — v3 의 거절은 RAG-077 로 `data.citations` 를 실었으므로 v4 의 답과 집합으로
맞댈 수 있습니다. 24문항 안 10건 + `multi_intent` 2건, **12/12 같은 집합**입니다.

## ④ 레코드별 기계 대조 — v3 ↔ v4 (`record_diff`, multi_intent 제외)

| 칸 | `off` | `off_ctl` | `abn3` | `abn200` | `normal3` |
| --- | --- | --- | --- | --- | --- |
| `plan` · `capabilities` | 0 | 0 | 0 | 0 | 0 |
| `top_status` = `life_status` = `life_code` | 5 | 5 | 6 | 2 | 1 |
| `life_text` | **24** | **24** | **24** | **24** | **24** |
| `life_citations` (OK↔OK) | 0 | 0 | 0 | 0 | 0 |

**본문은 24/24 전부 다릅니다.** 온도 0 이어도 프롬프트가 바뀌면 문장이 바뀝니다 — v4 안의 대조쌍
`off`↔`off_ctl` 은 그대로 붙습니다(①·③). #337 본문이 *"본문·인용은 안 바뀌어야 한다"* 고 적은 것은 **인용에
대해서만 맞았고 본문에 대해서는 과했습니다.** 그래서 `--assert-life` 는 이 비교에 쓰지 않았고, v3 ↔ v4 는
**상태·인용으로만** 읽습니다.

진료 권함 한 문장은 답한 대상 문항의 끝에 붙었습니다 — **`casual_03`(`off`) 하나 예외.** 그 답은 v3 거절과 같은
일반 조항(시설 조사·개인정보)을 인용하며 "약관을 확인하시기 바랍니다" 로 끝납니다. 답의 질이 낮은 자리이지
라벨의 문제가 아닙니다.

## 실측 — 시간

| 단계 | 시간 |
| --- | --- |
| 수집 ×5 (28문항, 온도 0) | **약 12.5분** — 첫 회 4:18(모델 적재 포함) · 이후 회당 약 2:00 |
| 비교 | 수초 |

## 재현

`#330` 본문 C 절과 같은 명령, 라벨만 `v4_*`. `POSTGRES_IP` 를 셸에 올리고(`report_screening_v2.md` 함정 1)
`uv sync --group ml` 이 돼 있어야 합니다. `dev_source_sha` 는 `055e3cc`, `questions_sha256` 은 `39ce3c95…`.

## 남은 것

- **`covered` 축** — `casual_03`·`noisy_02` 가 판정 블록 아래에서 기권, `abbrev_typo_03` 은 반대 방향. 경계
  라벨이 아니라 `covered` 의 일이라 이 카드 범위 밖입니다 (RAG-078 ④)
- **가입 전 발병 면책 조항이 검색에 안 잡힙니다** — `abbrev_typo_03` · 골든셋 `B8`. 질의는 알러지·탈모를
  말하고 조항은 "보험개시일 이전에 이미 발병" 이라 겹치는 낱말이 없습니다. `D11` 모양이지만 근거 2건입니다
- `casual_03` 의 낮은 답(일반 조항 인용) — 라벨은 맞고 답이 틀린 자리. 표본 1
- 거절 문장 지시 불이행(RAG-077 ④ 둘째)은 사람이 이 카드에서 뺐습니다. 거절이 0 이라 여기서는 잴 표본도
  없고, 골든셋 `B4`~`B6` 의 거절문이 그 표본입니다
