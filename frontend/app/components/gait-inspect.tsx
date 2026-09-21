"use client";

import { useCallback, useState } from "react";

/**
 * 「보행 분석」 갈래 — 영상 하나를 올려 **판정이 되나 안 되나**만 봅니다 (로드맵 C4).
 *
 * 피부 스크리닝 갈래(`skin-inspect.tsx`)처럼 한 번 POST 하고 끝나지 **않습니다.**
 * 보행은 구조가 달라서 그렇게 만들 수가 없습니다 —
 *
 *   · 피부: `/screen/v1/screen` 이 무인증 + 요청 안에서 바로 판정 (사진 한 장 0.6~3초)
 *   · 보행: `/app/gait/*` 만 있고(옛 무인증 `/gait/*` 는 410 Gone), 분석은 Celery
 *     워커가 나중에 합니다 (`gait.analyze`, 37초 영상에 CPU 약 2분). 엔진은
 *     워커에만 깔려 있습니다.
 *
 * 그래서 이 패널은 앱과 **똑같은 네 걸음**을 밟고 폴링만 대신 합니다:
 *
 *     analyze(티켓) → PUT 업로드 → confirm → records/{id} 를 3초마다
 *
 * ⚠️ **`lib/api.ts` 의 `apiJson`·`apiFetch` 를 쓰지 않습니다.** 그쪽은 401 을 받으면
 *    **관리자 세션**을 재발급하고, 실패하면 `onSessionExpired()` 로 로그인 화면에
 *    던집니다. 여기서 401 이 나는 이유는 아래 토큰 칸에 넣은 **회원 토큰**이 만료된
 *    것뿐인데, 그걸 관리자 세션 만료로 다루면 **영상 한 번 올려 보려다 콘솔에서
 *    로그아웃됩니다.** 두 자격 증명이 섞이면 안 되는 자리라 맨 `fetch` 를 씁니다.
 *
 * ⚠️ **회원 토큰이 필요합니다.** `/app/gait/*` 는 `current_app_user` 라 관리자 토큰을
 *    일부러 막습니다(`core/deps.py` — "저쪽이 앱 회원 토큰을 막듯이, 여기서는
 *    관리자 토큰을 막습니다"). 로드맵 C4 가 ⏸ 인 이유가 이것이고, 지금은 점검용
 *    계정으로 앱에서 한 번 로그인해 받은 access token 을 손으로 넣습니다.
 *    **저장하지 않습니다** — 새로고침하면 지워집니다. 남의 계정 토큰을 넣으면 그
 *    사람의 영상을 올리는 것이니 점검용 계정 것만 넣으세요.
 *
 * 화면을 권한으로 가리지 않습니다. 백엔드의 문턱은 `search:inspect` 가 아니라 위의
 * 회원 토큰이라, 탭만 감추면 막히는 것 없이 "보이는 사람/안 보이는 사람" 만 생깁니다
 * (`inspect-tabs.tsx` 가 피부 갈래에서 같은 이야기를 합니다).
 */

type Pet = { id: string; name: string; breed: string };
type PetList = { pets: Pet[] };

type Ticket = {
  record_id: string;
  status: string;
  upload_url: string;
  upload_headers: Record<string, string>;
  expires_in_seconds: number;
};

type Detail = {
  record_id: string;
  status: string;
  quality_status: string | null;
  quality_tier: string | null;
  failure_reason: string | null;
  quality: Record<string, unknown> | null;
  summary_for_ui: Record<string, unknown> | null;
  video_meta: Record<string, unknown> | null;
  source_file: string | null;
};

/** 3초마다 묻고, 10분까지만 봅니다. 워커가 그보다 오래 걸리면 화면이 손을 듭니다. */
const POLL_MS = 3_000;
const BUDGET_MS = 10 * 60_000;

const STATUS_LABEL: Record<string, string> = {
  PENDING: "업로드 기다리는 중",
  UPLOADED: "큐에 들어감",
  PROCESSING: "분석 중",
  DONE: "끝남",
  FAILED: "실패",
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 티켓의 `upload_url` 을 **같은 오리진**으로 바꿉니다.
 *
 * 티켓은 `GAIT_BRIDGE_BASE_URL` 로 만들어져서 **앱이 보는 주소**(`daengback.~`)가
 * 박혀 옵니다. 앱은 그걸 그대로 쓰면 되지만 브라우저는 다릅니다 — 콘솔 오리진에서
 * 그 주소로 `PUT` 하면 preflight 가 뜨고, 버킷도 아닌 우리 backend 의 CORS 허용
 * 목록에 콘솔이 들어 있어야 합니다. 경로만 떼어 `/api/` 뒤에 붙이면 다른 패널과
 * 똑같이 같은 오리진이라 그 이야기가 통째로 사라집니다 (D-015).
 *
 * 경로가 이미 상대 주소면 그대로 씁니다.
 */
export function sameOriginUpload(uploadUrl: string): string {
  let path = uploadUrl;
  if (/^https?:\/\//i.test(uploadUrl)) {
    const parsed = new URL(uploadUrl);
    path = `${parsed.pathname}${parsed.search}`;
  }
  return path.startsWith("/api/") ? path : `/api${path.startsWith("/") ? "" : "/"}${path}`;
}

/** 서버가 준 `detail` 을 문구로. 없으면 상태 코드라도 보여 줍니다. */
function messageOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  if (status === 401) return "회원 토큰이 없거나 만료됐습니다 (관리자 토큰은 안 됩니다).";
  if (status === 403) return "이 토큰의 계정이 가진 강아지가 아닙니다.";
  if (status === 413) return "영상이 너무 큽니다.";
  if (status === 503) return "서버에 보행 저장소가 설정돼 있지 않습니다 (GAIT_STORAGE).";
  return `HTTP ${status}`;
}

