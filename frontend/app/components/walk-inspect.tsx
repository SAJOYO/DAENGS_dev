"use client";

import { FormEvent, useState } from "react";

import { ApiError, apiJson } from "@/lib/api";
import {
  isBasisValue,
  walkFromErrorBody,
  type BasisState,
  type BasisValue,
  type Grade,
  type WalkResponse,
} from "@/lib/life-rag";

/**
 * `GET /life/walk-conditions` 점검 패널.
 *
 * **이 API 를 부르는 유일한 화면입니다.** 앱은 `/assistant/query` 만 쓰므로, 이 패널이 없으면
 * 살아 있는지 보려고 사람이 좌표를 넣어 `curl` 을 쳐야 합니다.
 *
 * 이 화면의 본론은 등급이 아니라 **`sources`** 입니다 — 9개 출처 중 어느 것이 죽었고
 * 왜 죽었는지가 보여야 점검입니다.
 *
 * ⚠️ 경로가 A4(#176)로 `/walk` → `/life/walk-conditions` 가 됐고 리다이렉트가 없습니다.
 * 산책 **기록**(`/app/walks`)과 한 글자 차이였던 것을 갈라 놓은 것이 그 카드입니다.
 */

/** 눈으로 좌표를 외우지 않아도 되게. 강남은 `#23` 실기 확인에 쓴 좌표 그대로입니다. */
const PRESETS: Array<{ label: string; lat: number; lon: number }> = [
  { label: "강남", lat: 37.4979, lon: 127.0276 },
  { label: "서울시청", lat: 37.5663, lon: 126.9779 },
  { label: "부산 해운대", lat: 35.1587, lon: 129.1604 },
  { label: "제주시", lat: 33.4996, lon: 126.5312 },
];

const GRADE_STYLE: Record<Grade, string> = {
  GOOD: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  CAUTION: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100",
  UNSAFE: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-100",
  unknown: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
};

const AXIS_LABEL: Record<string, string> = {
  heat: "더위",
  cold: "추위",
  air: "대기질",
  rain: "강수",
  wind: "바람",
  uv: "자외선",
};

function axisLabel(key: string): string {
  return AXIS_LABEL[key] ?? key;
}

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

function stamp(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 화면에 띄울 문구.
 *
 * **503 은 여기로 오지 않습니다** — 격자가 없는 503 은 본문이 실려 있어서 호출부가
 * 결과로 다룹니다(`walkFromErrorBody`). 여기 오는 것은 본문이 없는 실패뿐입니다.
 */
function messageOf(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 401) return "세션이 만료되었습니다. 다시 로그인해 주세요.";
    if (caught.status === 403) return "이 계정으로는 부를 수 없는 API 입니다.";
    if (caught.status === 422) return "좌표 범위를 벗어났습니다. 위도 33~39 / 경도 124~132 안에서 넣어 주세요.";
    return caught.message;
  }
  if (caught instanceof DOMException && caught.name === "AbortError") {
    return "50초 안에 응답이 오지 않았습니다. 콜드 캐시라면 한 번 더 눌러 보세요.";
  }
  return "산책 적합도 API 에 연결할 수 없습니다.";
}

