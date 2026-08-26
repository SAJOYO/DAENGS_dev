import { CARDS, CARDS_CSS_EFFECTS, altText, statText } from "./cards.mjs";
import { autoOpenFromQuery, bindLongPress, isImmersive, openImmersive } from "./immersive.mjs";

const dexEl = document.querySelector("#dex");
const countEl = document.querySelector("#count");
const viewer = document.querySelector("#viewer");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const esc = (s = "") =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const pad2 = (n) => String(n).padStart(2, "0");

/* ── 기울기 / 홀로그램 ─────────────────────────────────────
   rAF 는 카드마다 두지 않고 하나만 돌린다. 포인터는 어차피 한 번에 한 장 위에만
   있으므로, 가장 최근 입력만 남겨 두었다가 다음 프레임에 그 카드만 갱신한다. */

const MOTION_VARS = [
  "--mouse-x", "--mouse-y", "--rotate-x", "--rotate-y", "--glare", "--shadow-x", "--shadow-y",
  // cards-css 포일이 읽는 값. 이름도 계산식도 그쪽 규약이라 우리 쪽에서 안 쓰더라도
  // 같이 지워야 카드에서 손을 뗐을 때 정면 상태로 돌아간다.
  "--pointer-from-left", "--pointer-from-top", "--pointer-from-center",
];

let pending = null;
let frame = 0;
/** 지금 포인터가 올라가 있는 카드. 자이로가 이 카드는 건드리지 않는다. */
let pointerStage = null;

const clamp01 = (n) => Math.max(0, Math.min(1, n));

/**
 * 기울기를 실제로 CSS 변수에 쓰는 곳. **입력 소스가 뭐든 결국 여기로 들어온다** —
 * 포인터든, 폰 자이로든, 네이티브 앱이 밀어 넣는 센서 값이든 마찬가지다.
 * 그래서 소스를 늘릴 때 이 함수는 안 건드린다.
 *
 * @param {number} px 카드 안에서의 가로 위치 0~1 (0=왼쪽 끝)
 * @param {number} py 세로 위치 0~1
 * @param {number} intensity 포일 세기 0~1
 */
function writeTilt(stage, px, py, intensity) {
  stage.style.setProperty("--mouse-x", `${(px * 100).toFixed(2)}%`);
  stage.style.setProperty("--mouse-y", `${(py * 100).toFixed(2)}%`);
  stage.style.setProperty("--rotate-x", `${((0.5 - py) * 22).toFixed(2)}deg`);
  stage.style.setProperty("--rotate-y", `${((px - 0.5) * 25).toFixed(2)}deg`);
  stage.style.setProperty("--glare", intensity);
  stage.style.setProperty("--shadow-x", `${((0.5 - px) * 38).toFixed(1)}px`);
  stage.style.setProperty("--shadow-y", `${(18 + (0.5 - py) * 28).toFixed(1)}px`);

  // cards-css 포일용. 0~1 의 맨숫자(단위 없음)라 위의 % · deg 와 섞이지 않는다.
  // from-center 는 모서리가 1 이 아니라 반지름 0.5 를 1 로 보는 값이다 — reverse 가
  // 이걸로 가운데를 죽이고 가장자리를 살리므로, 정규화를 바꾸면 그 티어가 무너진다.
  stage.style.setProperty("--pointer-from-left", px.toFixed(3));
  stage.style.setProperty("--pointer-from-top", py.toFixed(3));
  stage.style.setProperty("--pointer-from-center", Math.min(Math.hypot(px - 0.5, py - 0.5) / 0.5, 1).toFixed(3));
}

function flush() {
  frame = 0;
  const job = pending;
  pending = null;
  if (!job) return;

  const { stage, x, y, intensity } = job;
  const rect = stage.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  writeTilt(stage, clamp01((x - rect.left) / rect.width), clamp01((y - rect.top) / rect.height), intensity);
}

function paint(stage, x, y, intensity = 1) {
  if (reducedMotion) return;
  pending = { stage, x, y, intensity };
  if (!frame) frame = requestAnimationFrame(flush);
}

