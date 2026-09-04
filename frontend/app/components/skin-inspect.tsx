"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiJson } from "@/lib/api";
import {
  GUIDE_BAND_TEXT,
  guideHint,
  type NormBox,
  type ScreenResponse,
  type ScreenVerdict,
  type ScreeningHealth,
} from "@/lib/screening";

/**
 * `POST /screen/v1/screen` 점검 패널.
 *
 * **응답을 줄이지 않고 그대로 그립니다.** 이 화면은 보호자 화면이 아니라 점검
 * 도구라 `meta` 와 원문 JSON 까지 다 보여 줍니다. 앱(DAENGS_APP)이 그리는 화면은
 * 이것보다 훨씬 적게 씁니다.
 *
 * ★ **여기서 절대 하면 안 되는 것 — `distribution[0]` 을 강조하는 것입니다.**
 *   2단계가 고른 이름은 holdout 에서 **56.6% 틀립니다.** 그래서 계약에 "1등 병변"
 *   필드가 아예 없고(D-023), 백엔드는 `tests/test_screening_agent.py` 가 그런 키가
 *   생기는지 감시합니다. **프론트에는 그런 감시가 없습니다** — 아래 분포를 그리는
 *   코드가 index 로 스타일을 가르지 않는 것이 유일한 방어입니다. 굵게·크게·
 *   "가장 유력" 어느 것도 붙이지 마세요.
 *
 * 부르는 주소가 `/api/screen/*` 인 이유: nginx 의 `/api/` 가 접두사를 떼고 backend 로
 * 넘겨서 앱이 쓰는 `/screen/` 과 같은 곳에 닿습니다. 오리진을 박으면 쿠키가 안 실립니다 (D-015).
 */

/** `service.py` 의 `MAX_BYTES` 와 같은 값. 413 을 받아 보는 것보다 먼저 막습니다. */
const MAX_BYTES = 12 * 1024 * 1024;

const VERDICT_STYLE: Record<ScreenVerdict, string> = {
  normal: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100",
  abnormal: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100",
  retake: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
};

const VERDICT_LABEL: Record<ScreenVerdict, string> = {
  normal: "정상 소견",
  abnormal: "이상 소견",
  retake: "다시 찍기",
};

type Failure = { message: string; hint?: string };

function failureOf(caught: unknown): Failure {
  if (caught instanceof ApiError) {
    switch (caught.status) {
      case 400:
      case 413:
        return { message: caught.message };
      case 415:
        return {
          message: caught.message,
          hint: "서버의 PIL 이 못 여는 형식입니다. 아이폰 HEIC 가 가장 흔한 원인이고, 그 경우 이 화면의 미리보기도 안 뜹니다.",
        };
      case 422:
        return {
          message: caught.message,
          hint: "네모를 정규화 [x, y, w, h] 로 못 보냈다는 뜻입니다. 사람이 만들 수 있는 상태가 아니라 이 화면의 버그입니다.",
        };
      case 401:
        return { message: "세션이 만료되었습니다. 다시 로그인해 주세요." };
      case 404:
        return {
          message: "이 서버에 /screen 라우터가 없습니다.",
          hint: "backend 가 daengs_screening 을 안 물고 있는 빌드입니다 (D-040 이전).",
        };
      case 503:
        return {
          message: caught.message,
          hint: "가중치가 없거나 못 읽는 상태입니다. 모델이 고장난 게 아니고, 요청을 바꿔도 결과가 같습니다. best.pt 는 저장소에 없어서(D-022) 서버의 SCREENING_RELEASE_DIR 에 놓여 있어야 합니다.",
        };
      case 502:
      case 504:
        return {
          message: caught.message,
          hint: "첫 요청은 가중치 350MB 를 올리느라 오래 걸립니다. /api/ 의 read timeout 이 60초라 콘솔에서만 끊길 수 있습니다 (앱이 쓰는 /screen/ 은 120초).",
        };
      default:
        return { message: caught.message };
    }
  }
  if (caught instanceof DOMException && caught.name === "AbortError") {
    return { message: "60초 안에 응답이 오지 않았습니다." };
  }
  return { message: "스크리닝 API 에 연결할 수 없습니다." };
}

