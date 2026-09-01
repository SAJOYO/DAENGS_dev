"use client";

import { useState } from "react";

import AskInspect from "./ask-inspect";
import TrainingChat from "./training-chat";
import WalkInspect from "./walk-inspect";
import { useAuth } from "./auth-provider";

/**
 * `기능 / 검색 점검` 안의 갈래 전환.
 *
 * **훈련 갈래만 권한으로 가립니다.** `/training/chat` 은 `Perm.SEARCH_INSPECT`(관리자
 * 전용)이고 `/ask`·`/walk` 은 `Perm.READ` 라, 두 갈래의 문턱이 다릅니다. 화면에서 가리는
 * 것은 UX 일 뿐이고 실제 차단은 백엔드가 같은 권한으로 합니다 (`lib/auth.ts`).
 *
 * **기본 갈래는 훈련 RAG 입니다** — 이 화면을 쓰던 사람의 흐름을 바꾸지 않으려는 것뿐이고,
 * 권한이 없으면 생활 RAG 가 기본이 됩니다.
 *
 * 갈래를 URL 에 안 싣습니다(`?tab=`). 새로고침하면 첫 갈래로 돌아옵니다 — 공유할 일이
 * 생기면 그때 넣습니다.
 */
type TabId = "training" | "life";

export default function InspectTabs() {
  const { can } = useAuth();
  const canInspectTraining = can("search:inspect");
  const [tab, setTab] = useState<TabId>(canInspectTraining ? "training" : "life");

  const tabs: Array<{ id: TabId; label: string; hint: string }> = [
    ...(canInspectTraining
      ? [{ id: "training" as const, label: "훈련 RAG", hint: "검수된 훈련 문서" }]
      : []),
    { id: "life", label: "생활 RAG", hint: "제도·문서 + 실시간 산책" },
  ];

  // 권한이 사라진 상태로 훈련 갈래가 선택돼 있을 수 없게 합니다(로그아웃 후 재로그인 등).
  const current: TabId = tab === "training" && !canInspectTraining ? "life" : tab;

  return (
    <div>
      <div role="tablist" aria-label="점검 갈래" className="flex flex-wrap gap-2">
        {tabs.map((item) => {
          const selected = item.id === current;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`inspect-panel-${item.id}`}
              id={`inspect-tab-${item.id}`}
              onClick={() => setTab(item.id)}
              className={
                selected
                  ? "rounded-full bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
                  : "rounded-full border border-zinc-300 px-4 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
              }
            >
              {item.label}
              <span className="ml-2 hidden text-xs opacity-70 sm:inline">{item.hint}</span>
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`inspect-panel-${current}`}
        aria-labelledby={`inspect-tab-${current}`}
        className="mt-6"
      >
        {current === "training" ? (
          <TrainingChat />
        ) : (
          <div className="flex flex-col gap-6">
            <AskInspect />
            <WalkInspect />
          </div>
        )}
      </div>
    </div>
  );
}