/** 인라인으로 덮어썼던 값만 지우면 style.css 의 기본값(정면)으로 돌아간다. */
function reset(stage) {
  if (pending?.stage === stage) pending = null;
  for (const name of MOTION_VARS) stage.style.removeProperty(name);
}

/**
 * @param {HTMLElement} stage
 * @param {boolean} arrowTilt Shift+화살표로 기울일지. 맨 화살표는 카드 사이 이동에
 *   쓰이므로(그리드는 dexEl, 확대 뷰는 viewer 가 처리) 기울이기는 Shift 를 요구한다.
 *   확대 뷰에서는 아예 끈다.
 */
function bindTilt(stage, { arrowTilt = true } = {}) {
  const grab = (e, intensity) => { pointerStage = stage; paint(stage, e.clientX, e.clientY, intensity); };
  const release = () => { if (pointerStage === stage) pointerStage = null; reset(stage); };

  stage.addEventListener("pointerenter", (e) => grab(e, 0.8));
  stage.addEventListener("pointermove", (e) => grab(e, 1));
  stage.addEventListener("pointerleave", release);
  stage.addEventListener("pointercancel", release);

  const card = stage.querySelector(".card");
  card.addEventListener("blur", () => reset(stage));

  if (!arrowTilt) return;

  let kx = 0;
  let ky = 0;
  card.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { kx = ky = 0; return reset(stage); }
    if (!e.shiftKey) return;   // 맨 화살표는 카드 이동 — 여기서 가로채면 안 된다

    const step = 9;
    if (e.key === "ArrowLeft") kx -= step;
    else if (e.key === "ArrowRight") kx += step;
    else if (e.key === "ArrowUp") ky -= step;
    else if (e.key === "ArrowDown") ky += step;
    else return;

    e.preventDefault();
    kx = Math.max(-45, Math.min(45, kx));
    ky = Math.max(-45, Math.min(45, ky));
    const rect = stage.getBoundingClientRect();
    paint(stage, rect.left + rect.width * (0.5 + kx / 100), rect.top + rect.height * (0.5 + ky / 100), 1);
  });
}

/* ── 기울기 입력 (2) 자이로 · 네이티브 ────────────────────
   **카드를 눌러 확대한 상태에서만** 폰을 기울이면 카드가 따라 기운다. 그리드는
   손대지 않는다. **PC 도 아무것도 안 바뀐다** — 데스크톱에는 센서가 없어서 이벤트가
   한 번도 안 오고, 위의 포인터 코드가 그대로 돈다.

   값이 들어오는 문은 두 개다.
     - 브라우저: `deviceorientation` 이벤트
     - 네이티브 앱: WebView 에서 `window.__neoTilt(beta, gamma)` 를 부른다
   둘 다 아래 feedOrientation() 하나로 모이고, 거기서 writeTilt() 로 나간다.

   **브라우저 쪽은 secure context 에서만 켜진다.** HTTPS 이거나 localhost 여야 하고,
   지금 배포(nginx :80)는 둘 다 아니다 — 폰에서 daengs.~ 로 들어가면 에러 없이 그냥
   조용히 안 켜진다. 개발 중에는 USB 로 `adb reverse tcp:3000 tcp:3000` 을 걸고
   폰에서 localhost:3000 으로 보면 인증서 없이 확인된다.
   네이티브 브릿지(`__neoTilt`)는 웹 API 를 안 거치므로 이 제약이 없다. */

/** 이 각도(도)만큼 기울이면 카드가 끝까지 돈다. 키우면 둔해지고 줄이면 예민해진다. */
const TILT_RANGE = 20;

/** **확대 뷰에 떠 있는 한 장에만 적용한다.** 그리드에서는 열두 장이 한꺼번에 같은
 *  각도로 도는데, 폰에서 매 프레임 열두 장을 갱신하는 비용도 크고 보기에도 산만하다.
 *  카드 한 장을 들고 기울여 보는 게 원래 하려던 동작이기도 하다.
 *
 *  참조를 들고 있지 않고 그때그때 찾는다 — 확대 뷰의 .stage 는 ‹ › 로 카드를 넘길
 *  때마다 새로 만들어지므로, 붙잡아 두면 넘긴 뒤 죽은 노드를 기울이게 된다. */