type Verdict = {
  tone: "ok" | "unavailable" | "failed";
  headline: string;
  note: string;
};

/**
 * 끝난 기록 하나를 **판정 됨 / 안 됨** 으로.
 *
 * ⚠️ `FAILED` 와 `quality_status = "unavailable"` 은 다른 것입니다 —
 * `services/gait.py` 머리말이 *"FAILED(재시도)와 unavailable(재촬영)을 섞지 않는다"*
 * 라고 못박아 둔 축입니다. 화면에서 둘을 한 덩어리로 보여 주면 **다시 찍어야 할
 * 영상과 서버가 삐끗한 것**이 구별되지 않습니다.
 */
export function verdictOf(detail: Detail): Verdict {
  if (detail.status === "FAILED") {
    return {
      tone: "failed",
      headline: "분석 실패",
      note: detail.failure_reason ?? "서버가 이유를 남기지 않았습니다.",
    };
  }
  if (detail.quality_status === "unavailable") {
    return {
      tone: "unavailable",
      headline: "판정 안 됨 (재촬영)",
      note: "분석은 끝났지만 영상에서 쓸 만한 걸음을 못 찾았습니다.",
    };
  }
  return {
    tone: "ok",
    headline: "판정 됨",
    note: detail.quality_tier ? `품질 ${detail.quality_tier}` : "품질 등급 없음",
  };
}

const TONE_STYLE: Record<Verdict["tone"], string> = {
  ok: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100",
  unavailable:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100",
  failed: "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100",
};