export default function WalkInspect() {
  const [lat, setLat] = useState("37.4979");
  const [lon, setLon] = useState("127.0276");
  const [result, setResult] = useState<WalkResponse | null>(null);
  /** 503(격자 없음)으로 받은 결과인지. 본문은 있지만 판정은 없습니다. */
  const [degraded, setDegraded] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading) return;

    setIsLoading(true);
    setError(null);
    setResult(null);
    setDegraded(false);
    setElapsedMs(null);

    const controller = new AbortController();
    // 훈련 RAG 와 같은 50초입니다. 여기는 생성이 아니라 **콜드 캐시** 때문입니다 —
    // 정적 메타(측정소 목록·AWS 지점표)를 처음 받는 요청이 8초대입니다 (`#23` 실측).
    const timeout = window.setTimeout(() => controller.abort(), 50_000);
    const startedAt = performance.now();

    try {
      const query = new URLSearchParams({ lat: lat.trim(), lon: lon.trim() });
      setResult(await apiJson<WalkResponse>(`/api/life/walk-conditions?${query}`, { signal: controller.signal }));
    } catch (caught) {
      // ⚠️ 503 은 실패가 아니라 **"판정 불가"라는 답**입니다. 본문에 어느 출처가 죽었는지
      // 들어 있고, 그것을 버리면 이 화면을 만든 이유가 사라집니다 (RT-001 ⑥).
      const salvaged = caught instanceof ApiError ? walkFromErrorBody(caught.body) : null;
      if (salvaged) {
        setResult(salvaged);
        setDegraded(true);
      } else {
        setError(messageOf(caught));
      }
    } finally {
      setElapsedMs(Math.round(performance.now() - startedAt));
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  return (
    <section
      aria-labelledby="walk-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-sky-700 dark:text-sky-400">생활 RAG · 실시간</p>
          <h2 id="walk-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
            산책 적합도 <code className="text-base font-normal text-zinc-500">GET /life/walk-conditions</code>
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            좌표 하나로 지금 등급과 24시간 타임라인, 권장 구간을 냅니다. 저장하지 않고 매번 받아 옵니다.
          </p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">첫 요청은 8초 안팎이 정상입니다</span>
      </div>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              disabled={isLoading}
              onClick={() => {
                setLat(String(preset.lat));
                setLon(String(preset.lon));
              }}
              className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              {preset.label}
            </button>
          ))}
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="flex-1">
            <span className="text-xs text-zinc-500 dark:text-zinc-400">위도 (33 ~ 39)</span>
            <input
              value={lat}
              onChange={(event) => setLat(event.target.value)}
              disabled={isLoading}
              inputMode="decimal"
              className="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-sky-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <label className="flex-1">
            <span className="text-xs text-zinc-500 dark:text-zinc-400">경도 (124 ~ 132)</span>
            <input
              value={lon}
              onChange={(event) => setLon(event.target.value)}
              disabled={isLoading}
              inputMode="decimal"
              className="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-sky-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <button
            type="submit"
            disabled={isLoading || !lat.trim() || !lon.trim()}
            className="self-end rounded-lg bg-zinc-900 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {isLoading ? "받아오는 중…" : "판정 받기"}
          </button>
        </div>
      </form>

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {isLoading && (
          <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            기상청·에어코리아·카카오를 부르고 있습니다. 캐시가 비어 있으면 8초 정도 걸립니다.
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
            {error}
          </p>
        )}

        {result && (
          <>
            {degraded && (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                <strong className="font-medium">503 · 판정 불가</strong> — 기상청 격자를 통째로 못 받았습니다.
                등급 대신 아래 <strong className="font-medium">출처</strong>를 보세요. 어느 쪽이 죽었는지 나옵니다.
              </p>
            )}

            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-3 py-1 text-sm font-medium ${GRADE_STYLE[result.now.grade]}`}>
                  {result.now.grade}
                </span>
                {result.now.dominant.map((axis) => (
                  <span key={axis} className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                    지배축 {axisLabel(axis)}
                  </span>
                ))}
                {result.now.capped && (
                  <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                    상한 적용
                  </span>
                )}
                <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
                  {stamp(result.generated_at)} 기준
                  {elapsedMs !== null && ` · ${(elapsedMs / 1000).toFixed(1)}초`}
                </span>
              </div>

              <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-200">{result.location.label}</p>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                격자 [{result.location.grid[0]}, {result.location.grid[1]}]
                {result.location.aws_station && ` · AWS ${result.location.aws_station}`}
                {result.location.warning_zone && ` · 특보구역 ${result.location.warning_zone}`}
              </p>

              {result.now.unknown_axes.length > 0 && (
                <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
                  판정 못 한 축: {result.now.unknown_axes.map(axisLabel).join(" · ")}
                </p>
              )}

              <ul className="mt-4 flex flex-col gap-3">
                {Object.entries(result.now.axes).map(([key, axis]) => (
                  <li key={key} className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{axisLabel(key)}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${GRADE_STYLE[axis.grade]}`}>{axis.grade}</span>
                      {axis.note && <span className="text-xs text-zinc-500 dark:text-zinc-400">{axis.note}</span>}
                    </div>
                    {Object.keys(axis.derived).length > 0 && (
                      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                        계산값 {Object.entries(axis.derived).map(([k, v]) => `${k} ${v}`).join(" · ")}
                      </p>
                    )}
                    {axis.basis.length > 0 && (
                      <ul className="mt-2 flex flex-col gap-1">
                        {axis.basis.map((basis, index) => (
                          <li key={index} className="text-xs text-zinc-600 dark:text-zinc-400">
                            {isBasisValue(basis) ? <BasisValueLine basis={basis} /> : <BasisStateLine basis={basis} />}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </article>

            {result.windows.length > 0 && (
              <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                <h3 className="text-sm font-medium">권장 구간</h3>
                <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                  끝 시각은 <strong className="font-medium">마지막으로 좋은 시각</strong>입니다. 그다음 판정까지 좋다는 뜻이 아닙니다.
                </p>
                <ul className="mt-3 flex flex-col gap-2">
                  {result.windows.map((window) => (
                    <li key={`${window.from}-${window.to}`} className="flex items-center gap-2 text-sm">
                      <span className={`rounded-full px-2 py-0.5 text-xs ${GRADE_STYLE[window.grade]}`}>{window.grade}</span>
                      <span className="text-zinc-700 dark:text-zinc-200">
                        {stamp(window.from)} ~ {stamp(window.to)}
                      </span>
                    </li>
                  ))}
                </ul>
              </article>
            )}

            {result.timeline.length > 0 && (
              <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
                <h3 className="text-sm font-medium">24시간 타임라인</h3>
                <div className="mt-3 overflow-x-auto">
                  <ul className="flex gap-1">
                    {result.timeline.map((point) => (
                      <li
                        key={point.at}
                        title={`${stamp(point.at)} · ${point.grade}${point.dominant.length ? ` · ${point.dominant.map(axisLabel).join(",")}` : ""}`}
                        className={`flex w-12 shrink-0 flex-col items-center rounded px-1 py-2 text-[10px] ${GRADE_STYLE[point.grade]}`}
                      >
                        <span>{hhmm(point.at)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </article>
            )}

            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <h3 className="text-sm font-medium">
                출처 {result.sources.filter((source) => source.ok).length}/{result.sources.length}
              </h3>
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {result.sources.map((source) => (
                  <li key={source.provider} className="flex items-baseline gap-2 text-xs">
                    <span
                      className={
                        source.ok
                          ? "rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100"
                          : "rounded-full bg-red-100 px-2 py-0.5 text-red-900 dark:bg-red-950 dark:text-red-100"
                      }
                    >
                      {source.ok ? "ok" : "fail"}
                    </span>
                    <span className="text-zinc-700 dark:text-zinc-200">{source.provider}</span>
                    {source.stale && <span className="text-zinc-500 dark:text-zinc-400">(캐시)</span>}
                    {source.reason && <span className="text-zinc-500 dark:text-zinc-400">{source.reason}</span>}
                  </li>
                ))}
              </ul>
            </article>

            {result.notes.length > 0 && (
              <ul className="flex flex-col gap-1 text-xs text-zinc-500 dark:text-zinc-400">
                {result.notes.map((note) => (
                  <li key={note}>· {note}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function BasisValueLine({ basis }: { basis: BasisValue }) {
  return (
    <>
      <span className="text-zinc-700 dark:text-zinc-300">{basis.quantity}</span> {basis.value}
      {basis.unit_note && ` ${basis.unit_note}`}
      {basis.grade !== null && basis.grade !== undefined && ` (등급 ${basis.grade})`}
      <span className="text-zinc-400"> · {basis.source} · {basis.spatial_ref} · {stamp(basis.valid_at)}</span>
    </>
  );
}

function BasisStateLine({ basis }: { basis: BasisState }) {
  return (
    <>
      <span className="text-zinc-700 dark:text-zinc-300">{basis.kind}</span> {basis.category}
      <span className="text-zinc-400">
        {" "}
        · {basis.area} · {stamp(basis.valid_from)}
        {basis.valid_to ? ` ~ ${stamp(basis.valid_to)}` : " ~"}
      </span>
    </>
  );
}
