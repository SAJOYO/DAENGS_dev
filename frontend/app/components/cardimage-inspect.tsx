"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../../lib/api";
import { useAuth } from "./auth-provider";

/**
 * 「도감 카드 생성」 갈래 — 사진 한 장 + 이름으로 카드 14종(1~12월 + 딸기·상추)을 엔진을 골라
 * 만들어 보고, 만든 것을 **콘솔 전용 표**에서 다시 보고 지운다 (#496, #592).
 *
 * `GET /admin/cardimage/options` 로 카드 목록·엔진 가능 여부·사진 안내를 받아 그린다
 * (`search:inspect`). **여기에 카드 표나 안내 문구를 복제하지 않는다** — 예전에 `MONTHS` 가
 * 4·9월에 멈춰 있었고 `PHOTO_GUIDANCE` 는 손으로 맞춘 사본이었다.
 *
 * 생성은 `POST /admin/cardimage/generate` 한 번에 **약 $0.10~0.20** 이 나가서 화면에 그 말을
 * 적는다. 응답은 base64 PNG 라 그대로 `<img>` 에 넣는다. 사진 원본은 서버에 남지 않고,
 * 만들어진 카드 PNG 만 표(`admin_ai_cards`)에 남는다.
 *
 * 부르는 주소가 `/api/admin/cardimage/...` 인 이유는 다른 패널과 같습니다 —
 * nginx 의 `/api/` 가 접두사를 떼고 backend 로 넘겨서 같은 오리진을 유지합니다
 * (D-015). 생성 요청의 몸통은 FormData 가 아니라 **원본 이미지 바이트**입니다 — 이 백엔드는
 * `Content-Type` 헤더로 MIME 을 읽지 multipart 를 안 받습니다. `File` 은 Blob 이라
 * `apiFetch` 의 401 재시도가 본문을 두 번 읽어도 안전합니다(`lib/api.ts`).
 *
 * **미리보기는 `<img src="/api/...">` 로 못 그립니다** — 그 경로도 Bearer 인증이 필요한데
 * `<img>` 는 헤더를 못 싣습니다. `apiFetch` 로 받아 `URL.createObjectURL` 로 그리고,
 * 목록을 새로 받거나 화면을 떠날 때 `revokeObjectURL` 로 되돌려 줍니다.
 */
type Judge = { likeness: number; text_ok: boolean; avatar_ok: boolean; note: string };

/** `POST /admin/cardimage/generate` 의 응답 (`schemas/cardimage.py` 의 `CardImageResponse`). */
type Result = {
  /** 저장된 행의 id. **저장소가 꺼져 있으면 `null`** 이고 그때 `stored` 도 거짓입니다. */
  id: string | null;
  /** 달은 `"4"`, 종류는 `"strawberry"` — `month` 정수가 아닙니다 (#592). */
  card: string;
  title: string;
  attempts: number;
  judge: Judge | null;
  png_base64: string;
  elapsed_ms: number;
  engine: string;
  /** **Nano Banana 2 는 seed 를 버리므로 `null`** 입니다. GPU 경로에서만 값이 옵니다. */
  seed: number | null;
  stored: boolean;
};

/** `GET /admin/cardimage/options` (`CardImageOptions`). */
type CardOption = { key: string; label: string };
type EngineOption = { key: string; label: string; available: boolean; reason: string | null };
type Options = { cards: CardOption[]; engines: EngineOption[]; photo_guidance: string };

/** `GET /admin/cardimage/cards` 의 한 줄 (`AdminCardOut`). 이미지 바이트는 안 옵니다. */
type StoredCard = {
  id: string;
  admin_user_id: string;
  card: string;
  dog_name: string;
  title: string;
  engine: string;
  seed: number | null;
  attempts: number;
  likeness: number | null;
  judge_note: string | null;
  width: number;
  height: number;
  size_bytes: number;
  elapsed_ms: number;
  created_at: string;
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

/**
 * JSON 을 받는 요청. `lib/api.ts` 의 `apiJson` 을 안 쓰는 이유는 이 라우터의 `detail` 이
 * 문자열이 아니라 `{code, message}` 객체라서입니다 — `apiJson` 은 그 경우 문구를 버립니다.
 */
async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    throw new Error(messageOf(body, res.status));
  }
  return (await res.json()) as T;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

function revokeAll(urls: Record<string, string>): void {
  for (const url of Object.values(urls)) URL.revokeObjectURL(url);
}

