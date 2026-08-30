import CrawlConsole from "../../components/crawl-console";

/**
 * `수집 / 크롤` — 크롤 실행 이력과 수동 트리거 (RAG-001 요구사항 ②③ · RAG-047).
 *
 * **`기능 / 검색 점검` 에 넣지 않았습니다.** 저쪽은 "두들겨 보고 근거를 확인"하는 읽기
 * 성격이고, 여기는 외부 사이트로 실제 요청을 내보내고 `data/raw/` 를 바꾸는 쓰기입니다.
 * 권한도 다릅니다 — 저쪽은 `read`, 여기 트리거는 `ops:write` 입니다.
 *
 * 서버 컴포넌트입니다. 폴링과 권한별 버튼 노출은 `CrawlConsole`(클라이언트)이 합니다.
 */
export default function ConsoleCrawlPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">수집 / 크롤</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        소스별 마지막 수집 결과입니다. 주기 실행은 Beat 가 하고, 여기서 직접 부를 수도 있습니다.
        <strong className="font-medium"> 수집까지만 합니다</strong> — 적재(임베딩·색인)는 사람이
        따로 판단합니다.
      </p>

      <div className="mt-10">
        <CrawlConsole />
      </div>
    </section>
  );
}
