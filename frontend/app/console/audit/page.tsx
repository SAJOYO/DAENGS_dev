import AuditConsole from "../../components/audit-console";

/**
 * `감사 로그` — 관리자가 무엇을 했는지 (콘솔 로드맵 A4-1 · #221).
 *
 * **#203 이 테이블을 만들었고 #207 · #212 가 기록을 늘렸는데, 보는 길이 psql 뿐이었습니다.**
 * 로드맵 §1 의 "누가 복호화를 봤나 → 알 수 없다" 는 기록만으로는 반쪽이고, 읽는 화면이
 * 있어야 닫힙니다.
 *
 * **여기 있는 것은 로그가 아니라 데이터입니다.** 운영 로그(에러 · 스택트레이스)는 파일로
 * 가고 콘솔에 띄우지 않기로 했습니다 — 그 선은 `docs/console/roadmap.md` §6 에 있습니다.
 *
 * 서버 컴포넌트입니다. 조회와 페이지 넘김은 `AuditConsole`(클라이언트)이 합니다.
 */
export default function ConsoleAuditPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">감사 로그</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        관리자가 한 일이 남습니다 — 로그인 시도, 계정 발급과 권한 변경,{" "}
        <strong className="font-medium">회원 개인정보 원문 조회</strong>.
        값은 남기지 않고 &ldquo;누가 · 언제 · 무엇을&rdquo; 까지만 남습니다.
      </p>

      <div className="mt-10">
        <AuditConsole />
      </div>
    </section>
  );
}
