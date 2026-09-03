/**
 * 생활비서(`daengs_life`) API 두 개의 응답 타입.
 *
 * **백엔드 DTO 와 손으로 맞춘 것입니다.** 원본은
 * `backend/src/daengs_life/app/dto/walk.py` 와 `.../dto/ask.py` 이고,
 * **한쪽만 고치면 조용히 어긋납니다** — 필드를 더하거나 이름을 바꿀 때는 둘을 같이 보세요.
 *
 * 여기 있는 것은 콘솔 `기능 / 검색 점검` 화면이 쓰는 만큼입니다. 앱용 클라이언트가
 * 생기면 그쪽은 더 적은 필드만 쓰게 될 수 있습니다 (RAG-028 ② 의 "응답 축소").
 */

/** 판정 불가를 `null` 이 아니라 문자열로 냅니다 (RT-001 ⑥). */
export type Grade = "UNSAFE" | "CAUTION" | "GOOD" | "unknown";

// ------------------------------------------------- GET /life/walk-conditions

/** 근거가 된 측정값 하나. */
export type BasisValue = {
  quantity: string;
  /** 구간·코드는 숫자가 아니라 라벨로 옵니다 ('강수없음'). */
  value: number | string;
  unit_note?: string | null;
  /** API 가 준 공식 등급. 없는 것이 결함은 아닙니다. */
  grade?: number | null;
  source: string;
  spatial_ref: string;
  valid_at: string;
  issued_at: string;
};

/** 근거가 된 상태 하나 — 특보·대기질 예보. 값이 아니라 구간 + 범주입니다. */
export type BasisState = {
  kind: string;
  category: string;
  area: string;
  valid_from: string;
  valid_to?: string | null;
  issued_at: string;
};

/** `quantity` 가 있으면 값, 없으면 상태. 둘을 가르는 유일한 표시입니다. */
export function isBasisValue(basis: BasisValue | BasisState): basis is BasisValue {
  return "quantity" in basis;
}

export type Axis = {
  grade: Grade;
  note: string;
  basis: Array<BasisValue | BasisState>;
  /** 체감온도처럼 **우리가 계산한** 값. */
  derived: Record<string, number>;
};

/** `now` — 유일하게 상세 근거를 싣는 자리입니다. */
export type Verdict = {
  at: string;
  grade: Grade;
  dominant: string[];
  axes: Record<string, Axis>;
  unknown_axes: string[];
  capped: boolean;
  /** 부가 표시. **등급에 영향이 없습니다.** */
  uv?: Axis | null;
};

/** T+0 ~ T+24h, 1시간 간격. 근거는 안 실립니다. */
export type TimelinePoint = {
  at: string;
  grade: Grade;
  dominant: string[];
};

/**
 * 산책 권장 구간.
 *
 * **키가 `from` 입니다.** 파이썬 예약어라 서버가 별칭으로 내보내고
 * (`WindowOut.from_` + `response_model_by_alias=True`), 받는 쪽은 그대로 `from` 입니다.
 * `to` 는 **마지막으로 좋은 시각**이지 "그때까지 좋다"가 아닙니다.
 */
export type WalkWindow = {
  from: string;
  to: string;
  grade: Grade;
};

export type WalkLocation = {
  dong: string;
  grid: [number, number];
  air_station?: string | null;
  air_station_km?: number | null;
  aws_station?: string | null;
  warning_zone?: string | null;
  /** "○○동 (측정소: △△) 기준" — **서버가 만듭니다.** 클라이언트가 조립하지 않습니다. */
  label: string;
};

/** 저하된 이유가 사용자에게 보이는 자리. 점검 화면에서는 이게 본론입니다. */
export type WalkSource = {
  provider: string;
  ok: boolean;
  stale: boolean;
  reason?: string | null;
};

export type WalkResponse = {
  location: WalkLocation;
  generated_at: string;
  now: Verdict;
  timeline: TimelinePoint[];
  windows: WalkWindow[];
  sources: WalkSource[];
  notes: string[];
};

/**
 * 503 의 `detail` 이 `WalkResponse` 인지 확인합니다.
 *
 * 격자가 통째로 없으면 서버가 **503 이되 본문은 그대로 싣습니다** (RT-001 ⑥).
 * `ApiError.body` 에서 이걸 건져내는 것이 이 화면이 존재하는 이유의 절반입니다.
 */
export function walkFromErrorBody(body: unknown): WalkResponse | null {
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  return "sources" in detail && "location" in detail ? (detail as WalkResponse) : null;
}

// ------------------------------------------------------------ POST /life/ask

/**
 * 근거 청크 하나.
 *
 * ⚠️ **`score` 는 코사인 유사도지 신뢰도가 아닙니다.** 사용자는 `0.62` 를 확신도로 읽는데,
 * 검문소③에서 광견병 질문에 혈청검사 수수료가 상위로 온 적이 있습니다 (RAG-028 ②).
 * 화면에 그 사실을 적어 두세요.
 */
export type AskHit = {
  rank: number;
  score: number;
  chunk_id: string;
  /** KPI 의 절반 — "조항 번호 인용". */
  citation: string;
  /** KPI 의 나머지 절반 — "출처 링크". */
  citation_url?: string | null;
  section?: string | null;
  document_title: string;
  content: string;
  /** `"supplementary"` = 부칙. */
  part?: string | null;
};

export type AskResponse = {
  question: string;
  answer: string;
  hits: AskHit[];
  /** 답변에 등장한 조항 번호. */
  cited: string[];
  /** 그중 컨텍스트에 없는 것 = **검문소④가 세는 수**. 0 이 아니면 지어낸 것입니다. */
  ungrounded: string[];
  model: string;
  embedding_model: string;
};
