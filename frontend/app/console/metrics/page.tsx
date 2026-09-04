import MetricsConsole from "../../components/metrics-console";

/**
 * `운영 지표` — 대화가 어떻게 흘렀는지 (콘솔 로드맵 B3 · B4 첫 판 · #223).
 *
 * **원문 없이 세기만 합니다.** D-037 이 관측에 질문 원문을 금지했고, 이 화면의 주장은
 * **숫자만으로도 "사람들이 무엇을 묻나" 가 보인다**는 것입니다. 그 주장이 틀리면 답은
 * 원문을 담는 것이 아니라 다른 값을 세는 것입니다 (`schemas/metrics.py`).
 *
 * **B2 는 아직 없습니다.** 요청 메타데이터(request_id · 능력 · status · elapsed_ms)를
 * 어디에 쌓을지가 사람 결정 대기라(로드맵 §7), 이 화면은 제품 테이블(`chat_*`)만
 * 집계합니다. B2 가 정해지면 같은 화면에 칸을 더합니다.
 *
 * 서버 컴포넌트입니다. 조회와 기간 전환은 `MetricsConsole`(클라이언트)이 합니다.
 */
export default function ConsoleMetricsPage() {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">운영 지표</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500 dark:text-zinc-400">
        대화가 얼마나 오갔고 어떤 갈래로 답했는지 봅니다.{" "}
        <strong className="font-medium">질문과 답변의 내용은 남기지 않습니다</strong> — 세는 것만 합니다.
      </p>

      <div className="mt-10">
        <MetricsConsole />
      </div>
    </section>
  );
}
