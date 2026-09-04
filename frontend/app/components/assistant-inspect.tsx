"use client";

import { FormEvent, useState } from "react";

import { ApiError, apiJson } from "@/lib/api";
import {
  RESOLVED_CAPABILITIES,
  type AssistantQueryRequest,
  type AssistantResponse,
  type AssistantStatus,
  type CapabilityResult,
  type CapabilityStatus,
} from "@/lib/assistant";
// 좌표표를 복사하지 않습니다 — 두 벌이 되면 한쪽만 고쳐집니다.
// `walk-inspect.tsx` 가 소유하고 여기서 빌려 씁니다.
import { PRESETS } from "./walk-inspect";

/**
 * `POST /assistant/query` 점검 패널.
 *
 * **앱 ChatScreen 이 실제로 쓰는 유일한 경로입니다.** 나머지 세 갈래(훈련 · 생활 · 피부)는
 * 직접 API 라, 앱에서 같은 질문이 어떻게 흘러가는지는 지금까지 `curl` 로만 봤습니다
 * (A0 스모크 `#169`). 이 패널이 그 자리를 대신합니다.
 *
 * **응답을 줄이지 않고 그대로 그립니다.** 점검 도구라 `results[]` 를 하나도 접지 않고,
 * `handoffs[]` 는 실행 결과가 있어도 **항상** 렌더합니다 (D-034).
 *
 * ⚠️ **관리자로 부른 결과입니다.** `routers/assistant.py` 가 principal 을
 * `kind=ADMIN` + permissions 로 넘기는데 라우팅 인가 매트릭스가 종류별로 다릅니다
 * (`docs/orchestration-routing.md` §5). 앱 회원과 같은 결과를 보려면 테스트 회원 토큰이
 * 필요하고, 그건 콘솔 로드맵 C4 입니다.
 *
 * ⚠️ **라우터 종류는 여기서 볼 수 없습니다.** `RoutePlan` 이 공개 응답에 안 실립니다
 * (`lib/assistant.ts` 머리). `results[].capability` 로 무엇이 실행됐는지만 보입니다.
 */

/** 최상위 8상태. 색이 뜻을 나릅니다 — `REFUSED`(정책·안전)와 `FAILED`(고장)를 안 합칩니다. */
const STATUS_STYLE: Record<AssistantStatus, string> = {
  ANSWERED: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  PARTIAL: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100",
  CLARIFY: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-100",
  HANDOFF: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100",
  UNCERTAIN: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200",
  REFUSED: "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-100",
  PENDING: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-100",
  FAILED: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-100",
};

/** 한 줄 설명. 집계 진리표(`orchestration-contracts.md` §5)를 화면에 옮긴 것입니다. */
const STATUS_NOTE: Record<AssistantStatus, string> = {
  ANSWERED: "실행된 능력이 전부 성공",
  PARTIAL: "일부만 성공 — 나머지는 기권·거절·실패",
  CLARIFY: "되물음. 아무것도 실행되지 않았습니다 (배타적)",
  HANDOFF: "순수 핸드오프 — 실행된 능력이 없습니다",
  UNCERTAIN: "전부 기권. 거절도 실패도 아닙니다 (D-033)",
  REFUSED: "정책·안전 거절. 고장이 아닙니다",
  PENDING: "비동기 작업이 걸려 있습니다",
  FAILED: "오류·타임아웃만 남았습니다",
};

const CAPABILITY_STATUS_STYLE: Record<CapabilityStatus, string> = {
  OK: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  ABSTAINED: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200",
  REFUSED: "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-100",
  PENDING: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-100",
  ERROR: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-100",
  TIMEOUT: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-100",
};

const CAPABILITY_LABEL: Record<string, string> = {
  training: "훈련",
  life: "생활 · 제도",
  walk: "산책 적합도",
  place: "장소 추천",
  skin: "피부 스크리닝",
  gait: "보행 분석",
};

function capabilityLabel(name: string): string {
  return CAPABILITY_LABEL[name] ?? name;
}

