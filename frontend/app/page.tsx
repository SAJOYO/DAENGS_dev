const features = [
  {
    title: "산책 기록",
    description: "언제, 어디를, 얼마나 걸었는지 자동으로 남깁니다.",
  },
  {
    title: "건강 관리",
    description: "예방접종과 병원 방문 일정을 놓치지 않게 챙깁니다.",
  },
  {
    title: "돌봄 공유",
    description: "가족이 같은 기록을 함께 보고 함께 씁니다.",
  },
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col font-sans">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-black/80">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
          <span className="text-lg font-semibold tracking-tight">DAENGS</span>
          <nav className="flex items-center gap-6 text-sm text-zinc-600 dark:text-zinc-400">
            <a href="#features" className="hover:text-zinc-950 dark:hover:text-zinc-50">
              기능
            </a>
            <a href="#about" className="hover:text-zinc-950 dark:hover:text-zinc-50">
              소개
            </a>
          </nav>
        </div>
      </header>

      <main className="flex-1">
        <section className="mx-auto max-w-5xl px-6 py-24 sm:py-32">
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
            반려견과의 하루를 기록하는 가장 쉬운 방법
          </p>
          <h1 className="mt-4 max-w-2xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            우리 아이의 모든 순간을 한곳에
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-zinc-600 dark:text-zinc-400">
            산책, 식사, 병원 기록까지. 흩어져 있던 반려 생활을 하나로 모아
            가족 모두가 함께 돌볼 수 있게 합니다.
          </p>
          <div className="mt-10 flex flex-wrap gap-3">
            <a
              href="#features"
              className="rounded-full bg-zinc-950 px-6 py-3 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-800 dark:bg-zinc-50 dark:text-zinc-950 dark:hover:bg-zinc-200"
            >
              시작하기
            </a>
            <a
              href="#about"
              className="rounded-full border border-zinc-300 px-6 py-3 text-sm font-medium transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              더 알아보기
            </a>
          </div>
        </section>

        <section
          id="features"
          className="border-t border-zinc-200 dark:border-zinc-800"
        >
          <div className="mx-auto max-w-5xl px-6 py-20">
            <h2 className="text-2xl font-semibold tracking-tight">주요 기능</h2>
            <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {features.map((feature) => (
                <div
                  key={feature.title}
                  className="rounded-xl border border-zinc-200 p-6 dark:border-zinc-800"
                >
                  <h3 className="font-medium">{feature.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                    {feature.description}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section
          id="about"
          className="border-t border-zinc-200 dark:border-zinc-800"
        >
          <div className="mx-auto max-w-5xl px-6 py-20">
            <h2 className="text-2xl font-semibold tracking-tight">소개</h2>
            <p className="mt-6 max-w-2xl leading-7 text-zinc-600 dark:text-zinc-400">
              이 자리에 서비스 소개를 채워 넣으세요. 지금은 배포 파이프라인을
              확인하기 위한 뼈대이며, 레이아웃 구조만 잡아 두었습니다.
            </p>
          </div>
        </section>
      </main>

      <footer className="border-t border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-col gap-2 px-6 py-10 text-sm text-zinc-500 sm:flex-row sm:items-center sm:justify-between dark:text-zinc-400">
          <span>© 2026 DAENGS</span>
          <span>Next.js · PM2 · nginx</span>
        </div>
      </footer>
    </div>
  );
}
