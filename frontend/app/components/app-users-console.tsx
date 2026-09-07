"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { apiJson, ApiError } from "@/lib/api";

/**
 * `GET /api/admin/app-users` 의 한 줄 (`schemas/app_user_admin.py` 의 `AppUserOut`).
 *
 * **원문도 `email_hash` 도 없습니다.** 서버가 안 보내는 것이고, 여기에 필드를 더하고
 * 싶어지는 날이 오면 그건 응답이 잘못된 것입니다 (스키마 파일의 첫 문단).
 */
type AppUser = {
  id: string;
  kakao_id: number;
  email_masked: string | null;
  phone_masked: string | null;
  name_masked: string | null;
  status: "active" | "suspended" | "withdrawn";
  room_name: string | null;
  /**
   * 사람 이름. **이 화면에서 회원을 알아보는 거의 유일한 값입니다** — 이 앱키로는
   * 이메일·전화·이름 동의를 못 받아 위 `*_masked` 가 전부 `null` 입니다.
   *
   * `null` 은 **아직 발급 전**입니다 (이 칸보다 먼저 가입한 회원. 다음 로그인에
   * 채워집니다). `*_masked` 의 `null` 과 뜻이 다릅니다 — 저건 동의를 못 받았거나 파기.
   */
  nickname: string | null;
  created_at: string;
};

/**
 * `GET /api/admin/app-users/list` 의 한 줄 (`AppUserRosterItem`).
 *
 * **`AppUser` 와 겹치지 않습니다.** 개인정보가 한 칸도 없고 `kakao_id` 도 없습니다 —
 * 조건 없이 전 회원을 주는 목록이라, 아무것도 안 들고 있는 것이 이 화면이 열릴 수
 * 있게 된 이유입니다 (2026-09-05 사람 결정 · 로드맵 A2c).
 */
type RosterUser = {
  id: string;
  nickname: string | null;
  room_name: string | null;
  status: AppUser["status"];
  created_at: string;
  pet_count: number;
  /**
   * 대표 강아지 이름. 대표가 없으면 `null` 입니다.
   *
   * **닉네임이 비었을 때 사람을 가리는 값입니다** — 이 칸이 생기기 전에 가입한
   * 회원은 닉네임이 `null` 이라, 없으면 목록이 "이름 없음" 만 줄줄이 뜹니다.
   */
  primary_pet_name: string | null;
};

type RosterPage = { users: RosterUser[]; next_cursor: string | null };

/** 검색 갈래. `email` 은 이 앱키로는 영영 0건이지만 일부러 남겨 둡니다 (아래 안내). */
type SearchMode = "nickname" | "email" | "kakao_id";

/** 목록 한 쪽에 몇 명. 서버 기본값과 같습니다 (`services/app_user_admin.py`). */
const PAGE = 50;

/**
 * 0건일 때의 문구. **갈래마다 다른 이유로 0건이라** 한 문장으로 못 씁니다 —
 * 닉네임은 아직 발급 전일 수 있고, 이메일은 이 앱키로는 언제나 0건입니다.
 */
const PLACEHOLDER: Record<SearchMode, string> = {
  nickname: "네옹집사",
  email: "daengs@example.com",
  kakao_id: "123456789",
};

const EMPTY_NOTICE: Record<SearchMode, string> = {
  nickname:
    "그 닉네임을 가진 회원이 없습니다. 이 칸보다 먼저 가입한 회원은 아직 닉네임이 없어 다음 로그인에 생깁니다.",
  email:
    "그 이메일로 가입한 회원이 없습니다 — 지금은 이메일로는 아무도 못 찾습니다 (아래 안내).",
  kakao_id: "그 카카오 회원번호를 가진 회원이 없습니다.",
};

/**
 * `schemas/app_user_admin.py` 의 `AdminPetOut` (= 앱의 `PetResponse` + `breed_label`).
 * 강아지에는 암호화 컬럼이 없어 그대로 옵니다 (05_pets.sql).
 */
