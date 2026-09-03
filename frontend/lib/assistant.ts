/**
 * 공개 진입점 `POST /assistant/query` 의 요청·응답 타입.
 *
 * **백엔드와 손으로 맞춘 것입니다.** 원본이 두 곳입니다 —
 * 요청은 `backend/src/daengs_backend/schemas/assistant.py`,
 * 응답은 `backend/src/daengs_backend/orchestration/contracts.py` 이고
 * 계약 문서는 `docs/orchestration-contracts.md` §5(응답) · §8(요청)입니다.
 * **한쪽만 고치면 조용히 어긋납니다** — 필드를 더하거나 이름을 바꿀 때는 셋을 같이 보세요.
 *
 * ⚠️ **여기 있는 것이 응답의 전부입니다.** `RoutePlan` 은 공개 응답에 실리지 않아
 * 라우터 종류(`deterministic`/`llm`)와 모델 이름은 밖에서 볼 수 없습니다. 화면이 그릴 수
 * 있는 것은 `results[].capability` 로 **무엇이 실행됐나** 이고, "라우터가 무엇을 골랐나" 는
 * 그 역산입니다 (`assistant-inspect.tsx` 가 그렇게 적어 둡니다).
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
 * 앞의 셋은 실행(`execute`), 뒤의 둘은 순수 핸드오프입니다.
 */
export const RESOLVED_CAPABILITIES = ["training", "life", "walk", "skin", "gait"] as const;

// ------------------------------------------------------------------- 응답

/** 실행되는 능력. 핸드오프 대상(`skin`·`gait`)은 여기 없습니다 — 실행되지 않으니까요. */
export type CapabilityName = "training" | "life" | "walk";

/** 능력 하나의 결과 상태. 최상위 status 와 **다른 축**입니다. */
export type CapabilityStatus = "OK" | "ABSTAINED" | "REFUSED" | "PENDING" | "ERROR" | "TIMEOUT";

/**
 * 최상위 status 8개. 능력별 6개와 헷갈리지 마세요 — 집계 진리표는
 * `docs/orchestration-contracts.md` §5 가 소유합니다.
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

/** **CLARIFY 는 배타적입니다** (O-8) — 이것이 있으면 능력도 핸드오프도 실행되지 않았습니다. */
export type ClarifyRequest = { question: string; missing: string[] };

export type AssistantResponse = {
  request_id: string;
  status: AssistantStatus;
  message: string;
  results: CapabilityResult[];
  handoffs: Handoff[];
  clarify?: ClarifyRequest | null;
};