const tiltTarget = () => (viewer.open ? viewer.querySelector(".stage") : null);

let tiltBase = null;      // 처음 들어온 값을 '정면'으로 삼는다
let tiltPending = null;
let tiltFrame = 0;

function tiltFlush() {
  tiltFrame = 0;
  const job = tiltPending;
  tiltPending = null;
  if (!job) return;

  const stage = tiltTarget();
  // 손가락이 올라가 있으면 포인터가 이긴다. 두 소스가 같은 카드를 두고 매 프레임
  // 싸우는 걸 막는다 — 폰에서도 화면을 문지르면 그쪽이 우선이다.
  if (!stage || stage === pointerStage) return;
  writeTilt(stage, job.px, job.py, 1);
}

/**
 * @param {number} beta  앞뒤 기울기 (deviceorientation 규약, 도 단위)
 * @param {number} gamma 좌우 기울기
 */
function feedOrientation(beta, gamma) {
  if (reducedMotion) return;
  if (typeof beta !== "number" || typeof gamma !== "number") return;
  if (Number.isNaN(beta) || Number.isNaN(gamma)) return;

  // 절대 각도가 아니라 '처음 든 자세에서 얼마나 움직였는지'를 쓴다. 폰을 눕혀서 보든
  // 세워서 보든 처음 자세가 정면이 되므로, 들자마자 카드가 홱 돌아가지 않는다.
  if (!tiltBase) tiltBase = { beta, gamma };
  let dx = gamma - tiltBase.gamma;   // 좌우
  let dy = beta - tiltBase.beta;     // 앞뒤

  // 가로로 눕히면 센서 축과 화면 축이 어긋난다. 화면이 돈 만큼 되돌려 준다.
  switch (screen.orientation?.angle ?? 0) {
    case 90:  [dx, dy] = [dy, -dx]; break;
    case 180: [dx, dy] = [-dx, -dy]; break;
    case 270: [dx, dy] = [-dy, dx]; break;
    default: break;
  }

  tiltPending = {
    px: clamp01(0.5 + dx / TILT_RANGE / 2),
    py: clamp01(0.5 + dy / TILT_RANGE / 2),
  };
  if (!tiltFrame) tiltFrame = requestAnimationFrame(tiltFlush);
}

/** 네이티브 앱용 문. Android 쪽에서 SensorManager 값을 그대로 넘기면 된다:
 *  `webView.evaluateJavascript("window.__neoTilt(" + beta + "," + gamma + ")", null)` */
window.__neoTilt = feedOrientation;

/* 브라우저 자이로 붙이기. iOS 13+ 는 사용자 제스처 안에서 권한을 물어야 해서, 첫
   탭까지 기다렸다가 붙인다. 안드로이드는 물을 게 없어서 바로 붙는다.
   권한을 거절해도 아무 일도 안 일어난다 — 포인터가 그대로 남는다. */
function startDeviceOrientation() {
  window.addEventListener("deviceorientation", (e) => feedOrientation(e.beta, e.gamma));
}

if (!reducedMotion && typeof DeviceOrientationEvent !== "undefined") {
  if (typeof DeviceOrientationEvent.requestPermission === "function") {
    document.addEventListener("pointerdown", function ask() {
      document.removeEventListener("pointerdown", ask);
      DeviceOrientationEvent.requestPermission()
        .then((r) => { if (r === "granted") startDeviceOrientation(); })
        .catch(() => {});
    }, { once: true });
  } else {
    startDeviceOrientation();
  }
}

/* ── 카드 만들기 ───────────────────────────────────────── */

/**
 * @param {object} card
 * @param {boolean} opts.lazy   그리드는 lazy, 확대 뷰는 즉시 로드
 * @param {boolean} opts.button 그리드에서는 눌러서 여는 버튼, 확대 뷰에서는
 *   이미 열린 상태라 누를 게 없으므로 role=img 인 포커스 가능한 그림으로 둔다.
 */