export default function GaitInspect() {
  const [token, setToken] = useState("");
  const [pets, setPets] = useState<Pet[] | null>(null);
  const [petId, setPetId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [steps, setSteps] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Detail | null>(null);

  const log = useCallback((line: string) => {
    const at = new Date().toLocaleTimeString("ko-KR", { hour12: false });
    setSteps((prev) => [...prev, `${at} · ${line}`]);
  }, []);

  /** 회원 토큰으로 부르는 요청. 위 주석대로 관리자 세션을 건드리지 않습니다. */
  const call = useCallback(
    async (path: string, init?: RequestInit): Promise<Response> =>
      fetch(path, {
        ...init,
        headers: { ...(init?.headers ?? {}), Authorization: `Bearer ${token.trim()}` },
      }),
    [token],
  );

  async function loadPets() {
    setError(null);
    setPets(null);
    setPetId("");
    try {
      const res = await call("/api/app/pets");
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setError(messageOf(body, res.status));
        return;
      }
      const list = (body as PetList).pets ?? [];
      setPets(list);
      if (list.length > 0) setPetId(list[0].id);
      if (list.length === 0) setError("이 계정에 등록된 강아지가 없습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function run() {
    if (!file || !petId) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setSteps([]);
    const startedAt = Date.now();
    try {
      // ① 기록 생성 + 업로드 티켓
      log("analyze — 기록 만들고 업로드 자리 받기");
      const ticketRes = await call("/api/app/gait/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pet_id: petId,
          source_file: file.name,
          content_type: file.type || "video/mp4",
        }),
      });
      const ticketBody: unknown = await ticketRes.json().catch(() => null);
      if (!ticketRes.ok) {
        setError(messageOf(ticketBody, ticketRes.status));
        return;
      }
      const ticket = ticketBody as Ticket;
      log(`record_id ${ticket.record_id}`);

      // ② 영상은 backend 를 거쳐 bridge 로. 여기가 오래 걸립니다.
      log(`업로드 — ${(file.size / 1_000_000).toFixed(1)}MB`);
      const putRes = await fetch(sameOriginUpload(ticket.upload_url), {
        method: "PUT",
        headers: ticket.upload_headers,
        body: file,
      });
      if (!putRes.ok) {
        const putBody: unknown = await putRes.json().catch(() => null);
        setError(messageOf(putBody, putRes.status));
        return;
      }

      // ③ 올렸다고 신고해야 큐가 발행됩니다.
      log("confirm — 분석 큐에 넣기");
      const confirmRes = await call(`/api/app/gait/records/${ticket.record_id}/confirm`, {
        method: "POST",
      });
      if (!confirmRes.ok) {
        const confirmBody: unknown = await confirmRes.json().catch(() => null);
        setError(messageOf(confirmBody, confirmRes.status));
        return;
      }

      // ④ 끝날 때까지 묻기
      let last = "";
      while (Date.now() - startedAt < BUDGET_MS) {
        await sleep(POLL_MS);
        const res = await call(`/api/app/gait/records/${ticket.record_id}`);
        const body: unknown = await res.json().catch(() => null);
        if (!res.ok) {
          setError(messageOf(body, res.status));
          return;
        }
        const detail = body as Detail;
        if (detail.status !== last) {
          last = detail.status;
          const secs = Math.round((Date.now() - startedAt) / 1000);
          log(`${secs}초 · ${STATUS_LABEL[detail.status] ?? detail.status}`);
        }
        if (detail.status === "DONE" || detail.status === "FAILED") {
          setResult(detail);
          return;
        }
      }
      setError("10분 안에 안 끝났습니다. 워커가 살아 있는지 보세요 (기록은 남아 있습니다).");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const verdict = result ? verdictOf(result) : null;

  return (
    <section
      aria-labelledby="gait-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div>
        <p className="text-sm font-medium text-sky-700 dark:text-sky-400">보행 분석 · 영상 1개</p>
        <h2 id="gait-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
          보행 분석{" "}
          <code className="text-base font-normal text-zinc-500">POST /app/gait/analyze</code>
        </h2>
        <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
          영상을 올려 <strong className="font-medium">판정이 되나 안 되나</strong>만 봅니다. 앱과 같은
          경로(티켓 → 업로드 → confirm → 조회)를 그대로 밟고, 분석은 워커가 하므로{" "}
          <strong className="font-medium">몇 분 걸립니다</strong> (37초 영상에 약 2분). 기록은{" "}
          <strong className="font-medium">지워지지 않고 그 계정에 남습니다.</strong>
        </p>
      </div>

      <label className="mt-4 flex flex-col gap-1 text-sm" htmlFor="gait-token">
        점검용 회원 access token
        <input
          id="gait-token"
          type="password"
          value={token}
          disabled={busy}
          onChange={(event) => setToken(event.target.value)}
          placeholder="점검용 계정으로 앱에서 로그인해 받은 토큰"
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 font-mono text-sm outline-none ring-sky-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <span className="text-xs text-zinc-500 dark:text-zinc-400">
          관리자 토큰은 이 API 가 일부러 막습니다. 저장하지 않으니 새로고침하면 다시 넣어야 합니다.
        </span>
      </label>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <button
          type="button"
          onClick={loadPets}
          disabled={busy || token.trim().length === 0}
          className="rounded-lg border border-zinc-300 px-4 py-2.5 text-sm transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          강아지 불러오기
        </button>
        {pets && pets.length > 0 && (
          <label className="flex flex-col gap-1 text-sm" htmlFor="gait-pet">
            강아지
            <select
              id="gait-pet"
              value={petId}
              disabled={busy}
              onChange={(event) => setPetId(event.target.value)}
              className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none ring-sky-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
            >
              {pets.map((pet) => (
                <option key={pet.id} value={pet.id}>
                  {pet.name} · {pet.breed}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1 text-sm" htmlFor="gait-video">
          영상
          <input
            id="gait-video"
            type="file"
            accept="video/mp4,video/quicktime"
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="min-w-0 rounded-lg border border-zinc-300 bg-white px-4 py-2.5 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:file:bg-zinc-800"
          />
        </label>
        <button
          type="button"
          onClick={run}
          disabled={busy || !file || !petId}
          className="rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {busy ? "분석 기다리는 중…" : "올려서 판정 보기"}
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

        {steps.length > 0 && (
          <ol className="rounded-lg bg-zinc-50 px-4 py-3 font-mono text-xs leading-6 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        )}

        {verdict && result && (
          <div className={`rounded-lg border px-4 py-3 ${TONE_STYLE[verdict.tone]}`}>
            <p className="text-lg font-semibold">{verdict.headline}</p>
            <p className="mt-1 text-sm leading-6">{verdict.note}</p>
            <p className="mt-1 text-xs opacity-80">
              status {result.status}
              {result.quality_status ? ` · quality_status ${result.quality_status}` : ""}
            </p>
          </div>
        )}

        {result && (
          <details className="rounded-lg border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800">
            <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">응답 원문</summary>
            <pre className="mt-3 overflow-x-auto text-xs leading-5">
              {JSON.stringify(result, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </section>
  );
}
