"use client";

import { useState } from "react";

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
  created_at: string;
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
function emptyReason(status: AppUser["status"]): string {
  return status === "withdrawn" ? "파기됨" : "동의 안 받음";
}

/**
 * 회원 조회 (콘솔 로드맵 A2 · #211).
 *
 * **검색만 있고 목록이 없습니다.** 서버가 조건 없는 조회를 422 로 막습니다 — 이 API 는
 * `Perm.READ` 라 VIEWER 까지 통과하는데, 목록을 열면 로그인한 사람 누구나 전 회원을
 * 넘겨보게 됩니다 (`routers/app_user_admin.py` 의 `search`).
 *
 * **원문 보기와 정지 버튼이 없습니다.** 짝 카드(#212)가 `pii:read` · `ops:write` 와
 * 감사 기록을 함께 붙입니다. 이 화면은 읽고 가려서 보여 주기만 합니다.
 */
export default function AppUsersConsole() {
  // **버튼 둘의 권한이 다릅니다** — 원문은 `pii:read`, 정지는 `ops:write`.
  // 지금 `ROLE_PERMISSIONS` 로는 둘 다 ADMIN·OPERATOR 만 가지지만, 화면에서 합쳐 두면
  // role 구성을 바꿀 때 여기가 조용히 틀립니다 (`core/deps.py` 첫 문단).
  const { can } = useAuth();
  const canReveal = can("pii:read");
  const canWrite = can("ops:write");

  const [mode, setMode] = useState<"email" | "kakao_id">("email");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AppUser[] | null>(null);
  const [detail, setDetail] = useState<AppUserDetail | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 원문. `null` 이면 아직 안 열어 본 것입니다 — 열었는데 비어 있는 것과 다릅니다. */
  const [pii, setPii] = useState<RevealedPii | null>(null);

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
        setNotice(
          mode === "email"
            ? "그 이메일로 가입한 회원이 없습니다. 주소가 정확한지, 탈퇴한 회원은 아닌지 보세요."
            : "그 카카오 회원번호를 가진 회원이 없습니다.",
        );
      } else {
        // 결과가 하나뿐이라 바로 펼칩니다 — 한 번 더 누르게 할 이유가 없습니다.
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
          onChange={(e) => setMode(e.target.value as "email" | "kakao_id")}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        >
          <option value="email">이메일</option>
          <option value="kakao_id">카카오 회원번호</option>
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          // 카카오 회원번호는 64비트 정수라 `number` 로 두면 큰 값에서 정밀도가 깨집니다.
          // 문자열로 받아 서버가 검증하게 둡니다.
          inputMode={mode === "kakao_id" ? "numeric" : "email"}
          placeholder={mode === "email" ? "daengs@example.com" : "123456789"}
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
        이메일은 <strong className="font-medium">주소 전체가 정확히 맞아야</strong> 찾습니다.
        개인정보라 암호화되어 있어 일부만으로는 찾을 수 없습니다 (대소문자와 앞뒤 공백은 괜찮습니다).
        <br />
        <strong className="font-medium">탈퇴한 회원은 이메일로 찾을 수 없습니다</strong> — 탈퇴할 때
        개인정보가 파기되기 때문입니다. 카카오 회원번호로 찾으세요.
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
                {u.email_masked ?? emptyReason(u.status)} · {u.name_masked ?? "-"}
              </button>
            </li>
          ))}
        </ul>
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