function makeStage(card, { lazy = true, button = true } = {}) {
  const stage = document.createElement("div");
  stage.className = "stage";
  stage.style.setProperty("--ar", (card.w / card.h).toFixed(4));
  stage.style.setProperty("--accent", card.accent);
  stage.style.setProperty("--accent2", card.accent2);
  stage.dataset.rarity = card.rarity ?? "fullart";

  // cards-css 는 `.holo-card[data-effect="x"] .holo-card__shine` 을 찾는다. vendor 의
  // CSS 를 한 글자도 안 고치려고, 선택자를 바꾸는 대신 우리 요소에 그쪽 이름을 얹는다.
  // rarity 가 CARDS_CSS_EFFECTS 에 있을 때만 붙는다 — 지금은 No.01(immersive)만 빠진다.
  if (CARDS_CSS_EFFECTS.has(stage.dataset.rarity)) {
    stage.classList.add("holo-card");
    stage.dataset.effect = stage.dataset.rarity;
  }

  const shell = button
    ? '<button class="card" type="button">'
    : '<div class="card" tabindex="0" role="img">';
  stage.innerHTML = `
    ${shell}
      <img class="art" width="${card.w}" height="${card.h}" decoding="async"${lazy ? ' loading="lazy"' : ""}>
      <span class="foil holo-card__shine" aria-hidden="true"></span>
      <span class="glare holo-card__glare" aria-hidden="true"></span>
      <span class="grain" aria-hidden="true"></span>
      <span class="edge" aria-hidden="true"></span>
    ${button ? "</button>" : "</div>"}`;

  const img = stage.querySelector(".art");
  img.src = card.art;

  if (button) {
    img.alt = altText(card);
  } else {
    // role=img 컨테이너가 이미 설명을 갖고 있으므로 안쪽 img 는 중복해 읽히면 안 된다
    img.alt = "";
    stage.querySelector(".card").setAttribute("aria-label", altText(card));
  }

  // 확대 뷰에서는 화살표가 이전/다음 카드 이동에 쓰이므로 기울기에 안 쓴다
  bindTilt(stage, { arrowTilt: button });
  return stage;
}

/* ── 도감 그리드 ───────────────────────────────────────── */

CARDS.forEach((card, i) => {
  const li = document.createElement("li");
  li.className = "slot";

  const frameEl = document.createElement("div");
  frameEl.className = "frame";
  const stage = makeStage(card);
  frameEl.append(stage);

  const caption = document.createElement("div");
  caption.className = "caption";
  caption.innerHTML =
    `<span class="no">No. ${pad2(card.no)}</span>` +
    `<span class="name">${esc(card.name)}</span>` +
    `<span class="stat">${esc(statText(card))}</span>`;

  stage.querySelector(".card").addEventListener("click", () => open(i));

  // ☆☆☆ 는 꾹 눌러 안으로 들어간다. 게이지가 다 차기 전에 떼면 위의 click 이 살아서
  // 평범한 확대 뷰가 열린다 — 기존 동작은 그대로 남는다.
  if (isImmersive(card)) {
    // --accent 는 .stage 안에 갇혀 있어 캡션이 못 본다. 배지가 카드 색을 타도록 li 에 얹는다.
    li.style.setProperty("--accent", card.accent);
    // 자리는 누를 때마다 다시 잰다 — 그리드가 스크롤됐을 수 있다
    bindLongPress(stage, stage.querySelector(".card"),
      () => openImmersive(card, stage.getBoundingClientRect()));

    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "im-badge";
    badge.textContent = "★★★ 꾹 눌러서 들어가기";
    badge.addEventListener("click", () => openImmersive(card, stage.getBoundingClientRect()));
    caption.append(badge);
  }

  li.append(frameEl, caption);
  dexEl.append(li);
});

countEl.textContent = `${pad2(CARDS.length)} / ${pad2(CARDS.length)} 수집`;

/** 지금 몇 열로 깔려 있는지 — 화면 폭에 따라 1~3열이라 ↑/↓ 이동에 필요하다. */
function columnCount() {
  const tracks = getComputedStyle(dexEl).gridTemplateColumns.split(" ").filter(Boolean);
  return Math.max(1, tracks.length);
}

