/* ── 홀로 스튜디오 ─────────────────────────────────────────
   올린 이미지 한 장에 도감의 포일 12종을 입혀 보고, 원하면 HTML 한 장으로 뽑는다.
   도감(index.html / main.js)과는 **완전히 따로 도는 페이지**다 — 서로 import 하지
   않으므로 여기를 고쳐도 도감은 안 흔들린다. 공유하는 건 CSS 세 덩이(style.css ·
   rarity.css · vendor/) 와 cards.mjs 의 CARDS_CSS_EFFECTS 목록뿐이다.

   **이머시브(No.01의 꾹 누르기)는 없다.** 그건 카드마다 레이어 원화(배경 · 누끼 ·
   틀)가 따로 있어야 도는 연출이라, 이미지 한 장으로는 애초에 만들 수가 없다.
   그래서 여기 No.01 은 이머시브를 뺀 **그 카드의 표면**(style.css 의 자체 포일)이다. */

import { CARDS_CSS_EFFECTS } from "./cards.mjs";

/* 이 폴더는 **폴더 하나로 자족**한다(README 앞머리) — 바깥의 공용 유틸을 import 하지
   않는다. 원본(gohome)은 허브의 shared/util.js 에서 store 를 가져왔지만, 여기서 쓰는 건
   get/set 둘뿐이고 담기는 값도 **고른 포일 번호 하나**라 그만큼만 둔다.
   사파리 프라이빗처럼 localStorage 접근 자체가 던지는 데가 있어서 try 로 감싼다 —
   실패해도 포일이 기본값(No.07)으로 돌아갈 뿐이라 화면은 그대로 돈다. */
const PREF_KEY = (key) => `daengs:neo-hologram:${key}`;
const prefs = {
  get(key, fallback = null) {
    try {
      const v = localStorage.getItem(PREF_KEY(key));
      return v === null ? fallback : JSON.parse(v);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(PREF_KEY(key), JSON.stringify(value)); } catch {}
  },
};
const LAST_EFFECT = "studio-effect";

/**
 * 고를 수 있는 포일 12종. 번호와 이름은 도감의 레어도 표(cards.mjs 머리말)와 같다 —
 * 도감에서 본 No.07 과 여기서 고른 No.07 이 같아야 이 화면이 말이 된다.
 *
 * id 는 그대로 DOM 의 data-effect 가 되므로 `basic` 을 뺀 열한 개는
 * CARDS_CSS_EFFECTS 에 있어야 하고, vendor/cards-css/<id>.css 와
 * studio.html 의 링크도 함께 있어야 화면에 나온다 (아래에서 실제로 확인한다).
 */
const EFFECTS = [
  { no: 1,  id: "basic",    ko: "기본 홀로", desc: "무지개 원뿔 + 결. 도감 No.01 의 표면이다 — 꾹 눌러 들어가는 이머시브 연출만 뺐다." },
  { no: 2,  id: "prism",    ko: "프리즘",   desc: "무지개가 각도로 쪼개지는 분광." },
  { no: 3,  id: "crystal",  ko: "크리스탈", desc: "결정 패싯. 면마다 따로 꺾인다." },
  { no: 4,  id: "gold",     ko: "골드",     desc: "금박. 색상환을 안 돌고 명도만 오르내리는 이방성." },
  { no: 5,  id: "oilslick", ko: "오일슬릭", desc: "어두운 기름막 무지개. 밝은 그림 위에서는 잘 안 보인다." },
  { no: 6,  id: "sunburst", ko: "선버스트", desc: "중심에서 뻗는 광선." },
  { no: 7,  id: "holo",     ko: "홀로",     desc: "무지개 밴드 + 세로 스캔라인. 제일 고전적인 홀로." },
  { no: 8,  id: "reverse",  ko: "리버스",   desc: "가운데를 죽이고 가장자리를 살리는 역전 폴오프." },
  { no: 9,  id: "aurora",   ko: "오로라",   desc: "넓고 부드러운 색 띠." },
  { no: 10, id: "cosmos",   ko: "코스모스", desc: "성운 결." },
  { no: 11, id: "mosaic",   ko: "모자이크", desc: "격자로 잘린 타일. 유일하게 무늬가 기하학이다." },
  { no: 12, id: "metal",    ko: "메탈",     desc: "세로로 긁힌 브러시드 결. 색상환을 안 돌고 명도만 오르내린다." },
];

