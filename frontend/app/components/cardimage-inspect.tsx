"use client";

import { useState } from "react";

import { apiFetch } from "../../lib/api";

/**
 * 「도감 카드 생성」 갈래 — 사진 한 장 + 이름으로 4월 카드를 만들어 본다 (#496).
 *
 * `POST /admin/cardimage/generate`(`search:inspect`). 저장하지 않습니다. 한 번에
 * $0.10~0.20 이 나가서 화면에 그 말을 적습니다. 응답은 base64 PNG 라 그대로
 * `<img>` 에 넣습니다.
 *
 * 부르는 주소가 `/api/admin/cardimage/generate` 인 이유는 다른 패널과 같습니다 —
 * nginx 의 `/api/` 가 접두사를 떼고 backend 로 넘겨서 같은 오리진을 유지합니다
 * (D-015). 몸통은 FormData 가 아니라 **원본 이미지 바이트**입니다 — 이 백엔드는
 * `Content-Type` 헤더로 MIME 을 읽지 multipart 를 안 받습니다(Task 8). `File` 은
 * Blob 이라 `apiFetch` 의 401 재시도가 본문을 두 번 읽어도 안전합니다(`lib/api.ts`).
 */
type Judge = { likeness: number; text_ok: boolean; avatar_ok: boolean; note: string };
type Result = {
  month: number;
  title: string;
  attempts: number;
  judge: Judge | null;
  png_base64: string;
  elapsed_ms: number;
};

function messageOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    if (typeof detail === "string") return detail;
  }
  return `HTTP ${status}`;
}

export default function CardImageInspect() {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  async function run() {
    if (!file || !name.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const q = new URLSearchParams({ month: "4", dog_name: name.trim() });
      const res = await apiFetch(`/api/admin/cardimage/generate?${q}`, {
        method: "POST",
        body: file,
        // MIME 이 비어 있으면 헤더 자체를 안 보냅니다 — 백엔드의 400 bad_mime 이
        // 사용자에게 그 사실을 말해 줍니다.
        headers: file.type ? { "Content-Type": file.type } : undefined,
      });
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        setError(messageOf(body, res.status));
        return;
      }
      setResult((await res.json()) as Result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="cardimage-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-violet-700 dark:text-violet-400">도감 카드 · 사진 1장</p>
          <h2 id="cardimage-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
            도감 카드 생성{" "}
            <code className="text-base font-normal text-zinc-500">POST /admin/cardimage/generate</code>
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            강아지 사진 한 장과 이름을 넣으면 4월 카드(BLOSSOM)를 만듭니다. 정면·귀가 보이는 사진이 잘 됩니다.
            한 번에 <strong className="font-medium">약 $0.10~0.20</strong> 이 나갑니다 (유사도가 모자라면 한 번
            더 만듭니다).
          </p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">사진은 저장하지 않습니다</span>
      </div>

      <div className="mt-6 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm" htmlFor="cardimage-photo">
          사진
          <input
            id="cardimage-photo"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="min-w-0 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:file:bg-zinc-800"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor="cardimage-name">
          강아지 이름
          <input
            id="cardimage-name"
            value={name}
            maxLength={40}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
            placeholder="네오"
            className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-violet-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          />
        </label>
        <button
          type="button"
          onClick={run}
          disabled={busy || !file || !name.trim()}
          className="rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {busy ? "만드는 중… (20~60초)" : "4월 카드 만들기"}
        </button>
      </div>

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
          >
            {error}
          </p>
        )}

        {result && (
          <article className="flex flex-wrap gap-6 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`data:image/png;base64,${result.png_base64}`}
              alt={result.title}
              className="w-72 rounded shadow"
            />
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-zinc-500 dark:text-zinc-400">제목</dt>
              <dd>{result.title}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">시도</dt>
              <dd>
                {result.attempts}회 · {result.elapsed_ms}ms
              </dd>
              <dt className="text-zinc-500 dark:text-zinc-400">유사도</dt>
              <dd>{result.judge ? `${result.judge.likeness}/5` : "검수 없음"}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">글자</dt>
              <dd>{result.judge ? (result.judge.text_ok ? "무사" : "깨짐") : "-"}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">아바타</dt>
              <dd>{result.judge ? (result.judge.avatar_ok ? "같은 개" : "다름") : "-"}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">메모</dt>
              <dd>{result.judge?.note ?? "-"}</dd>
            </dl>
          </article>
        )}
      </div>
    </section>
  );
}