const focusSlot = (i) =>
  dexEl.children[Math.max(0, Math.min(CARDS.length - 1, i))]?.querySelector(".card")?.focus();

// 화살표로 카드 사이를 옮겨 다닌다. Shift+화살표는 기울이기라 넘긴다.
dexEl.addEventListener("keydown", (e) => {
  if (e.shiftKey) return;
  const card = e.target.closest(".slot .card");
  if (!card) return;

  const here = [...dexEl.children].findIndex((li) => li.contains(card));
  if (here < 0) return;

  const cols = columnCount();
  let to;
  if (e.key === "ArrowLeft") to = here - 1;
  else if (e.key === "ArrowRight") to = here + 1;
  else if (e.key === "ArrowUp") to = here - cols;
  else if (e.key === "ArrowDown") to = here + cols;
  else if (e.key === "Home") to = 0;
  else if (e.key === "End") to = CARDS.length - 1;
  else return;

  e.preventDefault();
  focusSlot(to);
});

/* ── 확대 뷰 ───────────────────────────────────────────── */

let index = 0;

function detailMarkup(card) {
  const move = card.moveNote
    ? `${esc(card.move)}<small>${esc(card.moveNote)}</small>`
    : esc(card.move);

  return `
    <div class="detail">
      <p class="tagline">${esc(card.tagline)}</p>
      <h2>${esc(card.name)}</h2>
      <dl>
        <dt>No.</dt><dd>${pad2(card.no)} / ${pad2(CARDS.length)}</dd>
        <dt>Code</dt><dd>${esc(card.code)}</dd>
        <dt>Type</dt><dd>${esc(card.type)}</dd>
        <dt>Move</dt><dd>${move}</dd>
        <dt>${esc(card.statLabel || "Stat")}</dt><dd>${card.stat}</dd>
      </dl>
      <p class="flavor">${esc(card.flavor)}</p>
      <span class="edition">${esc(card.edition)}</span>
      <div class="viewer-nav">
        <button type="button" data-nav="-1">‹ 이전</button>
        <button type="button" data-nav="1">다음 ›</button>
        <button type="button" class="close" data-close>닫기 (Esc)</button>
      </div>
    </div>`;
}

function render() {
  const card = CARDS[index];
  viewer.replaceChildren();

  const inner = document.createElement("div");
  inner.className = "viewer-inner";
  inner.append(makeStage(card, { lazy: false, button: false }));
  inner.insertAdjacentHTML("beforeend", detailMarkup(card));
  inner.insertAdjacentHTML("beforeend",
    '<p class="sheet-hint">탭하면 상세 · 좌우로 넘기기 · 아래로 쓸어내려 닫기</p>');
  viewer.append(inner);
}

/* ── 그리드 슬롯 ↔ 가운데 날아가기 (FLIP) ──────────────────
   카드가 팍 나타나지 않고 눌린 자리에서 가운데로 옮겨오게 한다.
   1) 그리드 카드의 현재 위치를 잰다
   2) 확대 뷰를 띄우고 큰 카드의 최종 위치를 잰다
   3) 그 차이만큼 되돌린 상태에서 시작해 0 으로 애니메이션한다
   닫을 때는 같은 keyframes 를 뒤집어 쓰고, 다 날아간 뒤에 실제로 닫는다.

   변환은 .card 가 아니라 .stage 에 건다 — .card 의 transform 은 마우스 기울기가
   쓰고 있어서 같은 속성을 두고 싸우게 된다. 두 카드가 같은 그림이라 비율이 같고,
   그래서 균일 배율이라 찌그러지지 않는다. */

const FLIGHT = { duration: 280, easing: "cubic-bezier(.2,.75,.25,1)" };

let flight = null;   // 진행 중인 비행
let closing = false;

const gridStage = (i) => dexEl.children[i]?.querySelector(".stage") || null;

/** 날아가는 동안(그리고 열려 있는 동안) 그리드의 원본 카드는 숨겨 둔다 — 안 그러면 두 장으로 보인다. */
function showGridStage(i, show) {
  const stage = gridStage(i);
  if (stage) stage.style.visibility = show ? "" : "hidden";
}