/**
 * 화면에 띄울 문구.
 *
 * **`FAILED` 는 여기 오지 않습니다** — 오케스트레이터의 실패는 200 + `status: "FAILED"` 라
 * 정상 응답으로 들어옵니다. 여기 걸리는 것은 그 앞의 HTTP 경계(인증 · 422)와 네트워크뿐입니다.
 */
function messageOf(caught: unknown): { message: string; hint?: string } {
  if (caught instanceof ApiError) {
    if (caught.status === 401) return { message: "세션이 만료되었습니다. 다시 로그인해 주세요." };
    if (caught.status === 403) return { message: "이 계정으로는 부를 수 없는 API 입니다." };
    if (caught.status === 422) {
      return {
        message: caught.message,
        hint: "요청 스키마가 extra=forbid 입니다 — 좌표 범위(위도 33~39 · 경도 124~132)를 벗어났거나, 공백만 든 값을 보냈습니다.",
      };
    }
    if (caught.status === 404) {
      return {
        message: "이 서버에 /assistant/query 라우트가 없습니다.",
        hint: "백엔드가 아직 옛 버전입니다. 프론트만 먼저 배포되면 이 404 가 납니다.",
      };
    }
    return { message: caught.message };
  }
  if (caught instanceof DOMException && caught.name === "AbortError") {
    return {
      message: "50초 안에 응답이 오지 않았습니다.",
      hint: "의미 라우팅(LLM)과 능력 실행이 겹치면 첫 요청이 깁니다. 한 번 더 눌러 보세요.",
    };
  }
  return { message: "어시스턴트 API 에 연결할 수 없습니다." };
}

