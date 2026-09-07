import ReportsConsole from "../../components/reports-console";

/**
 * `신고` — 회원이 신고한 AI 답변 (콘솔 로드맵 A1 · D-053 · #237).
 *
 * **#235 가 API 를 냈고, 그전까지 신고를 보는 길은 psql 뿐이었습니다.** 그 앞에는
 * 메일뿐이었고 — 답변 원문만 오고 누가 · 언제 · 무슨 질문에 대한 답인지가 없어서
 * 받아도 무엇을 고쳐야 할지 알 수 없었습니다 (로드맵 §3).
 *
 * **열람 범위는 D-053 이 정했습니다.** 상세를 열면 **신고된 답변 한 건**만 보이고,
 * 그 대화의 다른 부분은 안 열립니다. 대신 "몇 턴짜리 대화의 몇 번째인지" 가 숫자로
 * 붙습니다 — 맥락에 기댄 답변인지 알 수 있고, 나중에 범위를 넓힐지 정할 때 추측이
 * 아니라 실측이 근거가 되게 하려는 값입니다.
 *
 * 서버 컴포넌트입니다. 조회 · 열람 · 처리는 `ReportsConsole`(클라이언트)이 합니다.
 */
export default function ConsoleReportsPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">신고</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        회원이 앱에서 신고한 AI 답변입니다.{" "}
        <strong className="font-medium">신고된 답변 한 건만</strong> 열리고, 그
        대화의 다른 부분은 보이지 않습니다. 여는 것은 감사 기록에 남습니다.
      </p>

      <div className="mt-10">
        <ReportsConsole />
      </div>
    </section>
  );
}