function flightKeyframes(fromRect, toRect) {
  const scale = fromRect.width / toRect.width;
  const dx = fromRect.left + fromRect.width / 2 - (toRect.left + toRect.width / 2);
  const dy = fromRect.top + fromRect.height / 2 - (toRect.top + toRect.height / 2);
  return [
    { transform: `translate(${dx}px, ${dy}px) scale(${scale})` },
    { transform: "translate(0px, 0px) scale(1)" },
  ];
}

function open(i) {
  // 닫는 중에 다시 열면 그 비행은 버리고 즉시 정리한다 (막아버리면 클릭이 씹힌다)
  if (closing) finishClose();

  const from = gridStage((i + CARDS.length) % CARDS.length)?.getBoundingClientRect();
  const wasOpen = viewer.open;

  showGridStage(index, true);
  index = (i + CARDS.length) % CARDS.length;
  render();

  if (!wasOpen) {
    viewer.showModal();
    document.body.classList.add("is-viewing");
    viewer.classList.add("is-opening");
    setTimeout(() => viewer.classList.remove("is-opening"), FLIGHT.duration + 80);
  }
  showGridStage(index, false);

  const big = viewer.querySelector(".stage");
  if (!wasOpen && from && big && !reducedMotion) {
    flight?.cancel();
    flight = big.animate(flightKeyframes(from, big.getBoundingClientRect()), FLIGHT);
  }
}

/* ── 좌우로 넘기기 ─────────────────────────────────────────
   render() 가 확대 뷰 내용을 통째로 갈아치우므로, 나가는 카드는 사라지기 전에
   붙잡아 원래 자리에 fixed 로 띄워 두고(잔상) 진행 방향 반대쪽으로 밀어낸다.
   들어오는 카드는 진행 방향에서 미끄러져 들어온다.

   잔상은 dialog 에 직접 붙이는데, 다음 render() 의 replaceChildren() 이 알아서
   치워준다. viewer.querySelector(".stage") 가 잔상을 잡을 걱정은 없다 —
   .viewer-inner 가 문서 순서상 앞이라 진짜 카드가 먼저 걸린다. */

const SLIDE = { duration: 240, easing: "cubic-bezier(.25,.8,.3,1)" };

function slide(outgoing, fromRect, incoming, delta) {
  if (reducedMotion || !outgoing || !fromRect || !incoming) return;

  const dir = delta > 0 ? 1 : -1;
  const shift = Math.round(fromRect.width * 0.55);

  Object.assign(outgoing.style, {
    position: "fixed",
    left: `${fromRect.left}px`,
    top: `${fromRect.top}px`,
    width: `${fromRect.width}px`,
    height: `${fromRect.height}px`,
    margin: "0",
    pointerEvents: "none",
  });
  viewer.append(outgoing);

  const drop = () => outgoing.remove();
  outgoing.animate(
    [{ transform: "translateX(0)", opacity: 1 },
     { transform: `translateX(${-dir * shift}px)`, opacity: 0 }],
    SLIDE
  ).finished.then(drop, drop);
  setTimeout(drop, SLIDE.duration + 200);   // 숨은 탭에선 finished 가 안 온다

  incoming.animate(
    [{ transform: `translateX(${dir * shift}px)`, opacity: 0 },
     { transform: "translateX(0)", opacity: 1 }],
    SLIDE
  );

  // 설명도 같이 살짝 떠오르게 — 카드만 움직이고 글자가 툭 바뀌면 어긋나 보인다
  viewer.querySelector(".detail")?.animate(
    [{ opacity: 0, transform: `translateX(${dir * 14}px)` },
     { opacity: 1, transform: "translateX(0)" }],
    { duration: 220, easing: SLIDE.easing }
  );
}

function go(delta) {
  if (!viewer.open || closing) return;

  const outgoing = viewer.querySelector(".stage");
  const fromRect = outgoing?.getBoundingClientRect();

  showGridStage(index, true);
  index = (index + delta + CARDS.length) % CARDS.length;
  render();
  showGridStage(index, false);

  viewer.classList.remove("show-detail");
  const incoming = viewer.querySelector(".stage");
  incoming?.querySelector(".card")?.focus();
  slide(outgoing, fromRect, incoming, delta);
}