type Pet = {
  id: string;
  /** 앱의 아바타 id (`dog_toy_poodle_light_brown`). 화면에는 `breed_label` 을 씁니다. */
  breed: string;
  /**
   * 사람이 읽을 견종명. **서버가 붙여 보냅니다** — 번역표(`services/dog_context.py` 의
   * `BREED_LABELS`)는 이미 DAENGS_APP `DogShapes.kt` 의 사본이라, 프론트에 또 두면
   * 사본이 셋이 되어 반드시 어긋납니다.
   */
  breed_label: string;
  name: string;
  sex: "male" | "female" | null;
  neutered: boolean | null;
  weight_kg: string | number | null;
  birth_date: string | null;
  birth_date_kind: "birthday" | "family_day" | null;
  farewell_on: string | null;
  is_primary: boolean;
};

type AppUserDetail = AppUser & { pets: Pet[] };

/**
 * `GET /api/admin/app-users/{id}/pii` 의 응답 (`schemas` 의 `AppUserPiiOut`).
 *
 * **이 값을 받는 순간 서버에 감사 행이 하나 남았습니다** — 그래서 화면이 누르기 전에
 * 그 사실을 알려 줍니다. 모르고 누르는 기록은 감사가 아니라 함정입니다.
 */
type RevealedPii = { email: string | null; phone: string | null; name: string | null };

/**
 * 회원 상태. **`withdrawn` 을 `suspended` 와 다르게 씁니다** — 정지는 관리자가 막은
 * 것이고 탈퇴는 본인이 한 것이며, 탈퇴 쪽은 개인정보가 이미 파기돼 있습니다.
 * 색까지 같으면 화면에서 "되돌릴 수 있는 것"과 "되돌릴 수 없는 것"이 안 갈립니다.
 */
