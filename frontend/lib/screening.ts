/**
 * 피부 스크리닝(`daengs_screening`) 응답 계약 v1.0 의 타입.
 *
 * **백엔드와 손으로 맞춘 것입니다.** 원본은
 * `backend/src/daengs_screening/agent.py` 의 `contract()` 이고 문서는
 * `backend/src/daengs_screening/API.md` 입니다. **한쪽만 고치면 조용히
 * 어긋납니다** — 필드를 더하거나 이름을 바꿀 때는 둘을 같이 보세요.
 * 그쪽은 다시 원본 저장소(gayeoniee/deeplearning_test)의 사본이라,
 * 진짜로 고칠 곳은 원본 → `backend/tools/sync_screening.py` 입니다.
 *
 * ⚠️ **`meta` 는 전부 optional 입니다.** 이유가 둘 있습니다.
 *   ① `guide` 는 `box` 를 보냈을 때만 붙습니다 (`agent.py` 가 그때만 넣습니다).
 *   ② `retake` 가 두 종류인데, 이미지를 못 연 쪽은 `meta` 에 `error` 하나뿐이라
 *      `elapsed_ms` 조차 없습니다.
 * 필수로 두면 둘 다 런타임에서 undefined 를 읽습니다.
 */

/**
 * `normal` 정상 · `abnormal` 이상 · `retake` 다시 찍기.
 *
 * ⚠️ `retake` 는 **모델을 돌리기 전** 판단입니다 (이미지를 못 열었거나 가이드
 * 밴드 밖). 2단계가 확신이 없다고 `retake` 로 오지 않습니다 — 그건
 * `meta.stage2_low_confidence` 로 옵니다 (D-023).
 */
export type ScreenVerdict = "normal" | "abnormal" | "retake";

/** 병변 6종 중 한 줄. **병명이 아니라 병변 "형태" 입니다.** */
export type LesionRow = {
  /** A1~A6 */
  code: string;
  name_ko: string;
  name_en: string;
  prob: number;
  percent: number;
};

export type ScreenStage1 = {
  abnormal_prob: number | null;
  abnormal_percent: number | null;
  threshold: number | null;
  /**
   * 온도 보정이 걸렸는가. **하드코딩이 아니라 `T` 값에서 나옵니다** —
   * 지금은 false 지만 보정이 붙는 날 true 가 되므로, 문구를 이 값으로 가르세요.
   */
  calibrated: boolean;
};

/**
 * ★ **계열** 한 덩어리 — 6종 이름이 아니라 네 묶음 중 하나입니다.
 *
 * `null` 이면 **아무것도 그리지 않습니다.** 두 경우입니다:
 *   · 서버에서 꺼져 있음 (`DOG_SKIN_SHOW_GROUP` 미설정 — 기본값)
 *   · 확신이 문턱 아래 — **확신 없으면 말하지 않습니다**
 *
 * ⚠️ **이건 `distribution[0]` 이 아닙니다.** 여섯 개 중 하나를 고른 게 아니라
 *    네 묶음(융기·발진 / 표면 변화 / 미란·궤양 / 결절·종괴) 중 하나이고,
 *    `prob` 은 묶음 안 확률을 **더한 값**입니다. D-023 은 그대로입니다 —
 *    6종 이름은 여기 안 들어옵니다.
 *
 * ⚠️ **`text` 와 `caveat` 는 서버가 준 문장을 그대로 쓰세요.** 화면마다
 *    지어 쓰면 표현이 갈리고, 갈리면 한쪽이 단정적으로 읽힙니다.
 *
 * ⚠️ **긴급도(`URGENCY_HINT`)를 같이 붙이지 마세요.** 계열 묶음은 긴급도를
 *    높은 쪽으로 잡아서, 말한 것의 절반이 한 단계 부풀려집니다 (과잉 52.4%).
 */
export type LesionGroup = {
  /** 융기·발진 | 표면 변화 | 미란·궤양 | 결절·종괴 */
  name: string;
  /** 묶음 안 확률의 **합** (0~1). */
  prob: number;
  percent: number;
  /** `p1 × prob` — 1단계 확률까지 곱한 값. 서버가 이걸로 말할지 정합니다. */
  confidence: number;
  /** 화면에 그대로 띄울 문장. */
  text: string;
  /** 같이 띄울 단서. 빼지 마세요. */
  caveat: string;
};

export type ScreenStage2 = {
  /** false 면 분포 영역을 **통째로 그리지 않습니다.** */
  shown: boolean;
  /** 확률 내림차순. **개수가 고정이 아닙니다** (mock 6줄 / release 3줄). */
  distribution: LesionRow[];
  /** ★ 계열. **`null` 이면 안 그립니다** (기본값). */
  group?: LesionGroup | null;
};