/* 뒷정리를 dialog 의 close 이벤트에 맡기지 않는다.
   테스트한 Chrome(151)에서 <dialog> 가 close 이벤트를 발화하지 않았다 — 평범한 dialog 를
   페이지 스크립트로 열고 닫아도 open 은 true→false 로 바뀌는데 리스너는 호출되지 않는다.
   그 이벤트에 정리를 걸어두면 .is-viewing 이 안 지워지고, 그 클래스의
   pointer-events:none 때문에 페이지 전체가 먹통이 된다.
   그래서 닫는 길을 전부 closeViewer() 로 모으고, close 이벤트는 (동작하는 브라우저를 위한)
   보조 수단으로만 남긴다. afterClose 는 여러 번 불려도 안전하다. */

function afterClose() {
  if (viewer.open) return;
  closing = false;
  flight = null;
  viewer.classList.remove("is-closing", "is-opening", "show-detail");
  document.body.classList.remove("is-viewing");
  showGridStage(index, true);
  // 넘겨 봤다면 처음 연 카드가 아니라 마지막으로 보던 카드로 돌아간다
  dexEl.children[index]?.querySelector(".card")?.focus();
}

function finishClose() {
  if (viewer.open) viewer.close();
  afterClose();
}

function closeViewer() {
  if (!viewer.open) return afterClose();
  if (closing) return;

  const big = viewer.querySelector(".stage");
  // ←/→ 로 넘겨 봤다면 돌아갈 카드가 화면 밖일 수 있다. 먼저 끌어와야 제자리로 간다.
  const target = gridStage(index);
  target?.scrollIntoView({ block: "nearest", inline: "nearest" });

  if (reducedMotion || !big || !target) return finishClose();

  closing = true;
  viewer.classList.add("is-closing");
  document.body.classList.remove("is-viewing");   // 날아가는 동안 배경이 같이 선명해진다

  // 넘기던 중이면 미끄러짐이 아직 transform 을 물고 있다. 전부 취소해 원점으로
  // 돌려놔야 날아갈 거리를 정확히 잴 수 있다.
  flight?.cancel();
  big.getAnimations().forEach((a) => a.cancel());

  const toSlot = target.getBoundingClientRect();
  const fromHere = big.getBoundingClientRect();
  flight = big.animate(flightKeyframes(toSlot, fromHere).reverse(), FLIGHT);

  // 백그라운드 탭에서는 애니메이션 타임라인이 멈춰 finished 가 영영 안 온다.
  // 그때 dialog 가 열린 채 굳어버리므로 시간 제한을 함께 건다. finishClose 는 멱등.
  flight.finished.then(finishClose, finishClose);
  setTimeout(finishClose, FLIGHT.duration + 250);
}

viewer.addEventListener("click", (e) => {
  // 딱 dialog 자신이 눌렸다면 카드 바깥 = 배경을 누른 것
  if (e.target === viewer) return closeViewer();

  const nav = e.target.closest("[data-nav]");
  if (nav) return go(Number(nav.dataset.nav));
  if (e.target.closest("[data-close]")) closeViewer();
});

viewer.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
  // Esc 는 dialog 가 알아서 닫지만, 정리까지 확실히 하려고 직접 처리한다.
  // 네이티브가 먼저 닫아버려도 afterClose 가 멱등이라 결과는 같다.
  else if (e.key === "Escape") { e.preventDefault(); closeViewer(); }
});


