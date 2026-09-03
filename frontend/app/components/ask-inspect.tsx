"use client";

import { FormEvent, useState } from "react";

import { ApiError, apiJson } from "@/lib/api";
import type { AskHit, AskResponse } from "@/lib/life-rag";

/**
 * `POST /life/ask` 점검 패널.
 *
 * **응답을 줄이지 않고 그대로 그립니다.** `/life/ask` 가 근거를 통째로 싣는 이유가
 * *"인용한 조항이 실제로 컨텍스트에 있었나"* 를 보기 위해서인데(RAG-028 ②), 지금은 그것을
 * JSON 으로만 볼 수 있습니다. **`ungrounded` 배지가 검문소④ 그 자체입니다** — 0 이 아니면
 * 모델이 컨텍스트에 없는 조항을 지어낸 것입니다.
 *
 * **이 패널이 전문을 받는 유일한 소비자입니다.** 사용자에게 닿는 길은 `/assistant/query` 하나이고
 * 거기서 어댑터가 응답을 줄이므로, 여기가 없으면 근거 전문을 볼 곳이 사라집니다. A4(#176)가
 * 경로만 바꾸고 응답은 그대로 둔 이유입니다.
 *
 * ⚠️ **경로가 A4(#176)로 `/ask` → `/life/ask` 가 됐고 리다이렉트를 두지 않았습니다.** 프론트와
 * 백엔드가 같은 배포에 같이 나가야 하고, 한쪽만 먼저 나가면 아래 "라우트 없음" 404 를 받습니다 —
 * 서버가 주는 "근거 0건" 404 와 본문이 달라서 화면에서 구분합니다.
 */

/** 서버가 근거 0건일 때 주는 문구 (`services/ask.py`). 라우트 없음 404 와 가르는 표시입니다. */
const NO_EVIDENCE = "근거를 찾지 못했다";

type Outcome =
  | { kind: "answer"; data: AskResponse }
  /** 404 인데 서버가 준 것 — 실패가 아니라 **관찰 결과**입니다. */
  | { kind: "no-evidence" }
  | { kind: "error"; message: string; hint?: string };

function outcomeOf(caught: unknown): Outcome {
  if (caught instanceof ApiError) {
    if (caught.status === 404) {
      // 서버의 "근거 0건" 404 는 detail 문구로 알아봅니다. 라우트가 아예 없으면
      // FastAPI 가 "Not Found" 를 주므로 여기 안 걸립니다.
      if (caught.message.includes(NO_EVIDENCE)) return { kind: "no-evidence" };
      return {
        kind: "error",
        message: "이 서버에 /life/ask 라우트가 없습니다.",
        hint: "프론트만 먼저 배포됐을 수 있습니다 — 경로가 #176 으로 /ask → /life/ask 로 바뀌었고 리다이렉트가 없습니다. 붙고 나면 같은 404 라도 본문이 달라집니다.",
      };
    }
    if (caught.status === 401) return { kind: "error", message: "세션이 만료되었습니다. 다시 로그인해 주세요." };
    if (caught.status === 403) return { kind: "error", message: "이 계정으로는 부를 수 없는 API 입니다." };
    if (caught.status === 503) {
      return {
        kind: "error",
        message: caught.message,
        hint: "설정 문제입니다 — 임베딩 모델이 안 올라왔거나 GEMINI_API_KEY 가 없습니다. 요청을 바꿔도 결과가 같습니다.",
      };
    }
    if (caught.status === 502) {
      return {
        kind: "error",
        message: caught.message,
        hint: "상류가 죽은 것입니다. 메시지 앞의 예외 이름으로 DB(검색)인지 Gemini(생성)인지 갈립니다.",
      };
    }
    return { kind: "error", message: caught.message };
  }
  if (caught instanceof DOMException && caught.name === "AbortError") {
    return { kind: "error", message: "50초 안에 응답이 오지 않았습니다." };
  }
  return { kind: "error", message: "질의응답 API 에 연결할 수 없습니다." };
}

