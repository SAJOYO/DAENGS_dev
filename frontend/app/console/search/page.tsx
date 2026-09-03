import InspectTabs from "../../components/inspect-tabs";

/**
 * `기능 / 검색 점검` — 콘솔 메뉴의 두 번째 카드가 여는 화면.
 *
 * **갈래가 셋입니다.** 훈련 RAG(`/training/chat`, `#25`) · 생활 RAG(`/ask`·`/walk`) ·
 * 피부 스크리닝(`/screen/v1/screen`).
 * 원래는 훈련 RAG 하나뿐이었고, 그 챗봇도 처음에는 `/` 랜딩에 붙어 있었습니다 — 그때는
 * 콘솔이 없어서 임시로 거기 둔 것이었습니다.
 *
 * 서버 컴포넌트입니다 — 이 파일 자체는 상태도 fetch 도 없습니다. 갈래 전환과 권한별
 * 노출은 `InspectTabs`(클라이언트)가 합니다. 로그인 여부는 `proxy.ts` 와
 * `console/layout.tsx` 의 `AuthProvider` 가 이미 봤습니다.
 *
 * **라우트를 늘리지 않았습니다.** 갈래마다 경로를 파면 `console/page.tsx` 의
 * `href` 리터럴 유니온(typedRoutes 검사용)까지 같이 늘려야 합니다. 지금은 화면 상태로
 * 충분하고, 갈래가 더 늘면 그때 라우트로 가릅니다.
 */
export default function ConsoleSearchPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">기능 / 검색 점검</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        각 기능을 실제로 불러 보고, 무엇을 근거로 답했는지 함께 봅니다. 사용자 화면이 아니라
        <strong className="font-medium"> 점검 도구</strong>라 응답을 줄이지 않고 그대로 보여 줍니다.
      </p>

      <div className="mt-10">
        <InspectTabs />
      </div>
    </section>
  );
}
