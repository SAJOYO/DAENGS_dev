"use client";

import { useState } from "react";

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
  const [mode, setMode] = useState<"email" | "kakao_id">("email");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AppUser[] | null>(null);
  const [detail, setDetail] = useState<AppUserDetail | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    try {
      setDetail(await apiJson<AppUserDetail>(`/api/admin/app-users/${id}`));
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "회원을 불러오지 못했습니다.");
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
            <Field label="이메일" value={detail.email_masked} status={detail.status} />
            <Field label="전화번호" value={detail.phone_masked} status={detail.status} />
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
            **가린 값이지 지운 값이 아닙니다.** 원문을 여는 문은 짝 카드(#212)가
            `pii:read` 로 따로 내고, 그 조회는 감사 기록에 남습니다.
          */}
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            개인정보는 가려서 보여 줍니다. 원문 보기는 아직 없습니다.
          </p>

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
}: {
  label: string;
  value: string | null;
  status: AppUser["status"];
}) {
  return (
    <div>
      <dt className="text-xs text-zinc-500 dark:text-zinc-400">{label}</dt>
      <dd className={value ? "mt-0.5" : "mt-0.5 text-zinc-400 dark:text-zinc-500"}>
        {value ?? emptyReason(status)}
      </dd>
    </div>
  );
}
