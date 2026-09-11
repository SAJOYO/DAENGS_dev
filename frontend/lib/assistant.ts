/**
 * 공개 진입점 `POST /assistant/query` 의 요청·응답 타입.
 *
 * **백엔드와 손으로 맞춘 것입니다.** 원본이 두 곳입니다 —
 * 요청은 `backend/src/daengs_backend/schemas/assistant.py`,
 * 응답은 `backend/src/daengs_backend/orchestration/contracts.py` 이고
 * 계약 문서는 `docs/orchestration/contracts.md` §5(응답) · §8(요청)입니다.
 * **한쪽만 고치면 조용히 어긋납니다** — 필드를 더하거나 이름을 바꿀 때는 셋을 같이 보세요.
 *
 * ⚠️ **`route` 는 아무에게나 오지 않습니다.** 라우터 종류·모델 이름·프롬프트 버전은
 * `search:inspect` 권한을 가진 관리자에게만 실립니다 (#238). 앱 회원과 권한 없는 관리자에게는
 * **키는 있고 값이 `null`** 입니다 — 없는 것이 정상이라 화면이 그것을 오류로 그리면 안 됩니다.
 * 나머지 필드는 예나 지금이나 모두에게 같습니다.
 *
 * 이 파일은 `life-rag.ts` 와 층이 다릅니다 — 저쪽은 Life 의 **직접** API 두 개고,
 * 여기는 그 위에서 능력을 고르는 오케스트레이션의 경계입니다.
 */

// ------------------------------------------------------------------- 요청

/** `LocationIn` — 남한 좌표 범위(위도 33~39 · 경도 124~132)는 서버가 422 로 막습니다. */
export type AssistantLocation = { lat: number; lon: number };

/**
 * `AssistantQueryRequest`. **서버가 `extra="forbid"` 라 여기 없는 필드는 전부 422** 입니다.
 *
 * `source` · `action` · `active_dog_id` 는 승인된 라우팅 메타데이터 키 그 자체입니다
 * (`orchestration/semantic.py` `_ROUTING_METADATA_KEYS`). 임의의 `context` 딕셔너리는
 * 받지 않습니다 — 구조화 컨텍스트는 서버가 이 필드들로만 조립합니다.
 */
export type AssistantQueryRequest = {
  query: string;
  /**
   * **라우팅 신호일 뿐 인가가 아닙니다** (D-036, 불변식 12).
   * `resolve_deterministic_route` 가 아는 값이면 결정론적으로 그 능력이 잡히고,
   * 모르는 값이면 실패가 아니라 **의미 라우팅으로 그대로 넘어갑니다.**
   */
  requested_capability?: string;
  source?: string;
  action?: string;
  /** 소유권 증명이 아니라 라우팅·개인화 힌트입니다 (O-4). */
  active_dog_id?: string;
  location?: AssistantLocation;
};

/**
 * `requested_capability` 가 결정론적으로 풀리는 값 전부 (`planner.py`).
 * 앞의 넷은 실행(`execute`), 뒤의 둘은 순수 핸드오프입니다.
 */
export const RESOLVED_CAPABILITIES = [
  "training",
  "life",
  "walk",
  "place",
  "skin",
  "gait",
] as const;

// ------------------------------------------------------------------- 응답

/**
 * 실행되는 능력. 핸드오프 대상(`skin`·`gait`)은 여기 없습니다 — 실행되지 않으니까요.
 *
 * `place` 는 PR #196 에서 실행 registry 에 들어왔고 PR #204(D-051)부터 의미 라우터도
 * 고를 수 있습니다. `general` 은 PR #279 의 일반 답변 폴백 — 라우터가 고르는 것이 아니라
 * 전문 능력이 하나도 안 골렸을 때 planner 가 붙이는 것이라 `RESOLVED_CAPABILITIES` 에는
 * 없습니다(`requested_capability` 로 못 부릅니다). 이 파일은 백엔드와 **손으로** 맞추는
 * 것이라(머리 주석) 저쪽 `CapabilityName` 이 다섯인 동안 여기가 넷이면 조용히 어긋납니다.
 */
export type CapabilityName = "training" | "life" | "walk" | "place" | "general";

/** 능력 하나의 결과 상태. 최상위 status 와 **다른 축**입니다. */
export type CapabilityStatus = "OK" | "ABSTAINED" | "REFUSED" | "PENDING" | "ERROR" | "TIMEOUT";

