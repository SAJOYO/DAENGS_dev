"use client";

import { FormEvent, useState } from "react";

type Decision = "ANSWER" | "UNCERTAIN" | "MEDICAL_REFUSAL";

type TrainingChatResponse = {
  decision: Decision;
  answer: string;
  citations: Array<{ rank: number; label: string }>;
};

const DECISION_LABEL: Record<Decision, string> = {
  ANSWER: "훈련 근거 기반 안내",
  UNCERTAIN: "현재 자료 범위 안내",
  MEDICAL_REFUSAL: "수의학 상담 안내",
};

function fallbackMessage(status: number | undefined): string {
  if (status === 401) return "로그인 후 훈련 도우미를 이용할 수 있습니다.";
  if (status === 504) return "응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.";
  return "훈련 도우미에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.";
}

export default function TrainingChat() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<TrainingChatResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || isLoading) return;

    setIsLoading(true);
    setError(null);
    setResult(null);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 50_000);

    try {
      const response = await fetch("/api/training/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: normalizedQuestion }),
        signal: controller.signal,
      });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const detail =
          body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
            ? body.detail
            : fallbackMessage(response.status);
        throw new Error(detail);
      }
      setResult(body as TrainingChatResponse);
    } catch (caught) {
      setError(caught instanceof Error && caught.name !== "AbortError" ? caught.message : fallbackMessage(504));
    } finally {
      window.clearTimeout(timeout);
      setIsLoading(false);
    }
  }

  return (
    <section aria-labelledby="training-chat-title" className="rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">훈련 도우미 데모</p>
          <h2 id="training-chat-title" className="mt-1 text-2xl font-semibold tracking-tight">강아지 훈련 질문</h2>
          <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">배변·산책·입질·켄넬 훈련처럼 검수된 범위의 질문에 근거를 바탕으로 답합니다.</p>
        </div>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">의료 판단과 처방은 안내하지 않습니다.</span>
      </div>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-3 sm:flex-row">
        <label className="sr-only" htmlFor="training-question">훈련 질문</label>
        <input
          id="training-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={isLoading}
          maxLength={1000}
          placeholder="예: 강아지가 손을 물 때 어떻게 가르쳐야 하나요?"
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-4 py-3 text-sm outline-none ring-emerald-500 focus:ring-2 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button
          type="submit"
          disabled={isLoading || !question.trim()}
          className="rounded-lg bg-zinc-900 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-400 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {isLoading ? "답변을 준비하고 있어요…" : "질문 보내기"}
        </button>
      </form>

      <div aria-live="polite" className="mt-5">
        {isLoading && <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">검색 근거를 확인하고 답변을 만들고 있습니다. 중복 전송은 잠시 막아둘게요.</p>}
        {error && <p role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">{error}</p>}
        {result && (
          <article className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{DECISION_LABEL[result.decision]}</p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-zinc-800 dark:text-zinc-100">{result.answer}</p>
            {result.citations.length > 0 && (
              <ol className="mt-4 flex flex-wrap gap-2" aria-label="답변 근거">
                {result.citations.map((citation) => (
                  <li key={`${citation.rank}-${citation.label}`} className="rounded-full bg-zinc-100 px-3 py-1 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
                    근거 {citation.rank}: {citation.label}
                  </li>
                ))}
              </ol>
            )}
          </article>
        )}
      </div>
    </section>
  );
}