/* ── 폰 확대 뷰 제스처 ────────────────────────────────────
   폰에서는 카드가 화면을 가득 채우고 설명은 감춰져 있다 (style.css 의 760px 블록).
   감춘 걸 도로 꺼내는 길과, 카드를 넘기는 길이 필요하다.

     탭          설명 열기/닫기
     좌우 스와이프  이전 / 다음 카드
     아래로 스와이프 닫기

   **좌우를 '설명 열기'가 아니라 '카드 넘기기'에 준 이유**: 화면을 채운 사진에서
   좌우로 미는 건 사진첩·스토리·카드 앱에서 거의 예외 없이 '다음 항목'이다. 여기에
   설명을 걸면 사람들이 제일 먼저 해보는 동작이 엉뚱하게 반응한다.

   **제스처는 카드(.stage) 위에서 시작한 것만 받는다.** 배경을 탭하면 닫히는 기존
   동작(viewer 의 click 처리)과 부딪히지 않게 하려는 것이다. 설명 시트 안은 스크롤
   해야 하므로 애초에 여기까지 오지 않는다.

   데스크톱은 이 블록 전체가 놀고 있다 — 마우스로 드래그해도 아무 일도 안 일어난다. */

const phoneViewer = matchMedia("(max-width: 760px)");

/** 손가락이 이만큼 움직여야 스와이프로 친다 (px) */
const SWIPE = {
  side: 46,     // 좌우 — 카드 넘기기
  down: 90,     // 아래 — 닫기. 넘기기보다 크게 잡아야 비스듬한 손짓이 안 닫는다
  tap: 12,      // 이 안에서 멈추면 탭
  tapMs: 700,   // 오래 누르고 있다 떼는 건 탭으로 안 친다
};

let gesture = null;

viewer.addEventListener("pointerdown", (e) => {
  gesture = null;
  if (!phoneViewer.matches || !viewer.open) return;
  if (!e.target.closest(".stage")) return;
  gesture = { x: e.clientX, y: e.clientY, t: e.timeStamp };
});

viewer.addEventListener("pointercancel", () => { gesture = null; });

viewer.addEventListener("pointerup", (e) => {
  const g = gesture;
  gesture = null;
  if (!g) return;

  const dx = e.clientX - g.x;
  const dy = e.clientY - g.y;

  // 아래로 크게 쓸어내리면 닫는다. 세로가 가로보다 확실히 커야 한다 —
  // 안 그러면 비스듬히 넘기려던 게 닫혀 버린다.
  if (dy > SWIPE.down && Math.abs(dy) > Math.abs(dx)) return closeViewer();

  // 좌우로 밀면 카드를 넘긴다. 왼쪽으로 밀면 다음 장이다 (사진첩과 같은 방향).
  if (Math.abs(dx) > SWIPE.side && Math.abs(dx) > Math.abs(dy)) return go(dx < 0 ? 1 : -1);

  // 거의 안 움직였으면 탭 — 설명을 여닫는다
  if (Math.hypot(dx, dy) < SWIPE.tap && e.timeStamp - g.t < SWIPE.tapMs) {
    viewer.classList.toggle("show-detail");
  }
});

viewer.addEventListener("cancel", afterClose);
viewer.addEventListener("close", afterClose);

/* ── 꾹 눌렀을 때 브라우저가 끼어드는 것 막기 ─────────────
   선택·드래그는 style.css 가 CSS 로 끈다. 남는 건 길게 누르기 메뉴인데, 안드로이드
   크롬은 -webkit-touch-callout 을 보지 않아 사진 위에서 "이미지 다운로드" 메뉴가
   그대로 뜨고 그러면 이머시브로 들어가는 꾹 누르기가 끊긴다. 그래서 사진·카드
   위에서만 메뉴를 막는다 — 페이지 나머지(링크·글)에서는 오른쪽 버튼이 그대로 산다.
   immersive.mjs 도 꾹 누르는 동안 같은 걸 막지만 그건 게이지가 도는 순간뿐이라,
   확대 뷰나 이머시브 장면의 사진은 여기서만 걸린다. */
const NO_MENU = "img, .card, .stage, .dio, .viewer";

for (const ev of ["contextmenu", "dragstart"]) {
  document.addEventListener(ev, (e) => {
    if (e.target instanceof Element && e.target.closest(NO_MENU)) e.preventDefault();
  });
}

/* ?im=<카드 id> 로 열면 이머시브로 바로 들어간다 — live-server 가 새로 고칠 때마다
   다시 꾹 누르지 않아도 되도록. 개발 편의용이고 평소 경로에는 영향이 없다. */
autoOpenFromQuery();