/**
 * 최상위 status 8개. 능력별 6개와 헷갈리지 마세요 — 집계 진리표는
 * `docs/orchestration/contracts.md` §5 가 소유합니다.
 *
 * `UNCERTAIN` 은 **전부 기권**이고 `REFUSED`/`FAILED` 가 아닙니다 (D-033).
 * `HANDOFF` 는 **순수 핸드오프**(실행된 능력 없음)입니다 — 실행과 섞이면 최상위는
 * 실행된 결과에서만 계산하고 `handoffs[]` 는 따로 항상 렌더합니다 (D-034).
 */
export type AssistantStatus =
  | "ANSWERED"
  | "PARTIAL"
  | "CLARIFY"
  | "HANDOFF"
  | "UNCERTAIN"
  | "REFUSED"
  | "PENDING"
  | "FAILED";

/** `abstention` 과 `refusal` 이 같은 모양입니다. **뜻은 다릅니다** (불변식 2). */
export type OutcomeDetail = { code: string; message: string };

export type PendingJob = { job_id: string; poll: string };

export type ErrorDetail = { kind: string; detail: string };

/**
 * `CapabilityResult`. status 마다 **동반해야 하는 필드가 정해져 있습니다** —
 * OK↔`data` · ABSTAINED↔`abstention` · REFUSED↔`refusal` · PENDING↔`job` ·
 * ERROR/TIMEOUT↔`error`. 서버가 그것을 검증하므로 화면은 status 로 갈라 그리면 됩니다.
 *
 * `data` 는 **능력이 소유하는 페이로드**라 공용 모양이 없습니다 (불변식 6).
 * 그래서 `Record<string, unknown>` 이고, 점검 화면은 해석하지 않고 그대로 보여 줍니다.
 */
export type CapabilityResult = {
  capability: CapabilityName;
  status: CapabilityStatus;
  data?: Record<string, unknown> | null;
  abstention?: OutcomeDetail | null;
  refusal?: OutcomeDetail | null;
  job?: PendingJob | null;
  error?: ErrorDetail | null;
  /** 불변식 7 — 모든 결과에 있습니다. */
  elapsed_ms: number;
};

export type Handoff = { target: string; reason: string };

/**
 * **CLARIFY 는 배타적입니다** (O-8) — 이것이 있으면 능력도 핸드오프도 실행되지 않았습니다.
 *
 * 생산자는 둘입니다 (D-068): 좌표가 없어서 계획 시점에 나는 되묻기와, 미명세 질문에
 * General 이 답 시점에 내는 되묻기. `missing` 이 그 **종류**를 말하고
 * (`location.lat` vs `observation`), `missing_axes` 는 관찰 되묻기가 **무엇을 물었는지**를
 * 말합니다.
 *
 * ⚠ `missing_axes` 는 **"아직 물어본 항목"이지 강아지의 상태가 아닙니다.** `APPETITE` 가
 * 있다는 것은 "식욕을 물었다"는 뜻이지 "식욕에 문제가 있다"가 아닙니다. 비어 있을 수도
 * 있고, 그건 "물은 것이 없다"가 아니라 "축을 모른다"는 뜻입니다.
 */
export type ObservationAxis =
  | "APPETITE"
  | "ENERGY"
  | "STOOL"
  | "VOMIT"
  | "BREATHING"
  | "MOBILITY"
  | "OTHER";

export type ClarifyRequest = {
  question: string;
  missing: string[];
  missing_axes?: ObservationAxis[];
};

/**
 * `RouteTrace` — **어느 길로 갔나**. 점검 권한이 있을 때만 옵니다 (#238).
 *
 * `model` 과 `prompt_version` 이 `null` 인 것은 **모르는 것이 아니라 없는 것**입니다 —
 * 결정론적 경로는 모델을 아예 부르지 않습니다. 빈칸으로 그리면 그 둘이 헷갈립니다.
 *
 * 여기 실리는 것은 **메타데이터뿐**입니다. 질문 원문·프롬프트 본문·공급자 payload 는
 * 응답에도 로그에도 싣지 않습니다 (D-037).
 */
export type RouteTrace = {
  router: "deterministic" | "llm";
  model?: string | null;
  prompt_version?: string | null;
};

export type AssistantResponse = {
  request_id: string;
  status: AssistantStatus;
  message: string;
  results: CapabilityResult[];
  handoffs: Handoff[];
  clarify?: ClarifyRequest | null;
  /** 점검 권한이 없으면 `null` 입니다. 옛 백엔드에서는 키 자체가 없습니다. */
  route?: RouteTrace | null;
};
