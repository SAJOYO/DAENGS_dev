import OwnPasswordConsole from "../../components/own-password-console";

/**
 * `비밀번호 변경` — 본인 비밀번호를 바꾸는 화면 (콘솔 로드맵 A3-1 · #222).
 *
 * **`관리자 계정`(A3)과 다른 화면입니다.** 저기는 남의 계정을 발급·정지·강등하는
 * 곳이라 `admin:manage`(ADMIN 만)로 잠겨 있고, 여기는 **자기 것만** 바꾸는 곳이라
 * `read` 로 열려 있습니다 — VIEWER 도 들어옵니다. 그래서 이 화면은 대시보드
 * (`console/page.tsx`)의 카드가 아니라 **헤더의 이름 옆**에 있습니다. 카드로 두면
 * "메뉴는 권한으로 갈린다"는 그 화면의 규칙과 어긋납니다.
 *
 * #207 이 계정 발급을 콘솔로 옮기면서 초기 비밀번호를 발급자가 정해 전달하는 것으로
 * 뒀고, 받은 사람이 바꿀 길이 없어 그 값이 팀 채널에 계속 남아 있었습니다.
 * 이 화면이 그 길 하나만 냅니다 — **최초 로그인 시 변경을 강제하지는 않습니다**
 * (2026-09-04 사람 결정. 강제하려면 `admin_users` 에 컬럼이 하나 늘고 두 DB 에
 * 손으로 적용해야 합니다).
 *
 * 서버 컴포넌트입니다. 폼과 요청은 `OwnPasswordConsole`(클라이언트)이 합니다.
 */
export default function ConsolePasswordPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">비밀번호 변경</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        발급받은 초기 비밀번호를 바꿉니다.{" "}
        {/* `{" "}` 가 없으면 문장이 붙습니다 — JSX 는 태그에 붙은 줄바꿈을 지웁니다. */}
        <strong className="font-medium">
          바꾸면 모든 기기에서 로그아웃되고 다시 로그인해야 합니다.
        </strong>{" "}
        바꾼 사실은 감사 기록에 남지만, 비밀번호 자체는 어디에도 남지 않습니다.
      </p>

      <div className="mt-10">
        <OwnPasswordConsole />
      </div>
    </section>
  );
}