const STATUS: Record<AppUser["status"], { label: string; className: string }> = {
  active: {
    label: "이용 중",
    className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  },
  suspended: {
    label: "정지됨",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  withdrawn: {
    label: "탈퇴함",
    className: "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  },
};

const SEX: Record<string, string> = { male: "수컷", female: "암컷" };

function when(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", { dateStyle: "medium" });
}

/**
 * 가려진 값이 비어 있을 때 뭐라고 할지. **`null` 의 뜻이 `status` 로 갈립니다.**
 *
 * 탈퇴한 회원은 `*_enc` 가 파기됐고(`services/app_auth.py`), 그 외에는 카카오에서 그
 * 항목 동의를 못 받은 것입니다. 둘 다 "없음"으로 쓰면 "우리가 지웠다"와 "처음부터
 * 못 받았다"가 화면에서 같아 보입니다.
 */
/**
 * 반려견 칸의 글자. **대표 이름을 앞에 둡니다** — 닉네임이 비어 있을 때 사람을
 * 가리는 것이 이 이름이라, "반려견 3" 보다 "네옹 외 2마리" 가 훨씬 잘 읽힙니다.
 *
 * 대표가 없는데 마릿수가 있는 경우도 있습니다 (지웠다가 아직 승계 전). 그때는
 * 마릿수만 말합니다 — 없는 이름을 지어내지 않습니다.
 */
function petLabel(u: RosterUser): string {
  if (u.pet_count === 0) return "반려견 없음";
  if (!u.primary_pet_name) return `반려견 ${u.pet_count}`;
  return u.pet_count === 1
    ? u.primary_pet_name
    : `${u.primary_pet_name} 외 ${u.pet_count - 1}`;
}

function emptyReason(status: AppUser["status"]): string {
  return status === "withdrawn" ? "파기됨" : "동의 안 받음";
}

/**
 * 회원 조회 (콘솔 로드맵 A2 · #211 · #212 · #257).
 *
 * **찾기와 훑기가 다른 문입니다.** 검색은 `GET /admin/app-users`(`Perm.READ`)이고,
 * 조건 없는 목록은 `GET /admin/app-users/list`(`admin:manage`)입니다. 서버에서 경로가
 * 갈린 것은 **FastAPI 의존성이 경로 단위**라 질의 인자로는 권한을 못 가르기 때문입니다.
 *
 * **목록에는 개인정보가 한 칸도 없습니다** — 마스킹한 것조차 없습니다. 그것이 조건
 * 없는 목록을 열 수 있게 된 이유입니다 (2026-09-05 사람 결정 · 로드맵 A2c). 여기에
 * `email_masked` 한 칸을 더하고 싶어지면 그 결정을 먼저 다시 여세요 — 화면은 멀쩡해
 * 보이는데 "누가 전 회원의 개인정보를 넘겨본다" 가 그 순간 다시 성립합니다.
 *
 * **이 화면 안에서 권한이 셋으로 갈립니다.** 목록 `admin:manage` · 원문 `pii:read` ·
 * 정지 `ops:write`. 화면에서 가리는 것은 UX 일 뿐이고 실제 차단은 백엔드가 같은
 * 권한으로 합니다 (`lib/auth.ts`).
 */
export default function AppUsersConsole() {
  // **버튼 둘의 권한이 다릅니다** — 원문은 `pii:read`, 정지는 `ops:write`.
  // 지금 `ROLE_PERMISSIONS` 로는 둘 다 ADMIN·OPERATOR 만 가지지만, 화면에서 합쳐 두면
  // role 구성을 바꿀 때 여기가 조용히 틀립니다 (`core/deps.py` 첫 문단).
  const { can } = useAuth();
  const canReveal = can("pii:read");
  const canWrite = can("ops:write");
  const canBrowse = can("admin:manage");

  // **기본은 닉네임입니다.** 이메일은 이 앱키로 영영 0건이라(아래 안내) 기본으로 두면
  // 처음 쓰는 사람이 "검색이 고장났다" 를 먼저 만납니다.
  const [mode, setMode] = useState<SearchMode>("nickname");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AppUser[] | null>(null);
  const [detail, setDetail] = useState<AppUserDetail | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 원문. `null` 이면 아직 안 열어 본 것입니다 — 열었는데 비어 있는 것과 다릅니다. */
  const [pii, setPii] = useState<RevealedPii | null>(null);

  /** 훑는 목록. `null` 이면 아직 안 불러온 것이고, `[]` 는 회원이 없는 것입니다. */
  const [roster, setRoster] = useState<RosterUser[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`reports-console.tsx` 와 같은 장치).
  const alive = useRef(true);

  const loadRoster = useCallback((signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(PAGE) });
    return apiJson<RosterPage>(`/api/admin/app-users/list?${params}`, { signal })
      .then((page) => {
        if (!alive.current) return;
        setRoster(page.users);
        setCursor(page.next_cursor);
        setRosterError(null);
      })
      .catch((e: unknown) => {
        if (!alive.current || (e as Error)?.name === "AbortError") return;
        setRosterError(e instanceof ApiError ? e.message : "회원 목록을 불러오지 못했습니다.");
      });
  }, []);

  useEffect(() => {
    // 목록을 못 보는 계정에서는 아예 부르지 않습니다 — 부르면 403 한 번이 콘솔에
    // 남고, 화면에는 어차피 안 그립니다.
    if (!canBrowse) return;
    alive.current = true;
    const controller = new AbortController();
    void loadRoster(controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [canBrowse, loadRoster]);

  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE), cursor });
      const page = await apiJson<RosterPage>(`/api/admin/app-users/list?${params}`);
      // **이어 붙입니다.** 키셋 커서라 읽는 동안 가입이 들어와도 겹치거나 빠지지
      // 않습니다 (OFFSET 이면 어긋납니다).
      setRoster((prev) => [...(prev ?? []), ...page.users]);
      setCursor(page.next_cursor);
    } catch (e) {
      setRosterError(e instanceof ApiError ? e.message : "더 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    setDetail(null);
    try {
      const params = new URLSearchParams({ [mode]: query.trim() });
      const found = await apiJson<AppUser[]>(`/api/admin/app-users?${params}`);
      setResults(found);
      if (found.length === 0) {
        setNotice(EMPTY_NOTICE[mode]);
      } else if (found.length === 1) {
        // 하나뿐이면 바로 펼칩니다 — 한 번 더 누르게 할 이유가 없습니다.
        // **닉네임은 여럿이 나올 수 있어** 조건이 `=== 1` 입니다 (부분 일치라서).
        await open(found[0].id);
      }
    } catch (e) {
      setResults(null);
      setNotice(e instanceof ApiError ? e.message : "검색에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    // **원문을 반드시 접습니다.** 안 접으면 A 회원의 원문이 B 회원 화면에 남습니다.
    setPii(null);
    try {
      setDetail(await apiJson<AppUserDetail>(`/api/admin/app-users/${id}`));
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "회원을 불러오지 못했습니다.");
    }
  }

  async function reveal(id: string) {
    setBusy(true);
    setNotice(null);
    try {
      setPii(await apiJson<RevealedPii>(`/api/admin/app-users/${id}/pii`));
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "원문을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(id: string, status: "active" | "suspended") {
    setBusy(true);
    setNotice(null);
    try {
      await apiJson<AppUser>(`/api/admin/app-users/${id}`, {
        method: "PATCH",
        // `apiJson` 은 헤더를 안 붙여 줍니다 — 빼면 FastAPI 가 본문을 파싱하지 않아
        // 422 가 나고, 그 detail 은 목록이라 화면에 기본 문구만 남습니다.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      setNotice(status === "suspended" ? "회원을 정지했습니다." : "정지를 풀었습니다.");
      // 상태가 바뀐 행을 다시 읽습니다. `open` 이 원문도 같이 접습니다.
      await open(id);
    } catch (e) {
      // 409 는 **탈퇴한 회원**입니다. 서버가 무엇을 해야 하는지까지 문구에 담아 줍니다.
      setNotice(e instanceof ApiError ? e.message : "상태를 바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={(e) => void search(e)} className="flex flex-wrap gap-3">
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as SearchMode)}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        >
          <option value="nickname">닉네임</option>
          <option value="email">이메일</option>
          <option value="kakao_id">카카오 회원번호</option>
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          // 카카오 회원번호는 64비트 정수라 `number` 로 두면 큰 값에서 정밀도가 깨집니다.
          // 문자열로 받아 서버가 검증하게 둡니다.
          inputMode={mode === "kakao_id" ? "numeric" : "text"}
          placeholder={PLACEHOLDER[mode]}
          className="min-w-[18rem] flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-full bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          찾기
        </button>
      </form>

      {/*
        **이 안내가 없으면 "검색이 고장났다" 로 읽힙니다.** `email_hash` 가 HMAC 이라
        조각 검색이 원천적으로 성립하지 않습니다 (D-012 · 03_auth.sql). 우리가 게을러서
        부분 검색을 안 넣은 것이 아니라, 넣을 방법이 없습니다.
      */}
      <p className="text-xs leading-5 text-zinc-500 dark:text-zinc-400">
        <strong className="font-medium">닉네임</strong>은 일부만 넣어도 찾습니다. 다만 이 칸이
        생기기 전에 가입한 회원은 아직 닉네임이 없어{" "}
        <strong className="font-medium">다음 로그인에 생깁니다</strong> — 그때까지는 목록에서
        찾으세요.
        <br />
        <strong className="font-medium">이메일로는 지금 아무도 못 찾습니다.</strong> 우리 카카오
        앱키로는 이메일 동의를 받을 수 없어 저장된 값이 없습니다. 칸을 남겨 둔 것은 비즈 앱
        심사를 통과하면 그날부터 도는 길이기 때문입니다 (그때도 주소 전체가 정확히 맞아야
        합니다 — 암호화돼 있어 일부로는 못 찾습니다).
        <br />
        <strong className="font-medium">탈퇴한 회원은 카카오 회원번호로만</strong> 찾습니다 —
        탈퇴할 때 개인정보가 파기되기 때문입니다.
      </p>

      {notice && (
        <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          {notice}
        </p>
      )}

      {results && results.length > 1 && (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-900">
          {results.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                onClick={() => void open(u.id)}
                className="w-full py-3 text-left text-sm hover:underline"
              >
                {/* **닉네임이 앞입니다.** 뒤의 둘은 이 앱키로는 전부 비어 있어서,
                    닉네임이 없으면 줄 전체가 "동의 안 받음 · -" 가 됩니다. */}
                <span className="font-medium">{u.nickname ?? "이름 없음"}</span>
                <span className="ml-2 text-zinc-500 dark:text-zinc-400">
                  {u.email_masked ?? emptyReason(u.status)} · {u.name_masked ?? "-"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/*
        훑는 목록. **`admin:manage` 가 있을 때만 그립니다** — 없는 계정에게는 검색만
        보입니다. 화면에서 가리는 것은 UX 일 뿐이고 실제 차단은 백엔드가 같은 권한으로
        합니다 (`routers/app_user_admin.py` 의 `roster`).
      */}
      {canBrowse && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-sm font-medium">회원 목록</h2>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              가입 최근 순 · 개인정보는 상세에서 봅니다
            </p>
          </div>

          {rosterError && (
            <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
              {rosterError}
            </p>
          )}

          {roster && roster.length === 0 && (
            <p className="py-6 text-sm text-zinc-500 dark:text-zinc-400">
              가입한 회원이 아직 없습니다.
            </p>
          )}

          {roster && roster.length > 0 && (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-900">
              {roster.map((u) => (
                <li key={u.id}>
                  <button
                    type="button"
                    onClick={() => void open(u.id)}
                    className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 py-3 text-left text-sm hover:underline"
                  >
                    <span className="font-medium">{u.nickname ?? "이름 없음"}</span>
                    {u.room_name && (
                      <span className="text-zinc-500 dark:text-zinc-400">
                        {u.room_name}
                      </span>
                    )}
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${STATUS[u.status].className}`}
                    >
                      {STATUS[u.status].label}
                    </span>
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">
                      {petLabel(u)}
                    </span>
                    <span className="ml-auto text-xs text-zinc-400 dark:text-zinc-500">
                      {when(u.created_at)} 가입
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {cursor && (
            <button
              type="button"
              onClick={() => void more()}
              disabled={busy}
              className="rounded-full border border-zinc-300 px-4 py-2 text-sm transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              더 보기
            </button>
          )}
        </section>
      )}

      {detail && (
        <div className="space-y-6 rounded-xl border border-zinc-200 p-6 dark:border-zinc-800">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-lg font-medium">
              {detail.name_masked ?? "이름 없음"}
              {detail.room_name && (
                <span className="ml-2 text-sm font-normal text-zinc-500 dark:text-zinc-400">
                  {detail.room_name}
                </span>
              )}
            </h2>
            <span className={`rounded-full px-2.5 py-0.5 text-xs ${STATUS[detail.status].className}`}>
              {STATUS[detail.status].label}
            </span>
          </div>

          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            {/*
              원문을 열었으면 그것을, 아니면 가려진 값을 보여 줍니다. **원문이 `null` 인
              것은 실패가 아닙니다** — 열 것이 없었던 것이고, 그때는 가려진 값 자리와
              같은 문구(`파기됨` / `동의 안 받음`)로 떨어집니다.
            */}
            <Field
              label="이메일"
              value={pii ? pii.email : detail.email_masked}
              status={detail.status}
              revealed={pii !== null}
            />
            <Field
              label="전화번호"
              value={pii ? pii.phone : detail.phone_masked}
              status={detail.status}
              revealed={pii !== null}
            />
            <Field
              label="이름"
              value={pii ? pii.name : detail.name_masked}
              status={detail.status}
              revealed={pii !== null}
            />
            <div>
              <dt className="text-xs text-zinc-500 dark:text-zinc-400">카카오 회원번호</dt>
              <dd className="mt-0.5 font-mono text-xs">{detail.kakao_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-zinc-500 dark:text-zinc-400">가입일</dt>
              <dd className="mt-0.5">{when(detail.created_at)}</dd>
            </div>
          </dl>

          {/*
            **누르기 전에 기록된다는 것을 알려 줍니다.** 모르고 누르는 기록은 감사가
            아니라 함정입니다. 권한이 없는 사람에게는 버튼 대신 왜 없는지를 적습니다 —
            버튼만 사라지면 "고장났나" 로 읽힙니다.
          */}
          <div className="flex flex-wrap items-center gap-3">
            {canReveal ? (
              pii ? (
                <>
                  <button
                    type="button"
                    onClick={() => setPii(null)}
                    className="rounded-full border border-zinc-300 px-4 py-1.5 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                  >
                    다시 가리기
                  </button>
                  <span className="text-xs text-amber-700 dark:text-amber-400">
                    원문을 열었습니다. 이 조회는 기록에 남았습니다.
                  </span>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void reveal(detail.id)}
                    className="rounded-full bg-zinc-900 px-4 py-1.5 text-xs text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
                  >
                    원문 보기
                  </button>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    누르면 <strong className="font-medium">누가 · 언제 · 어느 칸을 열었는지</strong>가
                    기록에 남습니다. 값은 기록하지 않습니다.
                  </span>
                </>
              )
            ) : (
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                개인정보는 가려서 보여 줍니다. 원문을 보려면 <code>pii:read</code> 권한이 필요합니다.
              </span>
            )}
          </div>

          {canWrite && detail.status !== "withdrawn" && (
            <div className="flex flex-wrap items-center gap-3 border-t border-zinc-200 pt-4 dark:border-zinc-800">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void setStatus(detail.id, detail.status === "active" ? "suspended" : "active")
                }
                className="rounded-full border border-zinc-300 px-4 py-1.5 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
              >
                {detail.status === "active" ? "이용 정지" : "정지 해제"}
              </button>
              {/*
                **정지가 즉시가 아닙니다.** access token 은 무상태라 최대 5분(ACCESS_TTL)
                동안 살아 있습니다 — 요청마다 DB 를 보지 않기로 한 것이 D-015 입니다.
                열려 있던 세션(refresh)은 정지하는 순간 끊깁니다.
              */}
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                정지하면 열려 있던 세션이 끊기지만, 이미 발급된 접근 토큰은 최대 5분 더 살아 있습니다.
              </span>
            </div>
          )}

          {detail.status === "withdrawn" && (
            /*
              **탈퇴는 관리자가 되돌리지 않습니다.** 본인 요청이고 개인정보 파기가
              따라온 일이라, 되돌리면 삭제 요청을 관리자가 무르는 것이 됩니다.
              되살아나는 길은 본인이 카카오로 다시 로그인하는 것 하나입니다.
            */
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              탈퇴한 회원입니다. 개인정보가 이미 파기되었고, 관리자가 상태를 되돌릴 수 없습니다 —
              본인이 카카오로 다시 로그인하면 되살아납니다.
            </p>
          )}

          <div>
            <h3 className="text-sm font-medium">반려견 {detail.pets.length}마리</h3>
            {detail.pets.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
                등록된 반려견이 없습니다.
              </p>
            ) : (
              <ul className="mt-3 space-y-3">
                {detail.pets.map((pet) => (
                  <li
                    key={pet.id}
                    className="rounded-lg border border-zinc-200 px-4 py-3 text-sm dark:border-zinc-800"
                  >
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-medium">{pet.name}</span>
                      {pet.is_primary && (
                        <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
                          대표
                        </span>
                      )}
                      {/*
                        **`farewell_on` 은 무지개다리를 건넌 날입니다** (05_pets.sql).
                        지우지 않고 남기는 값이라, 화면에서도 조용히 알려 줍니다.
                      */}
                      {pet.farewell_on && (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400">
                          {when(pet.farewell_on)}에 이별
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      {[
                        pet.breed_label,
                        pet.sex ? SEX[pet.sex] : null,
                        pet.neutered === null ? null : pet.neutered ? "중성화함" : "중성화 안 함",
                        pet.weight_kg ? `${pet.weight_kg}kg` : null,
                        pet.birth_date
                          ? `${when(pet.birth_date)} ${
                              pet.birth_date_kind === "family_day" ? "가족이 된 날" : "생일"
                            }`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  status,
  revealed,
}: {
  label: string;
  value: string | null;
  status: AppUser["status"];
  /** 원문을 연 상태인가. 값이 같아도 **가려진 값인지 원문인지** 화면이 말해야 합니다. */
  revealed: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">
        {label}
        {revealed && value && (
          <span className="ml-1.5 text-amber-700 dark:text-amber-400">원문</span>
        )}
      </dt>
      <dd className={value ? "mt-0.5" : "mt-0.5 text-zinc-400 dark:text-zinc-500"}>
        {value ?? emptyReason(status)}
      </dd>
    </div>
  );
}