// 목록이 어긋나면 그 칩만 조용히 맨 카드가 된다. 조용한 게 제일 나쁘므로 한 번 짖는다.
for (const e of EFFECTS) {
  if (e.id !== "basic" && !CARDS_CSS_EFFECTS.has(e.id)) {
    console.warn(`[studio] cards.mjs 의 CARDS_CSS_EFFECTS 에 "${e.id}" 가 없습니다 — 칩은 뜨지만 포일은 안 나옵니다.`);
  }
}

const $ = (sel) => document.querySelector(sel);

const dropEl = $("#drop");
const holderEl = $("#holder");
const dropzoneEl = $("#dropzone");
const noteEl = $("#stage-note");
const fileEl = $("#file");
const effectsEl = $("#effects");
const effectDescEl = $("#effect-desc");
const fileMetaEl = $("#file-meta");
const downloadEl = $("#download");
const downloadMetaEl = $("#download-meta");
const clearEl = $("#clear");

/** 폰인가. 자이로 안내 문구를 폰에서만 띄우려고 쓴다 (기울기 자체는 엔진이 판단한다). */
const coarsePointer = matchMedia("(hover: none) and (pointer: coarse)");

const esc = (s = "") =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pad2 = (n) => String(n).padStart(2, "0");

const kb = (bytes) =>
  bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

/** 지금 화면에 있는 카드. 이미지가 없으면 null 이다. */
let art = null;   // { src, name, size, w, h, accent, accent2 }
let stage = null;
let effect = EFFECTS.find((e) => e.id === prefs.get(LAST_EFFECT)) ?? EFFECTS[6];   // 기본값 No.07 holo

/* ── 기울기 ────────────────────────────────────────────────
   포인터 · 키보드 · 자이로는 전부 tilt-engine.js 가 한다 (studio.html 이 모듈보다
   먼저 부른다). 여기 코드가 아니라 그 파일에 둔 이유는 **내보낸 HTML 도 같은 걸
   써야 하기 때문**이다 — 내보내기가 그 파일 텍스트를 그대로 인라인한다.
   자세한 건 tilt-engine.js 머리말. */

const tilt = window.NeoTilt;

/* ── 카드 뒤 글로우 색 뽑기 ────────────────────────────────
   도감 카드는 --accent / --accent2 를 손으로 골라 뒀다(그림에서 눈으로 뽑은 색).
   올린 이미지에는 그런 게 없으니 그림에서 직접 뽑는다 — 32x32 로 줄여 그린 다음
   제일 쨍한 픽셀(accent)과 전체 평균(accent2)을 쓴다.

   평균만 쓰면 대개 진흙색이 나와 글로우가 죽고, 최대 채도만 쓰면 튀는 한 점이
   카드 전체를 물들인다. 둘을 안쪽/바깥쪽에 나눠 두는 게 원본 카드의 구성과 같다
   (style.css 의 .stage::before 참고).

   데이터 URL 로 읽으므로 캔버스가 오염되지 않는다 — 그래서 getImageData 가 된다. */

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (!d) return [0, 0, l];
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h * 360, s, l];
}

const hsl = (h, s, l) => `hsl(${h.toFixed(0)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%)`;

function pickAccents(img) {
  const fallback = ["#7de2a8", "#3ad0ff"];
  try {
    const n = 32;
    const canvas = document.createElement("canvas");
    canvas.width = n;
    canvas.height = n;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, n, n);
    const { data } = ctx.getImageData(0, 0, n, n);

    let best = null;
    let bestScore = -1;
    const sum = [0, 0, 0];
    let count = 0;

    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 24) continue;             // 투명한 데는 색이 아니다 (누끼 PNG)
      const [r, g, b] = [data[i], data[i + 1], data[i + 2]];
      sum[0] += r; sum[1] += g; sum[2] += b;
      count++;

      const [h, s, l] = rgbToHsl(r, g, b);
      // 너무 어둡거나 하얗게 뜬 픽셀은 글로우로 쓰면 색이 안 보인다
      const score = s * (1 - Math.abs(l - 0.55) * 1.4);
      if (score > bestScore) { bestScore = score; best = [h, s, l]; }
    }

    if (!count) return fallback;

    const [ah, as, al] = rgbToHsl(sum[0] / count, sum[1] / count, sum[2] / count);

    // 글로우는 블러 30px 을 먹고 나면 눈에 띄게 흐려진다. 채도·밝기를 바닥에서 한 번
    // 올려 두지 않으면 회색 안개가 된다.
    const accent = best
      ? hsl(best[0], Math.max(0.5, best[1]), Math.min(0.68, Math.max(0.5, best[2])))
      : fallback[0];
    const accent2 = hsl(ah, Math.max(0.35, as), Math.min(0.62, Math.max(0.42, al)));
    return [accent, accent2];
  } catch {
    return fallback;                              // 캔버스가 막힌 환경이면 도감 기본색
  }
}