export default function SkinInspect() {
  /**
   * 사진과 그 미리보기 URL 을 **한 덩어리로** 들고 있습니다.
   *
   * 따로 두고 effect 안에서 URL 을 만들면 effect 본문에서 setState 를 하게 되는데,
   * React Compiler 린트(`react-hooks/set-state-in-effect`)가 그걸 막습니다 — 연쇄
   * 렌더가 나기 때문입니다. 고를 때 같이 만들면 effect 는 정리(revoke)만 하면 됩니다.
   */
  const [picked, setPicked] = useState<{ file: File; url: string } | null>(null);
  const [box, setBox] = useState<NormBox | null>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [health, setHealth] = useState<ScreeningHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);

  // 패널을 열자마자 서버 상태부터 봅니다. 실패해도 패널은 그립니다 — healthz 조차
  // 안 되면 그 사실 자체가 알아야 할 정보입니다.
  useEffect(() => {
    let alive = true;
    apiJson<ScreeningHealth>("/api/screen/healthz")
      .then((data) => {
        if (alive) setHealth(data);
      })
      .catch((caught: unknown) => {
        if (alive) setHealthError(failureOf(caught).message);
      });
    return () => {
      alive = false;
    };
  }, []);

  // objectURL 은 안 풀면 사진을 바꿀 때마다 쌓입니다. cleanup 이 **직전** URL 을
  // 들고 있으므로, 사진을 바꿀 때도 언마운트할 때도 같이 풀립니다.
  useEffect(() => {
    const url = picked?.url;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [picked]);

  const file = picked?.file ?? null;
  const previewUrl = picked?.url ?? null;
  const tooBig = file !== null && file.size > MAX_BYTES;

  function pick(next: File | null) {
    setPicked(next ? { file: next, url: URL.createObjectURL(next) } : null);
    setBox(null);
    setDrag(null);
    setResult(null);
    setFailure(null);
    setElapsedMs(null);
  }

  /**
   * 포인터를 미리보기 기준 0~1 로. `<img>` 를 `w-full` 로만 두어 **균일 축척**이라
   * 화면상의 비율이 곧 원본의 비율입니다 — canvas 도 축척 계산도 필요 없습니다.
   */
  const norm = useCallback((event: { clientX: number; clientY: number }) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return null;
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    return {
      x: clamp((event.clientX - rect.left) / rect.width),
      y: clamp((event.clientY - rect.top) / rect.height),
    };
  }, []);

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (!previewUrl) return;
    const p = norm(event);
    if (!p) return;
    // 포인터를 캡처해 두면 이미지 **밖으로 끌어도** 드래그가 이어집니다.
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    const p = norm(event);
    if (!p) return;
    setDrag({ ...drag, x1: p.x, y1: p.y });
  }

  function onPointerUp() {
    if (!drag) return;
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    // 아주 작은 것은 그리려던 게 아니라 클릭입니다.
    if (w < 0.01 || h < 0.01) return;
    setBox([Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), w, h]);
  }

  const live: NormBox | null = drag
    ? [
        Math.min(drag.x0, drag.x1),
        Math.min(drag.y0, drag.y1),
        Math.abs(drag.x1 - drag.x0),
        Math.abs(drag.y1 - drag.y0),
      ]
    : box;
  const hint = live && live[2] > 0 && live[3] > 0 ? guideHint(live) : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || isLoading || tooBig) return;

    setIsLoading(true);
    setResult(null);
    setFailure(null);
    setElapsedMs(null);
    const controller = new AbortController();
    // `/life/ask` 의 50초보다 깁니다 — 첫 요청이 가중치 350MB 를 올립니다.
    const timeout = window.setTimeout(() => controller.abort(), 60_000);
    const startedAt = performance.now();

    try {
      const form = new FormData();
      form.append("photo", file);
      // 네모가 없으면 필드 자체를 **안 보냅니다.** box 는 선택 항목이고, 안 보내면
      // 서버가 화면 중앙으로 물러섭니다 (`meta.box_source: "center"`).
      if (box) form.append("box", JSON.stringify(box));

      // ⚠️ Content-Type 을 직접 넣지 마세요. multipart 는 브라우저가 boundary 를
      //    붙여야 하는데, 손으로 넣으면 boundary 가 빠져 서버가 못 읽습니다.
      //    FormData 는 401 재시도로 두 번 읽혀도 안전합니다 (`lib/api.ts`).
      const data = await apiJson<ScreenResponse>("/api/screen/v1/screen", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      setResult(data);
    } catch (caught) {
      setFailure(failureOf(caught));
    } finally {
      setElapsedMs(Math.round(performance.now() - startedAt));
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  return (
    <section
      aria-labelledby="skin-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-rose-700 dark:text-rose-400">피부 스크리닝 · 사진 1장</p>
          <h2 id="skin-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
            병변 스크리닝 <code className="text-base font-normal text-zinc-500">POST /screen/v1/screen</code>
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            ① 정상/이상 → ② 이상이면 병변 6종의 확률 분포. <strong className="font-medium">진단이 아닙니다</strong> —
            어떤 병변인지는 말하지 않고 분포만 보여준 뒤 진료를 권합니다.
          </p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">사진은 저장하지 않습니다</span>
      </div>

      <div className="mt-6 rounded-lg bg-zinc-50 px-4 py-3 text-xs dark:bg-zinc-900">
        {healthError ? (
          <p className="text-amber-700 dark:text-amber-300">healthz 도 응답하지 않습니다 — {healthError}</p>
        ) : health ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-zinc-600 dark:text-zinc-300">
            <span>계약 v{health.contract_version}</span>
            <span>
              가중치 {health.loaded ? "올라와 있음" : "아직 안 올림"}
              {!health.loaded && (
                <span className="ml-1 text-zinc-500 dark:text-zinc-400">(정상입니다 — 첫 요청 때 올립니다)</span>
              )}
            </span>
            {health.threshold != null && <span>1단계 임계값 {health.threshold.toFixed(4)}</span>}
            {health.mock && <span className="font-medium">mock 응답</span>}
            <span className="text-zinc-500 dark:text-zinc-400">
              <code>{health.release_dir}</code>
            </span>
          </div>
        ) : (
          <p className="text-zinc-500 dark:text-zinc-400">서버 상태를 확인하는 중입니다…</p>
        )}
      </div>

      <form onSubmit={submit} className="mt-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <label className="sr-only" htmlFor="skin-photo">
            사진
          </label>
          <input
            id="skin-photo"
            type="file"
            accept="image/*"
            disabled={isLoading}
            onChange={(event) => pick(event.target.files?.[0] ?? null)}
            className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:file:bg-zinc-800"
          />
          <button
            type="submit"
            disabled={isLoading || !file || tooBig}
            className="rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {isLoading ? "판정 중…" : "보내기"}
          </button>
        </div>

        {file && (
          <p className={`text-xs ${tooBig ? "text-red-700 dark:text-red-300" : "text-zinc-500 dark:text-zinc-400"}`}>
            {file.name} · {(file.size / 1e6).toFixed(1)}MB
            {tooBig && " — 12MB 를 넘습니다. 서버가 413 으로 돌려보내므로 보내지 않습니다."}
          </p>
        )}

        {previewUrl && (
          <div className="flex flex-col gap-2">
            <div
              ref={frameRef}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              className="relative w-full max-w-lg cursor-crosshair touch-none select-none overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800"
            >
              {/* next/image 를 안 씁니다 — blob URL 은 최적화 대상이 아니고, 여기서는
                  화면상의 크기 비율만 있으면 됩니다. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={previewUrl} alt="" draggable={false} className="block w-full" />
              {live && live[2] > 0 && live[3] > 0 && (
                <div
                  className={`pointer-events-none absolute border-2 ${
                    hint?.ok === false ? "border-red-500" : "border-emerald-400"
                  }`}
                  style={{
                    left: `${live[0] * 100}%`,
                    top: `${live[1] * 100}%`,
                    width: `${live[2] * 100}%`,
                    height: `${live[3] * 100}%`,
                  }}
                />
              )}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
              {box ? (
                <>
                  <span>네모 [{box.map((v) => v.toFixed(3)).join(", ")}]</span>
                  <button
                    type="button"
                    onClick={() => setBox(null)}
                    className="rounded-full border border-zinc-300 px-3 py-0.5 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
                  >
                    네모 지우기 (중앙 크롭으로 보내기)
                  </button>
                </>
              ) : (
                <span className="text-zinc-500 dark:text-zinc-400">
                  사진 위를 끌어서 병변에 네모를 맞추세요. 안 그리면 화면 중앙을 자릅니다 (
                  <code>box_source: center</code>).
                </span>
              )}
            </div>

            {hint && (
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                <span
                  className={`mr-2 rounded-full px-2 py-0.5 ${
                    hint.level === "recommend"
                      ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100"
                      : hint.level === "allow"
                        ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100"
                        : "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-100"
                  }`}
                >
                  {hint.level === "recommend" ? "권장" : hint.level === "allow" ? "허용" : "밴드 밖"}
                </span>
                가로 {hint.widthFrac.toFixed(3)} · 중심 이탈 {hint.centerOff.toFixed(3)} — {hint.reason}
              </p>
            )}

            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              {GUIDE_BAND_TEXT}. 서버가 추론 <strong className="font-medium">전에</strong> 다시 검사해서, 밴드 밖이면
              모델을 돌리지 않고 <code>retake</code> 로 돌려보냅니다. 1단계는 네모의 중심만 쓰고, 2단계는{" "}
              <strong className="font-medium">크기</strong>를 씁니다.
            </p>
          </div>
        )}
      </form>

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {isLoading && (
          <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            사진을 학습과 같은 함수로 자른 뒤 1단계 → 2단계로 넣고 있습니다. 첫 요청은 가중치를 올리느라 더 걸립니다.
          </p>
        )}

        {failure && (
          <div
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
          >
            <p className="whitespace-pre-wrap">{failure.message}</p>
            {failure.hint && <p className="mt-1 text-xs opacity-80">{failure.hint}</p>}
          </div>
        )}

        {result && <ScreenResult result={result} elapsedMs={elapsedMs} />}
      </div>
    </section>
  );
}

function ScreenResult({ result, elapsedMs }: { result: ScreenResponse; elapsedMs: number | null }) {
  const { stage1, stage2, meta } = result;

  return (
    <>
      <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-sm font-medium ${VERDICT_STYLE[result.verdict]}`}>
            {VERDICT_LABEL[result.verdict]} · {result.verdict}
          </span>
          {meta.stage2_low_confidence && (
            <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
              2단계 확신 낮음
            </span>
          )}
          {elapsedMs !== null && (
            <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">{(elapsedMs / 1000).toFixed(1)}초</span>
          )}
        </div>

        <p className="mt-3 text-base font-medium">{result.headline}</p>

        {/* ★ `body` 는 숫자보다 **위**에 옵니다. 순서를 바꾸지 마세요 — "판단할 수
            없습니다" 를 숫자 아래로 내리면 숫자가 답처럼 읽힙니다 (D-023, 화면 규칙 ③). */}
        <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-zinc-800 dark:text-zinc-100">{result.body}</p>
        <p className="mt-2 text-sm leading-7 text-zinc-800 dark:text-zinc-100">{result.action}</p>

        {meta.retake_reason && (
          <p className="mt-3 rounded-lg bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-900">
            돌려보낸 이유 — {meta.retake_reason}
          </p>
        )}
      </article>

      <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h3 className="text-sm font-medium">1단계 — 정상 / 이상</h3>
        {stage1.abnormal_percent != null ? (
          <>
            <div className="mt-3 flex items-baseline gap-3">
              <span className="text-2xl font-semibold tabular-nums">{stage1.abnormal_percent.toFixed(1)}%</span>
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                이상 쪽 점수 · 임계값 {stage1.threshold?.toFixed(4) ?? "—"}
              </span>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-900">
              <div
                className="h-full rounded-full bg-zinc-700 dark:bg-zinc-300"
                style={{ width: `${Math.min(100, Math.max(0, stage1.abnormal_percent))}%` }}
              />
            </div>
          </>
        ) : (
          <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">
            모델을 돌리기 전에 돌려보내서 점수가 없습니다.
          </p>
        )}
        <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
          {stage1.calibrated ? (
            <>온도 보정이 걸린 값입니다 (T={meta.stage1_temperature ?? "—"}). 확률로 읽어도 됩니다.</>
          ) : (
            <>
              <strong className="font-medium">보정 전 값입니다.</strong> 순서는 맞지만 &ldquo;100번 중 62번&rdquo;
              이라는 뜻이 아직 아닙니다 — 게이지·색으로 쓰는 건 괜찮고, 확률처럼 설명하는 문구는 붙이면 안 됩니다.
            </>
          )}
        </p>
      </article>

      {stage2.shown && (
        <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-medium">2단계 — 병변 형태 분포</h3>
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              {stage2.distribution.length}줄 — 받은 만큼 전부
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            <strong className="font-medium">1등을 뽑지 않습니다.</strong> holdout 에서 2단계가 고른 이름이 56.6%
            틀렸습니다 (D-023). 줄은 전부 같은 무게로 읽으세요 — 병명이 아니라 병변 &ldquo;형태&rdquo; 입니다.
          </p>
          {/* 모든 행이 같은 className 입니다. index 로 스타일을 가르는 코드를 넣지 마세요. */}
          <ul className="mt-3 flex flex-col gap-2">
            {stage2.distribution.map((row) => (
              <li key={row.code} className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">{row.code}</span>
                  <span className="text-sm">{row.name_ko}</span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">{row.name_en}</span>
                  <span className="ml-auto text-sm tabular-nums">{row.percent.toFixed(1)}%</span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                  <div
                    className="h-full rounded-full bg-zinc-500 dark:bg-zinc-400"
                    style={{ width: `${Math.min(100, Math.max(0, row.percent))}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
          {meta.stage2_low_confidence && (
            <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
              종류를 가리기 특히 어려웠습니다 (1등 {meta.stage2_top_prob?.toFixed(4) ?? "—"} · 문턱{" "}
              {meta.stage2_abstain_threshold?.toFixed(4) ?? "—"}). ⚠️ 이걸 &ldquo;다시 찍어주세요&rdquo; 로 바꾸면 안
              됩니다 — 사진 탓이 아니라 모델 한계라 다시 찍어도 좋아지지 않습니다.
            </p>
          )}
        </article>
      )}

      {/* 접거나 회색으로 숨기지 않습니다 (D-023, 화면 규칙 ④). */}
      <p className="rounded-lg border border-zinc-200 px-4 py-3 text-sm leading-6 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
        {result.disclaimer}
      </p>

      <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h3 className="text-sm font-medium">meta — 이 판정이 어떻게 나왔나</h3>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
          <MetaRow label="서버 소요" value={meta.elapsed_ms != null ? `${meta.elapsed_ms.toFixed(1)}ms` : null} />
          <MetaRow label="mock" value={meta.mock == null ? null : String(meta.mock)} />
          <MetaRow label="1단계 크롭" value={meta.stage1_crop} />
          <MetaRow label="2단계 크롭" value={meta.stage2_crop} />
          <MetaRow
            label="1단계 온도"
            value={meta.stage1_temperature != null ? String(meta.stage1_temperature) : null}
          />
          <MetaRow
            label="네모 출처"
            value={
              meta.box_source === "user"
                ? "user — 그린 네모로 잘랐습니다"
                : meta.box_source === "center"
                  ? "center — 화면 중앙으로 물러섰습니다"
                  : null
            }
          />
          <MetaRow label="계약 버전" value={result.contract_version} />
          <MetaRow label="error" value={meta.error} />
        </dl>

        <div className="mt-4 rounded-lg bg-zinc-50 p-3 text-xs dark:bg-zinc-900">
          <p className="font-medium">가이드 프레임 검사</p>
          {meta.guide ? (
            <p className="mt-1 text-zinc-600 dark:text-zinc-300">
              {meta.guide.ok ? "밴드 안" : `밴드 밖 — ${meta.guide.reason}`} · 가로 {meta.guide.width_frac ?? "—"} ·
              중심 이탈 {meta.guide.center_off ?? "—"}
            </p>
          ) : (
            // ⚠️ 빈칸으로 두면 "ok 인데 안 나온 것" 으로 읽힙니다. `meta.guide` 는
            //    box 를 보냈을 때만 붙습니다.
            <p className="mt-1 text-zinc-500 dark:text-zinc-400">
              가이드 검사 안 함 — 네모를 안 보냈습니다. (검사에 통과한 것이 아닙니다)
            </p>
          )}
        </div>

        {meta.crop_note && <p className="mt-3 text-xs leading-6 text-zinc-500 dark:text-zinc-400">{meta.crop_note}</p>}

        <details className="mt-4">
          <summary className="cursor-pointer text-xs text-zinc-500 hover:underline dark:text-zinc-400">
            전문 보기 (<code>text</code> — 앱은 안 씁니다)
          </summary>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-zinc-700 dark:text-zinc-300">{result.text}</p>
        </details>

        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-zinc-500 hover:underline dark:text-zinc-400">
            원문 JSON
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-zinc-50 p-3 text-[11px] leading-5 dark:bg-zinc-900">
            {JSON.stringify(result, null, 2)}
          </pre>
        </details>
      </article>
    </>
  );
}

function MetaRow({ label, value }: { label: string; value?: string | null }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}
