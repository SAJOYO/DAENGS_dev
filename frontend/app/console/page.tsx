"use client";

import Link from "next/link";

import { useAuth } from "../components/auth-provider";
import type { Permission } from "@/lib/auth";

/**
 * 콘솔 메뉴. `permission` 은 **메뉴를 보여 줄지 정하는 값일 뿐**이고,
 * 실제 차단은 각 API 가 `core/deps.py` 의 같은 권한으로 합니다.
 * 이름 문자열이 백엔드의 `Perm` 과 어긋나면 조용히 메뉴가 사라집니다.
 */
const consoleSections: Array<{
  title: string;
  description: string;
  permission: Permission;
  /**
   * 화면이 있는 메뉴만 가집니다. 없으면 "준비 중" 카드로 그립니다.
   *
   * `string` 이 아니라 **실제 경로의 리터럴**인 이유: Next 의 typedRoutes 가
   * `<Link href>` 를 그렇게 검사해서, 오타나 지워진 라우트를 빌드에서 잡습니다.
   * 화면이 늘어나면 여기에 경로를 `|` 로 더하세요.
   */
  href?: "/console/search";
}> = [
  {
    title: "지식 베이스",
    description: "훈련 문서를 올리고, 청크와 그래프 추출 결과를 확인합니다.",
    permission: "kb:write",
  },
  {
    title: "검색 점검",
    description: "질문을 넣어 벡터·그래프 검색 경로와 근거 문서를 비교합니다.",
    permission: "search:inspect",
    href: "/console/search",
  },
  {
    title: "회원 · 반려견",
    description: "앱에서 들어온 계정과 반려견 프로필을 조회하고 정리합니다.",
    permission: "read",
  },
  {
    title: "운영 지표",
    description: "질문량, 거절 비율, 응답 지연을 한 화면에서 봅니다.",
    permission: "metrics:read",
  },
];

export default function ConsoleHome() {
  const { admin, can } = useAuth();
  const visible = consoleSections.filter((section) => can(section.permission));

  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">
        {admin?.name}님, 반갑습니다
      </h1>
      <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">
        열려 있는 메뉴만 눌러서 들어갈 수 있습니다. 나머지는 무엇을 담을지만 적어 둔 자리입니다.
      </p>

      <div className="mt-10 grid gap-6 sm:grid-cols-2">
        {visible.map((section) => {
          const body = (
            <>
              <div className="flex items-baseline justify-between gap-3">
                <h2 className="font-medium group-hover:underline">{section.title}</h2>
                <span className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
                  {section.href ? "열기" : "준비 중"}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                {section.description}
              </p>
            </>
          );

          return section.href ? (
            <Link
              key={section.title}
              href={section.href}
              className="group rounded-xl border border-zinc-200 p-6 transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
            >
              {body}
            </Link>
          ) : (
            <div
              key={section.title}
              className="rounded-xl border border-zinc-200 p-6 dark:border-zinc-800"
            >
              {body}
            </div>
          );
        })}
      </div>

      {visible.length === 0 && (
        <p className="mt-10 rounded-xl border border-zinc-200 p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          이 계정에 열려 있는 메뉴가 없습니다. 권한이 필요하면 관리자에게 요청하세요.
        </p>
      )}
    </section>
  );
}