export default function AssistantInspect() {
  const [query, setQuery] = useState("");
  const [capability, setCapability] = useState("");
  const [activeDogId, setActiveDogId] = useState("");
  const [sendLocation, setSendLocation] = useState(true);
  const [lat, setLat] = useState("37.4979");
  const [lon, setLon] = useState("127.0276");

  const [result, setResult] = useState<AssistantResponse | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery || isLoading) return;

    setIsLoading(true);
    setError(null);
    setResult(null);
    setElapsedMs(null);

    const controller = new AbortController();
    // 다른 패널과 같은 50초입니다. 여기는 라우팅(LLM)과 능력 실행이 **직렬로** 겹칩니다.
    const timeout = window.setTimeout(() => controller.abort(), 50_000);
    const startedAt = performance.now();

    try {
      // 비운 칸은 **보내지 않습니다.** 서버가 공백 문자열을 422 로 막는데, 그 422 는
      // API 의 답이 아니라 이 화면의 실수라 점검에 아무 도움이 안 됩니다.
      const body: AssistantQueryRequest = { query: normalizedQuery };
      if (capability) body.requested_capability = capability;
      if (activeDogId.trim()) body.active_dog_id = activeDogId.trim();
      if (sendLocation) {
        body.location = { lat: Number.parseFloat(lat), lon: Number.parseFloat(lon) };
      }

      setResult(
        await apiJson<AssistantResponse>("/api/assistant/query", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        }),
      );
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setElapsedMs(Math.round(performance.now() - startedAt));
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  return (
    <section
      aria-labelledby="assistant-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-teal-700 dark:text-teal-400">
            오케스트레이션 · 앱과 같은 경로
          </p>
          <h2 id="assistant-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
            어시스턴트 <code className="text-base font-normal text-zinc-500">POST /assistant/query</code>
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            앱 ChatScreen 은 자유 텍스트를 전부 이리로 보냅니다. 어느 능력이 실행됐고, 무엇을 거절·기권했고,
            어디로 넘겼는지를 응답 그대로 봅니다.
          </p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">관리자로 부른 결과입니다</span>
      </div>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="sr-only" htmlFor="assistant-query">
            질문
          </label>
          <input
            id="assistant-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            disabled={isLoading}
            placeholder="예: 지금 산책 나가도 될까? / 목줄 안 하면 과태료 얼마야?"
            className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-teal-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="rounded-lg bg-zinc-900 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {isLoading ? "라우팅 중…" : "보내기"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-zinc-500 dark:text-zinc-400" htmlFor="assistant-capability">
            requested_capability
          </label>
          <select
            id="assistant-capability"
            value={capability}
            onChange={(event) => setCapability(event.target.value)}
            disabled={isLoading}
            title="라우팅 신호일 뿐 인가가 아닙니다 (D-036). 비우면 의미 라우팅이 고릅니다."
            className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          >
            <option value="">안 보냄 (의미 라우팅)</option>
            {RESOLVED_CAPABILITIES.map((name) => (
              <option key={name} value={name}>
                {name} · {capabilityLabel(name)}
              </option>
            ))}
          </select>

          <label className="text-xs text-zinc-500 dark:text-zinc-400" htmlFor="assistant-dog">
            active_dog_id
          </label>
          <input
            id="assistant-dog"
            value={activeDogId}
            onChange={(event) => setActiveDogId(event.target.value)}
            disabled={isLoading}
            placeholder="비워 둠"
            title="앱 회원 컨텍스트에서만 뜻이 있습니다 — 관리자 계정으로는 반려견 조회가 안 됩니다."
            className="w-40 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 rounded-lg bg-zinc-50 px-3 py-2 dark:bg-zinc-900">
          <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={sendLocation}
              onChange={(event) => setSendLocation(event.target.checked)}
              disabled={isLoading}
              className="size-4"
            />
            location 함께 보내기
          </label>
          <label className="sr-only" htmlFor="assistant-lat">
            위도
          </label>
          <input
            id="assistant-lat"
            value={lat}
            onChange={(event) => setLat(event.target.value)}
            disabled={isLoading || !sendLocation}
            inputMode="decimal"
            className="w-28 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950"
          />
          <label className="sr-only" htmlFor="assistant-lon">
            경도
          </label>
          <input
            id="assistant-lon"
            value={lon}
            onChange={(event) => setLon(event.target.value)}
            disabled={isLoading || !sendLocation}
            inputMode="decimal"
            className="w-28 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-teal-500 focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950"
          />
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              disabled={isLoading || !sendLocation}
              onClick={() => {
                setLat(String(preset.lat));
                setLon(String(preset.lon));
              }}
              className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-950"
            >
              {preset.label}
            </button>
          ))}
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            끄고 산책을 물으면 <strong className="font-medium">CLARIFY</strong> 가 나옵니다
          </span>
        </div>
      </form>

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {isLoading && (
          <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            어느 능력을 부를지 고르고, 고른 것을 실행하고 있습니다.
          </p>
        )}

        {error && (
          <div
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
          >
            <p>{error.message}</p>
            {error.hint && <p className="mt-1 text-xs opacity-80">{error.hint}</p>}
          </div>
        )}

        {result && (
          <>
            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_STYLE[result.status]}`}>
                  {result.status}
                </span>
                <span className="text-xs text-zinc-500 dark:text-zinc-400">{STATUS_NOTE[result.status]}</span>
                <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
                  <code>{result.request_id}</code>
                  {elapsedMs !== null && ` · ${(elapsedMs / 1000).toFixed(1)}초`}
                </span>
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-zinc-800 dark:text-zinc-100">
                {result.message}
              </p>
            </article>

            {result.clarify && (
              <article className="rounded-lg border border-sky-300 bg-sky-50 p-4 text-sm dark:border-sky-800 dark:bg-sky-950">
                <h3 className="font-medium text-sky-900 dark:text-sky-100">되물음 · clarify</h3>
                <p className="mt-2 text-sky-900 dark:text-sky-100">{result.clarify.question}</p>
                <p className="mt-2 text-xs text-sky-800 dark:text-sky-200">
                  빠진 것: {result.clarify.missing.join(" · ")}
                </p>
                <p className="mt-2 text-xs text-sky-700 dark:text-sky-300">
                  CLARIFY 는 배타적입니다 — 능력도 핸드오프도 실행되지 않았습니다 (O-8). 같은 요청을 다시 보내도
                  이중 실행·이중 과금이 안 되는 이유가 그것입니다.
                </p>
              </article>
            )}

            {/* 실행 결과가 있어도 **항상** 그립니다 (D-034). 성공이 핸드오프를 가리지 않습니다. */}
            {result.handoffs.length > 0 && (
              <article className="rounded-lg border border-indigo-300 bg-indigo-50 p-4 text-sm dark:border-indigo-800 dark:bg-indigo-950">
                <h3 className="font-medium text-indigo-900 dark:text-indigo-100">
                  핸드오프 {result.handoffs.length}건
                </h3>
                <ul className="mt-2 flex flex-col gap-1">
                  {result.handoffs.map((handoff) => (
                    <li
                      key={`${handoff.target}-${handoff.reason}`}
                      className="text-indigo-900 dark:text-indigo-100"
                    >
                      <strong className="font-medium">{capabilityLabel(handoff.target)}</strong>
                      <span className="ml-2 text-xs opacity-80">
                        {handoff.target} · {handoff.reason}
                      </span>
                    </li>
                  ))}
                </ul>
              </article>
            )}

            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <h3 className="text-sm font-medium">능력 결과 {result.results.length}건</h3>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                최상위 status 는 이것들을 <strong className="font-medium">결정적으로</strong> 집계한 것입니다 —
                판정에 LLM 을 쓰지 않습니다 (O-5). 기권과 거절은 다른 것이라 합치지 않습니다 (불변식 2).
              </p>
              {result.results.length === 0 ? (
                <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">실행된 능력이 없습니다.</p>
              ) : (
                <ul className="mt-3 flex flex-col gap-3">
                  {result.results.map((item, index) => (
                    <ResultCard key={`${item.capability}-${index}`} result={item} />
                  ))}
                </ul>
              )}
            </article>

            <details className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <summary className="cursor-pointer text-sm text-zinc-500 hover:underline dark:text-zinc-400">
                원문 JSON — 화면이 줄인 것이 없는지 대조합니다
              </summary>
              <pre className="mt-3 overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs leading-5 text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          </>
        )}
      </div>
    </section>
  );
}

function ResultCard({ result }: { result: CapabilityResult }) {
  return (
    <li className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{capabilityLabel(result.capability)}</span>
        <code className="text-xs text-zinc-500 dark:text-zinc-400">{result.capability}</code>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${CAPABILITY_STATUS_STYLE[result.status]}`}
        >
          {result.status}
        </span>
        {/* 불변식 7 — 모든 결과에 있습니다. */}
        <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">{result.elapsed_ms}ms</span>
      </div>

      {result.refusal && (
        <p className="mt-2 text-xs text-orange-900 dark:text-orange-200">
          <strong className="font-medium">거절</strong> <code>{result.refusal.code}</code> —{" "}
          {result.refusal.message}
        </p>
      )}
      {result.abstention && (
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-300">
          <strong className="font-medium">기권</strong> <code>{result.abstention.code}</code> —{" "}
          {result.abstention.message}
        </p>
      )}
      {result.error && (
        <p className="mt-2 text-xs text-red-900 dark:text-red-200">
          <strong className="font-medium">{result.error.kind}</strong> — {result.error.detail}
        </p>
      )}
      {result.job && (
        <p className="mt-2 text-xs text-violet-900 dark:text-violet-200">
          <strong className="font-medium">대기 중</strong> <code>{result.job.job_id}</code> · 조회{" "}
          <code>{result.job.poll}</code>
        </p>
      )}

      {result.data && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-zinc-500 hover:underline dark:text-zinc-400">
            data 보기 — 능력이 소유하는 페이로드입니다 (불변식 6)
          </summary>
          <pre className="mt-2 overflow-x-auto rounded bg-white p-2 text-xs leading-5 text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
            {JSON.stringify(result.data, null, 2)}
          </pre>
        </details>
      )}
    </li>
  );
}