/* ── 카드 만들기 ───────────────────────────────────────────
   main.js 의 makeStage 와 **같은 구조**를 만든다 (span 네 장의 순서까지). 클래스
   이름이 하나라도 어긋나면 style.css 와 vendor 가 못 찾는다. 여기 카드는 눌러서
   열 게 없으므로 확대 뷰 쪽과 같은 role=img 짜임을 쓴다. */

function buildStage() {
  const el = document.createElement("div");
  el.className = "stage";
  el.style.setProperty("--ar", (art.w / art.h).toFixed(4));
  el.style.setProperty("--accent", art.accent);
  el.style.setProperty("--accent2", art.accent2);
  el.innerHTML = `
    <div class="card" tabindex="0" role="img" aria-label="올린 이미지로 만든 홀로그램 카드">
      <img class="art" width="${art.w}" height="${art.h}" decoding="async" alt="">
      <span class="foil holo-card__shine" aria-hidden="true"></span>
      <span class="glare holo-card__glare" aria-hidden="true"></span>
      <span class="grain" aria-hidden="true"></span>
      <span class="edge" aria-hidden="true"></span>
    </div>`;
  el.querySelector(".art").src = art.src;
  tilt.bind(el);          // 포인터 · 키보드 + 자이로의 대상이 된다
  return el;
}

/** 포일 갈아 끼우기. 카드를 다시 만들지 않고 속성만 바꾼다 — 기울기 바인딩이 살아 있다. */
function applyEffect(el, id) {
  if (!el) return;
  // No.01(basic)은 vendor 를 안 쓴다. data-rarity 는 도감 기본값과 같은 fullart 로
  // 둔다 — flat 을 넣으면 rarity.css 가 포일을 통째로 꺼 버린다.
  el.dataset.rarity = id === "basic" ? "fullart" : id;
  if (CARDS_CSS_EFFECTS.has(id)) {
    el.classList.add("holo-card");
    el.dataset.effect = id;
  } else {
    el.classList.remove("holo-card");
    delete el.dataset.effect;
  }
}

/* ── 포일 칩 ───────────────────────────────────────────── */

function renderChips() {
  effectsEl.replaceChildren();
  for (const e of EFFECTS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.id = e.id;
    chip.setAttribute("role", "radio");
    chip.innerHTML = `<span class="chip-no">${pad2(e.no)}</span><span>${esc(e.ko)}</span>`;
    chip.addEventListener("click", () => selectEffect(e.id, { focus: false }));
    effectsEl.append(chip);
  }
  syncChips();
}

function syncChips() {
  for (const chip of effectsEl.children) {
    const on = chip.dataset.id === effect.id;
    chip.setAttribute("aria-checked", String(on));
    chip.tabIndex = on ? 0 : -1;               // 화살표로 옮겨 다니는 라디오그룹
  }
  effectDescEl.textContent = `No.${pad2(effect.no)} ${effect.ko} — ${effect.desc}`;
}

function selectEffect(id, { focus = true } = {}) {
  effect = EFFECTS.find((e) => e.id === id) ?? EFFECTS[0];
  prefs.set(LAST_EFFECT, effect.id);
  applyEffect(stage, effect.id);
  syncChips();
  if (focus) effectsEl.querySelector('[aria-checked="true"]')?.focus();
}

