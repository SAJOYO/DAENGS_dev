import Link from "next/link";

const documents = [
  {
    title: "8/22 멘토링 — 오늘 확인할 의사결정 3건",
    description:
      "다견 컨텍스트 분리, 오케스트레이션 계약, Neo4j 그래프 실험. 앞의 두 건은 순서대로 이어지고 그래프는 별도 기술 검토입니다.",
    href: "/mentoring/0822.html",
  },
  {
    title: "네오 채소 도감 — 홀로그램 카드 데모",
    description:
      "채소가 된 네오 카드 12장. 마우스를 올리면 홀로그램이 돌고, 누르면 카드가 제자리에서 가운데로 날아옵니다. 안의 '홀로 스튜디오' 에서는 내 사진을 올려 포일 12종을 갈아 끼워 보고 HTML 한 장으로 뽑을 수 있습니다. 프레임워크 없이 HTML·CSS·JS 만으로 만든 화면입니다.",
    // 이 저장소가 아니라 SAJOYO/DAENGS_CARDS 의 GitHub Pages 입니다 (D-025).
    href: "https://cards.weareithero.cloud/",
  },
];

const stack = [
  ["웹", "Next.js 16 · standalone"],
  ["프로세스", "PM2 cluster ×2"],
  ["프록시", "nginx :80"],
  ["배포", "GitHub Actions self-hosted"],
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col font-sans">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-black/80">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
          <span className="text-lg font-semibold tracking-tight">
            DAENGS<span className="ml-2 text-sm font-normal text-zinc-500 dark:text-zinc-400">관리자</span>
          </span>
          <Link
            href="/console"
            className="rounded-full border border-zinc-300 px-4 py-1.5 text-sm text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
          >
            관리자 콘솔
          </Link>
        </div>
      </header>

      <main className="flex-1">
        <section className="mx-auto max-w-5xl px-6 py-20 sm:py-28">
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
            내부 운영 도구
          </p>
          <h1 className="mt-4 max-w-2xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            DAENGS 관리자 콘솔
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-zinc-600 dark:text-zinc-400">
            견주가 쓰는 화면은 안드로이드 앱으로 만듭니다. 이 웹은 지식 베이스와
            검색 품질을 들여다보는 운영용이며, 콘솔은 로그인해야 열립니다.
          </p>
        </section>

        <section className="border-t border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto max-w-5xl px-6 py-16">
            <h2 className="text-2xl font-semibold tracking-tight">문서</h2>
            <div className="mt-10 grid gap-6">
              {documents.map((doc) => (
                // 정적 HTML(public/) 이거나 아예 다른 도메인이라 Link 가 아닌 a 로 넘깁니다.
                <a
                  key={doc.href}
                  href={doc.href}
                  {...(doc.href.startsWith("http")
                    ? { target: "_blank", rel: "noreferrer" }
                    : {})}
                  className="group rounded-xl border border-zinc-200 p-6 transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
                >
                  <h3 className="font-medium group-hover:underline">
                    {doc.title}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                    {doc.description}
                  </p>
                </a>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-10 text-sm text-zinc-500 dark:text-zinc-400">
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
            {stack.map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-zinc-400 dark:text-zinc-500">
                  {label}
                </dt>
                <dd className="mt-0.5">{value}</dd>
              </div>
            ))}
          </dl>
          <span>© 2026 DAENGS</span>
        </div>
      </footer>
    </div>
  );
}
