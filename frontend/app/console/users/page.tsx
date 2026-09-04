import AppUsersConsole from "../../components/app-users-console";

/**
 * `회원 · 반려견` — 앱 회원을 찾아 상태와 반려견을 봅니다 (콘솔 로드맵 A2 · #211).
 *
 * **`관리자 계정`(#207)과 다른 화면입니다.** 저기는 이 콘솔에 로그인하는 사내 계정
 * (`admin_users`, id/pw)이고, 여기는 앱을 쓰는 회원(`app_users`, 카카오 로그인, 개인정보가
 * 암호화되어 있음)입니다. 한 테이블로 합치지 않은 이유가 `db/init/03_auth.sql` 에 적혀
 * 있고, 화면을 나눈 이유도 같습니다.
 *
 * **이 카드는 읽기만 합니다.** 원문 복호화(`pii:read`)와 상태 변경은 짝 카드(#212)가
 * 감사 기록과 함께 붙입니다 — 남길 가치가 있는 것은 "가려서 봤다"가 아니라 "원문을
 * 열어 봤다"라서, 그 선에서 카드를 갈랐습니다.
 *
 * 서버 컴포넌트입니다. 검색과 상세는 `AppUsersConsole`(클라이언트)이 합니다.
 */
export default function ConsoleUsersPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">회원 · 반려견</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        앱 회원을 이메일이나 카카오 회원번호로 찾습니다.{" "}
        <strong className="font-medium">개인정보는 가려서 보여 줍니다</strong> — 원문은 저장할 때
        암호화되어 있고, 여는 것은 권한이 따로 있는 일입니다.
      </p>

      <div className="mt-10">
        <AppUsersConsole />
      </div>
    </section>
  );
}