effectsEl.addEventListener("keydown", (e) => {
  const dirs = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
  let to;
  if (e.key in dirs) to = EFFECTS.findIndex((x) => x.id === effect.id) + dirs[e.key];
  else if (e.key === "Home") to = 0;
  else if (e.key === "End") to = EFFECTS.length - 1;
  else return;
  e.preventDefault();
  selectEffect(EFFECTS[(to + EFFECTS.length) % EFFECTS.length].id);
});

/* ── 이미지 받기 ───────────────────────────────────────────
   파일을 **데이터 URL 로** 읽는다. 오브젝트 URL 이 더 가볍지만 두 가지가 걸린다.
     1) 내보내기가 이미지를 파일 안에 넣어야 한다 — 어차피 데이터 URL 이 필요하다
     2) 캔버스로 색을 뽑을 때 오염(taint) 걱정이 없다
   대신 큰 사진은 문자열이 원본의 약 1.37배가 되므로, 아래에서 크기를 알려 준다.

   **올린 이미지는 이 브라우저 밖으로 안 나간다.** 어디로도 업로드하지 않고,
   localStorage 에도 안 넣는다 (거기 들어가는 건 마지막에 고른 포일 번호뿐이다). */

const MAX_SOFT = 12 * 1024 * 1024;   // 이 위는 막지 않고 알려만 준다

function note(msg, isError = false) {
  noteEl.textContent = msg;
  noteEl.classList.toggle("is-error", isError);
}

function acceptFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) return note("이미지 파일만 올릴 수 있습니다.", true);

  const reader = new FileReader();
  reader.onerror = () => note("파일을 읽지 못했습니다.", true);
  reader.onload = () => loadSrc(reader.result, file.name, file.size);
  reader.readAsDataURL(file);
}

function loadSrc(src, name, size) {
  const img = new Image();
  img.onerror = () => note("이미지를 열지 못했습니다. 다른 파일로 해 보세요.", true);
  img.onload = () => {
    const [accent, accent2] = pickAccents(img);
    art = { src, name, size, w: img.naturalWidth, h: img.naturalHeight, accent, accent2 };

    stage = buildStage();
    applyEffect(stage, effect.id);
    holderEl.replaceChildren(stage);            // dropzone 은 지우지 않고 떼어만 둔다 (비우기로 되돌아온다)

    fileMetaEl.textContent = `${name} · ${img.naturalWidth}×${img.naturalHeight} · ${kb(size)}`;
    downloadEl.disabled = false;
    clearEl.disabled = false;
    note(gyroLive ? GYRO_NOTE : baseNote(size));
  };
  img.src = src;
}

/* ── 자이로 ───────────────────────────────────────────────
   폰을 기울이면 카드가 따라 기운다. 켜는 건 이 두 줄이 전부고, 나머지는 전부
   tilt-engine.js 안에 있다 (내보낸 HTML 도 같은 코드로 돈다).

   **PC 는 아무것도 안 바뀐다** — 센서가 없어서 이벤트가 한 번도 안 온다. 그래서
   "자이로 켜짐" 안내는 미리 띄우지 않고, **첫 값이 실제로 들어왔을 때** 바꾼다.
   secure context 가 아니면(폰에서 http + LAN IP) 리스너는 붙고 값만 영영 안 오는데,
   그 경우와 PC 를 구분해 봐야 화면에 쓸 말이 없다. */

const GYRO_NOTE = "폰을 기울여 보세요 · 화면을 문지르면 그쪽이 우선입니다";
const baseNote = (size) => size > MAX_SOFT
  ? "카드 위에서 마우스를 움직여 보세요 · 이미지가 커서 내보낸 파일도 큽니다"
  : "카드 위에서 마우스를 움직여 보세요 · 키보드는 카드를 누른 뒤 화살표";

let gyroLive = false;
tilt.gyro({
  onLive: () => {
    gyroLive = true;
    if (art) note(GYRO_NOTE);
  },
});

// 폰에서 안 켜졌을 때 왜 안 되는지 알 길이 있어야 한다. 안내는 폰에서만 띄운다 —
// PC 에서는 자이로가 없는 게 정상이라 아무 말도 안 하는 게 맞다.
if (coarsePointer.matches && !tilt.canGyro) {
  fileMetaEl.insertAdjacentHTML("afterend",
    '<p class="meta">기울이기(자이로)는 https 나 localhost 에서만 켜집니다.</p>');
}

