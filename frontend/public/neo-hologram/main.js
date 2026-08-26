import { CARDS, altText } from "./cards.mjs";
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

const MOTION_VARS = ["--mouse-x", "--mouse-y", "--rotate-x", "--rotate-y", "--glare", "--shadow-x", "--shadow-y"];

let pending = null;
let frame = 0;

function flush() {
  frame = 0;
  const job = pending;
  pending = null;
  if (!job) return;

  const { stage, x, y, intensity } = job;
  const rect = stage.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  const px = Math.max(0, Math.min(1, (x - rect.left) / rect.width));
  const py = Math.max(0, Math.min(1, (y - rect.top) / rect.height));

  stage.style.setProperty("--mouse-x", `${(px * 100).toFixed(2)}%`);
  stage.style.setProperty("--mouse-y", `${(py * 100).toFixed(2)}%`);
  stage.style.setProperty("--rotate-x", `${((0.5 - py) * 22).toFixed(2)}deg`);
  stage.style.setProperty("--rotate-y", `${((px - 0.5) * 25).toFixed(2)}deg`);
  stage.style.setProperty("--glare", intensity);
  stage.style.setProperty("--shadow-x", `${((0.5 - px) * 38).toFixed(1)}px`);
  stage.style.setProperty("--shadow-y", `${(18 + (0.5 - py) * 28).toFixed(1)}px`);
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
  stage.addEventListener("pointerenter", (e) => paint(stage, e.clientX, e.clientY, 0.8));
  stage.addEventListener("pointermove", (e) => paint(stage, e.clientX, e.clientY, 1));
  stage.addEventListener("pointerleave", () => reset(stage));
  stage.addEventListener("pointercancel", () => reset(stage));

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

  const shell = button
    ? '<button class="card" type="button">'
    : '<div class="card" tabindex="0" role="img">';
  stage.innerHTML = `
    ${shell}
      <img class="art" width="${card.w}" height="${card.h}" decoding="async"${lazy ? ' loading="lazy"' : ""}>
      <span class="foil" aria-hidden="true"></span>
      <span class="glare" aria-hidden="true"></span>
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
    `<span class="stat">${esc(card.statLabel)} ${card.stat}</span>`;

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
        <dt>${esc(card.statLabel)}</dt><dd>${card.stat}</dd>
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
  viewer.classList.remove("is-closing", "is-opening");
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