/** 가이드 프레임 검사 결과. `box` 를 보냈을 때만 옵니다. */
export type GuideCheck = {
  ok: boolean;
  /** 보호자에게 그대로 보여줄 한국어. ok 면 빈 문자열입니다. */
  reason: string;
  width_frac: number | null;
  center_off: number | null;
};

export type ScreenMeta = {
  elapsed_ms?: number;
  mock?: boolean;
  stage1_crop?: string;
  stage2_crop?: string | null;
  stage1_temperature?: number;
  /** `user` 네모를 받아서 잘랐음 · `center` 화면 중앙으로 물러섰음. */
  box_source?: "user" | "center";
  stage2_low_confidence?: boolean;
  stage2_top_prob?: number | null;
  stage2_abstain_threshold?: number;
  guide?: GuideCheck;
  crop_note?: string;
  /** 밴드 밖이라 돌려보낼 때의 한국어 사유. */
  retake_reason?: string;
  /** 이미지를 못 열었을 때. 이 경우 meta 에 이것 하나뿐입니다. */
  error?: string;
};

export type ScreenResponse = {
  contract_version: string;
  verdict: ScreenVerdict;
  headline: string;
  /** ★ "어떤 병변인지는 이 사진만으로 판단할 수 없습니다" — **숫자보다 위**에 둡니다. */
  body: string;
  action: string;
  stage1: ScreenStage1;
  stage2: ScreenStage2;
  /** 터미널·디버그용 전문. 앱은 안 씁니다. */
  text: string;
  disclaimer: string;
  meta: ScreenMeta;
};

/** `GET /screen/healthz`. **모델을 올리지 않습니다** — `loaded:false` 가 정상입니다. */
export type ScreeningHealth = {
  ok: boolean;
  mock: boolean;
  contract_version: string;
  /** 첫 요청 때 올라옵니다. false 라고 고장난 게 아닙니다. */
  loaded: boolean;
  release_dir: string;
  threshold?: number | null;
};

/** 정규화 `[x, y, w, h]` (0~1, 원본 사진 기준). */
export type NormBox = [number, number, number, number];

/**
 * 촬영 가이드 밴드 — `agent.py` 의 `GUIDE_*` 를 **손으로 베낀 값**입니다.
 *
 * ⚠️ 이건 보내기 전에 알려주는 힌트일 뿐이고 **판정은 서버가 다시 합니다.**
 *    어긋나도 결과는 서버가 옳습니다. 서버가 밴드 밖으로 보면 모델을 돌리지
 *    않고 `verdict: "retake"` 로 돌아옵니다.
 * ⚠️ **서버는 `bw`(가로)만 봅니다.** `bh` 는 밴드 검사에 안 들어갑니다.
 * ⚠️ STEP 16 값입니다 (STEP 10 은 권장 0.34~0.56 / 허용 0.28~0.68 이었습니다).
 */
const GUIDE_RECOMMEND: [number, number] = [0.28, 0.48];
const GUIDE_ALLOW: [number, number] = [0.24, 0.56];
const GUIDE_CENTER_MAX = 0.1;

export type GuideHint = {
  level: "recommend" | "allow" | "out";
  /** 서버가 돌려보낼 것 같으면 false. */
  ok: boolean;
  reason: string;
  widthFrac: number;
  centerOff: number;
};

/** 네모가 밴드 안인가. 서버 `agent.check_guide()` 와 같은 순서로 봅니다. */
export function guideHint([x, y, w, h]: NormBox): GuideHint {
  const centerOff = Math.max(Math.abs(x + w / 2 - 0.5), Math.abs(y + h / 2 - 0.5));
  const base = { widthFrac: w, centerOff };

  if (w < GUIDE_ALLOW[0]) {
    return { ...base, level: "out", ok: false, reason: "병변이 너무 작게 잡혔습니다. 조금 더 가까이." };
  }
  if (w > GUIDE_ALLOW[1]) {
    return { ...base, level: "out", ok: false, reason: "너무 가까워서 주변 피부가 안 보입니다. 조금 더 멀리." };
  }
  if (centerOff > GUIDE_CENTER_MAX) {
    return { ...base, level: "out", ok: false, reason: "병변이 화면 가운데에서 벗어났습니다." };
  }
  if (w < GUIDE_RECOMMEND[0] || w > GUIDE_RECOMMEND[1]) {
    return { ...base, level: "allow", ok: true, reason: "허용 안이지만 권장 밖입니다." };
  }
  return { ...base, level: "recommend", ok: true, reason: "권장 안입니다." };
}

export const GUIDE_BAND_TEXT =
  `권장 가로 ${GUIDE_RECOMMEND[0]}~${GUIDE_RECOMMEND[1]} · ` +
  `허용 ${GUIDE_ALLOW[0]}~${GUIDE_ALLOW[1]} · 중심 이탈 ${GUIDE_CENTER_MAX} 이내`;
