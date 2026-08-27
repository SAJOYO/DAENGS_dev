import TrainingChat from "../../components/training-chat";

/**
 * `검색 점검` — 콘솔 메뉴의 두 번째 카드가 여는 화면.
 *
 * **지금은 훈련 RAG 한 갈래뿐입니다.** 메뉴 카드가 약속한 "벡터·그래프 검색 경로 비교"는
 * 아직 없습니다. 이 화면에 있던 챗봇이 원래 `/` 랜딩에 붙어 있었는데(`#25`), 그때는
 * 콘솔이 없어서 임시로 거기 둔 것이었습니다.
 *
 * 서버 컴포넌트입니다 — 이 파일 자체는 상태도 fetch 도 없습니다. 로그인 여부는
 * `proxy.ts` 와 `console/layout.tsx` 의 `AuthProvider` 가 이미 봤습니다.
 */
export default function ConsoleSearchPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">검색 점검</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        검수된 훈련 문서로 답을 만들고, 어떤 근거를 집어 왔는지 함께 보여 줍니다.
        벡터·그래프 검색 경로를 나란히 비교하는 화면은 아직 없습니다.
      </p>

      <div className="mt-10">
        <TrainingChat />
      </div>
    </section>
  );
}