fileEl.addEventListener("change", () => {
  acceptFile(fileEl.files?.[0]);
  fileEl.value = "";                 // 같은 파일을 다시 골라도 change 가 오도록
});

for (const id of ["#pick", "#pick-empty"]) {
  $(id)?.addEventListener("click", () => fileEl.click());
}

clearEl.addEventListener("click", () => {
  art = null;
  stage = null;
  holderEl.replaceChildren(dropzoneEl);
  fileMetaEl.textContent = "아직 올린 이미지가 없습니다.";
  downloadEl.disabled = true;
  clearEl.disabled = true;
  note("");
});

/* 끌어다 놓기 — dragover 에서 preventDefault 를 안 하면 브라우저가 그 파일을 그냥
   열어 버려서 페이지가 통째로 날아간다. 카드 자리만이 아니라 창 전체에서 받는다. */
for (const ev of ["dragenter", "dragover"]) {
  addEventListener(ev, (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    dropEl.classList.add("is-dragging");
  });
}

addEventListener("dragleave", (e) => {
  if (e.relatedTarget) return;       // 자식으로 옮겨 간 것뿐이면 무시
  dropEl.classList.remove("is-dragging");
});

addEventListener("drop", (e) => {
  if (!e.dataTransfer?.files?.length) return;
  e.preventDefault();
  dropEl.classList.remove("is-dragging");
  acceptFile(e.dataTransfer.files[0]);
});

// 스크린샷을 그대로 붙여넣는 게 제일 빠른 길이라 붙여넣기도 받는다.
addEventListener("paste", (e) => {
  const file = [...(e.clipboardData?.files ?? [])][0];
  if (file) { e.preventDefault(); acceptFile(file); }
});

/* ── HTML 한 장으로 내보내기 ───────────────────────────────
   CSS 는 지금 페이지가 쓰는 파일을 그대로 fetch 해서 넣는다. 손으로 추린 사본을
   두면 style.css 를 고쳤을 때 내보낸 파일만 옛 모양으로 남는다 — 그게 제일 찾기
   어려운 종류의 어긋남이라, 사본 대신 원본을 읽는다.

   세 파일 다 url() 도 @import 도 없어서(확인함) 통째로 인라인하면 그대로 돈다.
   touch.css 는 안 넣는다 — .slot(그리드) 전용 규칙이고 여기 카드는 한 장이다.

   **기울기도 같은 방식이다.** tilt-engine.js 를 fetch 해서 `<script>` 안에 그대로
   넣는다 — 그 파일이 모듈이 아닌 이유가 이것이다. 그래서 내보낸 카드는 이 화면과
   똑같이 마우스 · 키보드 · **자이로**로 기운다. 인라인한 뒤 붙이는 건 아래 두 줄뿐. */

/** 인라인한 엔진에 카드를 물리는 부트스트랩. */
const EXPORT_BOOT = `
  var stage = document.querySelector(".stage");
  NeoTilt.bind(stage);
  // 폰을 기울이면 카드가 따라 기운다. secure context 여야 값이 온다 (https · localhost ·
  // 크롬/파이어폭스의 file://). 안 오면 조용히 아무 일도 안 일어나고 포인터가 그대로 남는다.
  NeoTilt.gyro({ onLive: function () {
    var p = document.querySelector(".solo p");
    if (p) p.textContent = "폰을 기울여 보세요 — 화면을 문지르면 그쪽이 우선입니다";
  } });
`;

/** 내보낸 파일에만 필요한 배치. 카드 CSS 는 위에서 인라인한 원본이 다 갖고 있다. */
const EXPORT_LAYOUT = `
  body { display: grid; place-items: center; min-height: 100svh; }
  .solo { padding: clamp(16px, 4vw, 40px); display: grid; justify-items: center; gap: 18px; }
  .solo .stage { height: auto; width: min(86vw, calc(min(78svh, 760px) * var(--ar))); }
  .solo .card { cursor: grab; }
  .solo .stage:active .card { cursor: grabbing; }
  .solo p { margin: 0; color: #6f7d76; text-align: center;
            font: 600 12px/1.5 Inter, Pretendard, "Noto Sans KR", system-ui, sans-serif; }
`;

