import AdminAccountsConsole from "../../components/admin-accounts-console";

/**
 * `관리자 계정` — 콘솔 계정 발급 · 정지 · 권한 변경 (콘솔 로드맵 A3 · #207).
 *
 * **`회원 · 반려견`(A2)과 다른 화면입니다.** 저기는 서비스를 쓰는 앱 회원(`app_users`,
 * 카카오 로그인, 개인정보가 암호화되어 있음)이고, 여기는 이 콘솔에 로그인하는 사내
 * 계정(`admin_users`, id/pw)입니다. 한 테이블로 합치지 않은 이유가 `03_auth.sql` 에
 * 적혀 있고, 화면을 나눈 이유도 같습니다.
 *
 * A2 보다 이 화면이 **먼저** 온 이유: `pii:read` 를 못 가진 계정이 실제로 있어야
 * 저쪽의 복호화 가리기가 검증됩니다 (`docs/console/roadmap.md` §5).
 *
 * 서버 컴포넌트입니다. 목록 조회와 쓰기는 `AdminAccountsConsole`(클라이언트)이 합니다.
 */
export default function ConsoleAdminsPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">관리자 계정</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        콘솔에 로그인하는 계정입니다. 권한 등급에 따라 볼 수 있는 메뉴가 달라지고,
        <strong className="font-medium"> 개인정보 복호화 같은 위험한 조회는 등급으로 갈립니다.</strong>
        계정 발급과 권한 변경은 감사 기록에 남습니다.
      </p>

      <div className="mt-10">
        <AdminAccountsConsole />
      </div>
    </section>
  );
}
