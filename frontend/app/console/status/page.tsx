import StatusConsole from "../../components/status-console";

/**
 * `상태` — 컨테이너 · 모델 · 예산 · 크롤 (#180 · `docs/console/roadmap.md` B1).
 *
 * **읽기 전용입니다.** 여기서 아무것도 재시작하거나 끄지 않습니다 — 서버 상태를 고치는
 * 것은 사람의 일이고(`docs/collaboration.md` §7 "설정 파일은 AI 가 쓰되 상태는 사람이
 * 안다"), 이 화면은 "지금 무엇을 봐야 하나"만 알려 줍니다.
 *
 * **로컬과 GCP 에 같은 화면이 뜹니다.** 환경마다 있는 것이 달라서 항목이 `absent` 로
 * 빠지는데, 그것을 고장과 갈라 보여 주는 것이 이 화면의 요점입니다 (`status-console.tsx`
 * 의 `STATE` 주석).
 *
 * 서버 컴포넌트입니다. 폴링은 `StatusConsole`(클라이언트)이 합니다 —
 * `수집 / 크롤` 과 같은 구조입니다.
 */
export default function ConsoleStatusPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">상태</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        DB · 임베딩 모델 · 피부 가중치 · Redis 일 예산 · 장소 검색 · 경로 스냅샷 · 크롤 ·
        보행 분석을 한 번에 봅니다. <strong className="font-medium">회색은 고장이 아닙니다</strong> —
        이 배포에 그 서비스가 없다는 뜻입니다.
      </p>

      <div className="mt-10">
        <StatusConsole />
      </div>
    </section>
  );
}