async function readText(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} — ${res.status}`);
  return await res.text();
}

const readCss = async (path) => `/* ── ${path} ── */\n${await readText(path)}`;

/**
 * `<script>` 안에 넣을 자바스크립트를 안전하게 만든다. HTML 파서는 스크립트 안이라도
 * **`</script` 를 만나면 거기서 끊는다** — 주석이든 문자열이든 가리지 않는다.
 * tilt-engine.js 는 사람이 고치는 파일이고 주석에 태그를 적을 수 있으므로, 넣기 직전에
 * 한 번 막아 둔다. `<\/script` 는 자바스크립트에서 `</script` 와 같은 문자열이다.
 */
const safeScript = (js) => js.replace(/<\/script/gi, "<\\/script");

async function buildExport() {
  const paths = ["./style.css", "./rarity.css"];
  if (effect.id !== "basic") paths.push(`./vendor/cards-css/${effect.id}.css`);
  const [css, engine] = await Promise.all([
    Promise.all(paths.map(readCss)).then((all) => all.join("\n\n")),
    readText("./tilt-engine.js"),
  ]);

  const basic = effect.id === "basic";
  const cls = basic ? "stage" : "stage holo-card";
  const attrs = basic ? "" : ` data-effect="${esc(effect.id)}"`;
  const rarity = basic ? "fullart" : effect.id;
  const title = art.name.replace(/\.[^.]+$/, "") || "holo card";
  const styleAttr =
    `--ar:${(art.w / art.h).toFixed(4)};--accent:${art.accent};--accent2:${art.accent2}`;

  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0e0b05">
<title>${esc(title)} · No.${pad2(effect.no)} ${esc(effect.ko)}</title>
<!-- 네오 채소 도감의 홀로 스튜디오에서 만든 파일입니다. CSS · JS · 이미지가 전부
     안에 들어 있어 이 파일 하나만 있으면 그대로 돕니다 (file:// 로 열어도 됩니다).
     포일 CSS 는 @kongyo2/cards-css 0.5.0 (MIT) 에서 왔습니다. -->
<style>
${css}

/* ── 내보내기 배치 ── */
${EXPORT_LAYOUT}
</style>
</head>
<body>
  <main class="solo">
    <div class="${cls}"${attrs} data-rarity="${esc(rarity)}" style="${esc(styleAttr)}">
      <div class="card" tabindex="0" role="img" aria-label="${esc(title)} 홀로그램 카드">
        <img class="art" width="${art.w}" height="${art.h}" alt="" src="${art.src}">
        <span class="foil holo-card__shine" aria-hidden="true"></span>
        <span class="glare holo-card__glare" aria-hidden="true"></span>
        <span class="grain" aria-hidden="true"></span>
        <span class="edge" aria-hidden="true"></span>
      </div>
    </div>
    <p>카드 위에서 마우스나 손가락을 움직여 보세요 — No.${pad2(effect.no)} ${esc(effect.ko)}</p>
  </main>
<script>
${safeScript(engine)}
<\/script>
<script>
(function () {
${safeScript(EXPORT_BOOT)}
})();
<\/script>
</body>
</html>
`;
}

downloadEl.addEventListener("click", async () => {
  if (!art) return;
  downloadEl.classList.add("is-busy");
  downloadEl.textContent = "만드는 중…";
  try {
    const html = await buildExport();
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const base = (art.name.replace(/\.[^.]+$/, "") || "card")
      .replace(/[^\w가-힣-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "card";
    const a = document.createElement("a");
    a.href = url;
    a.download = `holo-${pad2(effect.no)}-${effect.id}-${base}.html`;
    a.click();
    // 클릭 직후에 지우면 다운로드가 시작되기 전에 URL 이 사라지는 브라우저가 있다.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    downloadMetaEl.textContent = `${a.download} · ${kb(blob.size)} — 열어 보면 이 카드가 그대로 돕니다.`;
  } catch (err) {
    console.error(err);
    downloadMetaEl.textContent =
      `CSS 를 읽지 못해 만들지 못했습니다 (${err.message}). file:// 로 열었다면 npm run dev 로 띄운 뒤 다시 해 보세요.`;
  } finally {
    downloadEl.classList.remove("is-busy");
    downloadEl.textContent = "HTML 한 장으로 저장";
  }
});

renderChips();