export default function AskInspect() {
  const [question, setQuestion] = useState("");
  const [k, setK] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || isLoading) return;

    setIsLoading(true);
    setOutcome(null);
    setElapsedMs(null);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 50_000);
    const startedAt = performance.now();

    try {
      const parsedK = Number.parseInt(k, 10);
      const data = await apiJson<AskResponse>("/api/life/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // `k` 를 비우면 **보내지 않습니다.** null 은 "서버 기본값을 쓴다"는 뜻이고,
        // 그 기본값은 프론트가 아니라 `services/ask.py` 가 가집니다 (RAG-026 ①).
        body: JSON.stringify(
          Number.isFinite(parsedK)
            ? { question: normalizedQuestion, k: parsedK }
            : { question: normalizedQuestion },
        ),
        signal: controller.signal,
      });
      setOutcome({ kind: "answer", data });
    } catch (caught) {
      setOutcome(outcomeOf(caught));
    } finally {
      setElapsedMs(Math.round(performance.now() - startedAt));
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  return (
    <section
      aria-labelledby="ask-inspect-title"
      className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-indigo-700 dark:text-indigo-400">생활 RAG · 제도·문서</p>
          <h2 id="ask-inspect-title" className="mt-1 text-2xl font-semibold tracking-tight">
            질의응답 <code className="text-base font-normal text-zinc-500">POST /life/ask</code>
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            동물보호법·가축전염병예방법 등에서 근거를 찾아 답합니다. 무엇을 근거로 줬는지 전문까지 함께 봅니다.
          </p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">근거가 없으면 답을 만들지 않습니다</span>
      </div>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-3 sm:flex-row">
        <label className="sr-only" htmlFor="ask-question">
          질문
        </label>
        <input
          id="ask-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={isLoading}
          maxLength={500}
          placeholder="예: 목줄 안 하면 과태료 얼마인가요?"
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-indigo-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <label className="sr-only" htmlFor="ask-k">
          근거 개수
        </label>
        <input
          id="ask-k"
          value={k}
          onChange={(event) => setK(event.target.value)}
          disabled={isLoading}
          inputMode="numeric"
          placeholder="k=5"
          title="근거 개수 (1~20). 비우면 서버 기본값을 씁니다."
          className="w-full rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-indigo-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 sm:w-24 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button
          type="submit"
          disabled={isLoading || !question.trim()}
          className="rounded-lg bg-zinc-900 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {isLoading ? "찾는 중…" : "질문 보내기"}
        </button>
      </form>

      <div aria-live="polite" className="mt-5 flex flex-col gap-4">
        {isLoading && (
          <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            질문을 벡터로 바꿔 검색하고, 그 근거 위에서 답을 만들고 있습니다.
          </p>
        )}

        {outcome?.kind === "error" && (
          <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
            <p>{outcome.message}</p>
            {outcome.hint && <p className="mt-1 text-xs opacity-80">{outcome.hint}</p>}
          </div>
        )}

        {outcome?.kind === "no-evidence" && (
          <div className="rounded-lg border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800">
            <p className="font-medium">근거 0건 — 답을 만들지 않았습니다</p>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              실패가 아니라 설계입니다. 컨텍스트가 빈 채로 생성하면 그건 검색 결과 위의 답이 아니라 모델의 기억이고,
              &ldquo;출처 링크 + 조항 번호&rdquo; 가 성립할 수 없습니다. 코퍼스에 없는 주제이거나, 아직 적재가 안 된 상태입니다.
            </p>
          </div>
        )}

        {outcome?.kind === "answer" && (
          <>
            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <p className="whitespace-pre-wrap text-sm leading-7 text-zinc-800 dark:text-zinc-100">
                {outcome.data.answer}
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                {outcome.data.cited
                  .filter((article) => !outcome.data.ungrounded.includes(article))
                  .map((article) => (
                    <span key={article} className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                      {article}
                    </span>
                  ))}
                {outcome.data.ungrounded.map((article) => (
                  <span
                    key={article}
                    title="컨텍스트에 없는 조항입니다 — 지어낸 것입니다 (검문소④)"
                    className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-900 dark:bg-red-950 dark:text-red-100"
                  >
                    {article} · 근거 없음
                  </span>
                ))}
                {outcome.data.cited.length === 0 && (
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">인용한 조항 없음</span>
                )}
              </div>

              <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
                생성 {outcome.data.model} · 검색 {outcome.data.embedding_model}
                {elapsedMs !== null && ` · ${(elapsedMs / 1000).toFixed(1)}초`}
              </p>
            </article>

            <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
              <h3 className="text-sm font-medium">근거 {outcome.data.hits.length}건</h3>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                점수는 <strong className="font-medium">코사인 유사도</strong>지 신뢰도가 아닙니다. 0.6 이어도 엉뚱한 문서일 수 있습니다.
              </p>
              <ul className="mt-3 flex flex-col gap-3">
                {outcome.data.hits.map((hit) => (
                  <HitCard key={hit.chunk_id} hit={hit} />
                ))}
              </ul>
            </article>
          </>
        )}
      </div>
    </section>
  );
}

function HitCard({ hit }: { hit: AskHit }) {
  return (
    <li className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs text-zinc-500 dark:text-zinc-400">[{hit.rank}]</span>
        {hit.citation_url ? (
          <a
            href={hit.citation_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium underline underline-offset-2"
          >
            {hit.citation}
          </a>
        ) : (
          <span className="text-sm font-medium">{hit.citation}</span>
        )}
        {hit.part === "supplementary" && (
          <span className="rounded-full bg-zinc-200 px-2 py-0.5 text-[10px] text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
            부칙
          </span>
        )}
        <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">{hit.score.toFixed(3)}</span>
      </div>

      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        {hit.document_title}
        {hit.section && ` · ${hit.section}`}
      </p>

      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-zinc-500 hover:underline dark:text-zinc-400">
          본문 보기 · <code>{hit.chunk_id}</code>
        </summary>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-zinc-700 dark:text-zinc-300">{hit.content}</p>
      </details>
    </li>
  );
}