export default function CardImageInspect() {
  const { admin } = useAuth();

  const [options, setOptions] = useState<Options | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [card, setCard] = useState("");
  const [engine, setEngine] = useState("");
  const [seed, setSeed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const [stored, setStored] = useState<StoredCard[] | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [listError, setListError] = useState<string | null>(null);
  const [listing, setListing] = useState(false);
  /** 삭제를 한 번 더 묻는 중인 행. 브라우저 `confirm()` 은 이 저장소에서 쓰지 않습니다. */
  const [asking, setAsking] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`audit-console.tsx` 와 같은 장치).
  const alive = useRef(true);
  // 지금 화면이 들고 있는 objectURL. 목록을 새로 받을 때와 화면을 떠날 때 전부 되돌립니다.
  const thumbsRef = useRef<Record<string, string>>({});

  /**
   * 목록과 미리보기를 다시 받는다.
   *
   * **`setState` 는 전부 `then`/`catch` 콜백 안에 있어야 한다** — 이 함수는 마운트 effect 에서도
   * 불리는데, 본문에 바로 쓰면 "effect 안의 동기 setState" 로 잡힌다
   * (`react-hooks/set-state-in-effect`, `audit-console.tsx` 의 `load` 와 같은 모양).
   * 그래서 「불러오는 중」 표시도 부르는 쪽(`reload`)이 켜고 끈다.
   */
  const refresh = useCallback(
    () =>
      fetchJson<{ cards: StoredCard[] }>("/api/admin/cardimage/cards")
        .then((page) =>
          // 미리보기는 한 장씩 따로 받습니다 — 목록 응답에 바이트가 안 실려 있습니다.
          // 한 장이 실패해도 나머지는 그립니다(`null` 이면 아래에서 "미리보기 없음").
          Promise.all(
            page.cards.map(async (row): Promise<[string, string] | null> => {
              const res = await apiFetch(`/api/admin/cardimage/cards/${row.id}/image`).catch(
                () => null,
              );
              if (!res || !res.ok) return null;
              return [row.id, URL.createObjectURL(await res.blob())];
            }),
          ).then((pairs) => {
            const next: Record<string, string> = {};
            for (const pair of pairs) if (pair) next[pair[0]] = pair[1];
            if (!alive.current) {
              // 화면이 떠난 뒤 도착했습니다. 방금 만든 URL 을 두면 그게 곧 누수입니다.
              revokeAll(next);
              return;
            }
            revokeAll(thumbsRef.current);
            thumbsRef.current = next;
            setThumbs(next);
            setStored(page.cards);
            setListError(null);
          }),
        )
        .catch((e: unknown) => {
          if (alive.current) setListError(e instanceof Error ? e.message : String(e));
        }),
    [],
  );

  async function reload() {
    setListing(true);
    try {
      await refresh();
    } finally {
      if (alive.current) setListing(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    fetchJson<Options>("/api/admin/cardimage/options")
      .then((got) => {
        if (!alive.current) return;
        setOptions(got);
        setCard((prev) => prev || got.cards[0]?.key || "");
        // 기본 엔진은 **지금 부를 수 있는 것** 중 첫째. 전부 막혀 있으면 첫 칸을 그대로 둡니다
        // (그 경우 아래에 이유가 뜨고 만들기 버튼이 잠깁니다).
        setEngine(
          (prev) => prev || got.engines.find((e) => e.available)?.key || got.engines[0]?.key || "",
        );
      })
      .catch((e: unknown) => {
        if (alive.current) setOptionsError(e instanceof Error ? e.message : String(e));
      });
    void refresh();
    return () => {
      alive.current = false;
      revokeAll(thumbsRef.current);
      thumbsRef.current = {};
    };
  }, [refresh]);

  const cardLabel = useCallback(
    (key: string) => options?.cards.find((c) => c.key === key)?.label ?? key,
    [options],
  );
  const engineLabel = useCallback(
    (key: string) => options?.engines.find((e) => e.key === key)?.label ?? key,
    [options],
  );

  const picked = options?.engines.find((e) => e.key === engine) ?? null;
  const engineBlocked = picked !== null && !picked.available;
  const usesSeed = engine === "cardgen";

  async function run() {
    if (!file || !name.trim() || !card || engineBlocked) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const q = new URLSearchParams({ card, dog_name: name.trim(), engine });
      // seed 는 GPU 경로에서만 보냅니다 — Nano Banana 2 에 실어 보내면 서버가 400
      // `seed_not_supported` 로 막습니다(받아 두고 버리면 거짓말이 되니까).
      if (usesSeed && seed.trim()) q.set("seed", seed.trim());
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
      const made = (await res.json()) as Result;
      setResult(made);
      if (made.stored) await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setRemoving(id);
    setListError(null);
    try {
      const res = await apiFetch(`/api/admin/cardimage/cards/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        setListError(messageOf(body, res.status));
        return;
      }
      setAsking(null);
      await refresh();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    } finally {
      setRemoving(null);
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
            강아지 사진 한 장과 이름을 넣으면 고른 카드를 만듭니다. 한 번에{" "}
            <strong className="font-medium">약 $0.10~0.20</strong> 이 나갑니다 (유사도가 모자라면 한 번 더
            만듭니다).
          </p>
        </div>
        {/* 예전에는 "사진은 저장하지 않습니다" 였는데, 이제 만든 카드는 표에 남습니다 (#592). */}
        <span className="text-xs text-zinc-500 dark:text-zinc-400">
          사진 원본은 저장하지 않습니다 · 만든 카드만 남습니다
        </span>
      </div>

      {optionsError && (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
        >
          카드 목록을 불러오지 못했습니다 — {optionsError}
        </p>
      )}

      {options && (
        <p className="mt-4 rounded-lg bg-violet-50 px-4 py-2.5 text-sm leading-6 text-violet-900 dark:bg-violet-950 dark:text-violet-100">
          {options.photo_guidance}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-3">
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
        <label className="flex flex-col gap-1 text-sm" htmlFor="cardimage-card">
          카드
          <select
            id="cardimage-card"
            value={card}
            disabled={busy || !options}
            onChange={(event) => setCard(event.target.value)}
            className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-violet-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          >
            {(options?.cards ?? []).map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor="cardimage-engine">
          엔진
          <select
            id="cardimage-engine"
            value={engine}
            disabled={busy || !options}
            onChange={(event) => {
              setEngine(event.target.value);
              // 엔진을 바꾸면 seed 를 버립니다 — Nano Banana 2 로 옮긴 뒤 남아 있으면
              // 안 보이는 칸의 값이 요청에 실려 400 이 됩니다.
              if (event.target.value !== "cardgen") setSeed("");
            }}
            className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-violet-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          >
            {(options?.engines ?? []).map((e) => (
              // 서버가 못 부른다고 한 엔진은 고를 수 없게 둡니다. 숨기지 않는 이유는
              // "그런 엔진이 없다"와 "지금 꺼져 있다"가 다른 이야기이기 때문입니다.
              <option key={e.key} value={e.key} disabled={!e.available}>
                {e.label}
                {e.available ? "" : " (지금 못 씁니다)"}
              </option>
            ))}
          </select>
        </label>
        {usesSeed && (
          <label className="flex flex-col gap-1 text-sm" htmlFor="cardimage-seed">
            seed
            <input
              id="cardimage-seed"
              type="number"
              min={0}
              step={1}
              value={seed}
              disabled={busy}
              onChange={(event) => setSeed(event.target.value)}
              placeholder="비우면 자동"
              className="w-32 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-violet-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
        )}
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
          disabled={busy || !file || !name.trim() || !card || engineBlocked}
          className="rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {busy ? "만드는 중… (20~60초)" : card ? `${cardLabel(card)} 만들기` : "카드 만들기"}
        </button>
      </div>

      {/* 못 쓰는 엔진의 이유는 서버가 알려 준 문장을 그대로 씁니다 (`/options` 의 `reason`). */}
      {(options?.engines ?? [])
        .filter((e) => !e.available && e.reason)
        .map((e) => (
          <p key={e.key} className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
            {e.label} — {e.reason}
          </p>
        ))}

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
          >
            {error}
          </p>
        )}

        {result && !result.stored && (
          // 저장 실패는 생성을 죽이지 않습니다(spec ④). 대신 **이 응답의 PNG 가 유일한 사본**이라,
          // 화면을 떠나면 사라진다는 것을 말해 줘야 합니다.
          <p
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
          >
            이 카드는 <strong className="font-medium">표에 저장되지 않았습니다</strong> — 아래 PNG 가
            유일한 사본입니다. 화면을 떠나면 사라지니 「PNG 저장」으로 내려받으세요.
          </p>
        )}

        {result && (
          <article className="flex flex-wrap gap-6 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <div className="flex flex-col items-start gap-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`data:image/png;base64,${result.png_base64}`}
                alt={result.title}
                className="w-72 rounded shadow"
              />
              {/* 저장됐든 아니든 원본 994×1582 PNG 를 그대로 내려받을 수 있게 둡니다 —
                  data: URL 을 download 속성으로. */}
              <a
                href={`data:image/png;base64,${result.png_base64}`}
                download={`${result.title.replace(/\s+/g, "_")}.png`}
                className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
              >
                PNG 저장
              </a>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-zinc-500 dark:text-zinc-400">카드</dt>
              <dd>{cardLabel(result.card)}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">제목</dt>
              <dd>{result.title}</dd>
              <dt className="text-zinc-500 dark:text-zinc-400">엔진</dt>
              <dd>
                {engineLabel(result.engine)}
                {/* seed 가 없는 것이 오류가 아닙니다 — Nano Banana 2 는 seed 를 버립니다. */}
                {result.seed !== null && ` · seed ${result.seed}`}
              </dd>
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
              <dt className="text-zinc-500 dark:text-zinc-400">저장</dt>
              <dd>{result.stored ? "표에 남았습니다" : "저장되지 않았습니다"}</dd>
            </dl>
          </article>
        )}
      </div>

      <div className="mt-8 border-t border-zinc-200 pt-6 dark:border-zinc-800">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-lg font-semibold tracking-tight">
            저장된 카드{" "}
            <code className="text-sm font-normal text-zinc-500">GET /admin/cardimage/cards</code>
          </h3>
          <button
            type="button"
            onClick={() => void reload()}
            disabled={listing}
            className="rounded-full border border-zinc-300 px-4 py-1.5 text-sm text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
          >
            {listing ? "불러오는 중…" : "새로 고침"}
          </button>
        </div>
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
          관리자 전원이 만든 카드를 최근 것부터 봅니다. 지우면 되돌릴 수 없습니다.
        </p>

        {listError && (
          <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
            {listError}
          </p>
        )}

        {stored === null ? (
          <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>
        ) : stored.length === 0 ? (
          <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">
            저장된 카드가 없습니다. 위에서 한 장 만들어 보세요.
          </p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[56rem] text-left text-sm">
              <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                <tr>
                  <th className="py-2 pr-4 font-normal">미리보기</th>
                  <th className="py-2 pr-4 font-normal">카드</th>
                  <th className="py-2 pr-4 font-normal">제목</th>
                  <th className="py-2 pr-4 font-normal">엔진</th>
                  <th className="py-2 pr-4 font-normal">닮음</th>
                  <th className="py-2 pr-4 font-normal">만든 사람</th>
                  <th className="py-2 pr-4 font-normal">시각</th>
                  <th className="py-2 font-normal">삭제</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
                {stored.map((row) => {
                  const url = thumbs[row.id];
                  const mine = admin?.admin_id === row.admin_user_id;
                  return (
                    <tr key={row.id}>
                      <td className="py-3 pr-4">
                        {url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={url} alt={row.title} className="w-20 rounded shadow" />
                        ) : (
                          <span className="text-xs text-zinc-400 dark:text-zinc-500">
                            미리보기 없음
                          </span>
                        )}
                      </td>
                      <td className="py-3 pr-4 whitespace-nowrap">{cardLabel(row.card)}</td>
                      <td className="py-3 pr-4">
                        {row.title}
                        <span className="ml-1.5 text-xs text-zinc-500 dark:text-zinc-400">
                          {row.attempts}회 · {row.elapsed_ms}ms
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-xs whitespace-nowrap">
                        {engineLabel(row.engine)}
                        <span className="ml-1.5 text-zinc-500 dark:text-zinc-400">
                          {/* seed 칸을 따로 두지 않고 엔진 옆에 붙입니다 — 값이 있는 것은
                              GPU 경로뿐이라 빈 칸만 늘어납니다. */}
                          {row.seed === null ? "seed 없음" : `seed ${row.seed}`}
                        </span>
                      </td>
                      <td className="py-3 pr-4 text-xs whitespace-nowrap">
                        {row.likeness === null ? "검수 없음" : `${row.likeness}/5`}
                      </td>
                      <td className="py-3 pr-4 text-xs whitespace-nowrap">
                        {mine ? (
                          <span className="text-zinc-600 dark:text-zinc-300">나</span>
                        ) : (
                          // 목록은 관리자 id 만 줍니다(`AdminCardOut`). 이름을 붙이려면 계정
                          // 조회 권한이 따로 필요해서, 여기서는 id 앞자리로 구분만 합니다.
                          <span className="font-mono text-zinc-400 dark:text-zinc-500">
                            {row.admin_user_id.slice(0, 8)}
                          </span>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-xs whitespace-nowrap text-zinc-500 tabular-nums dark:text-zinc-400">
                        {when(row.created_at)}
                      </td>
                      <td className="py-3 text-xs whitespace-nowrap">
                        {asking === row.id ? (
                          <span className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void remove(row.id)}
                              disabled={removing === row.id}
                              className="rounded-full bg-red-600 px-3 py-1 text-white transition-colors hover:bg-red-700 disabled:opacity-50"
                            >
                              {removing === row.id ? "지우는 중…" : "정말 지웁니다"}
                            </button>
                            <button
                              type="button"
                              onClick={() => setAsking(null)}
                              disabled={removing === row.id}
                              className="text-zinc-500 underline disabled:opacity-50 dark:text-zinc-400"
                            >
                              취소
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setAsking(row.id)}
                            className="rounded-full border border-zinc-300 px-3 py-1 text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                          >
                            삭제
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
