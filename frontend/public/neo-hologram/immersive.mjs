/* ☆☆☆ 이머시브 뷰 — 꾹 누르면 카드 안으로 들어간다.
   무대 구조와 각 평면이 하는 일은 immersive.css 맨 위에 적어 뒀다.
   여기는 (1) 꾹 누르기 (2) 장면 조립 (3) 시선 입력 세 가지를 맡는다. */

import { CARDS, altText } from "./cards.mjs";

const dialog = document.querySelector("#immersive");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 꾹 누르는 시간. immersive.css 의 게이지가 이 값을 --hold-ms 로 받아 쓴다. */
const HOLD_MS = 520;

/** 이만큼 움직이면 꾹이 아니라 스크롤/드래그로 본다. 스크롤을 막지 않으려면 필요하다. */
const SLOP = 10;

/* esc 는 main.js 에도 있다. 거기서 가져오면 main → immersive → main 순환이 되므로
   짧은 함수 하나는 각자 갖는다. */
const esc = (s = "") =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* 장면은 매번 같은 모양이어야 한다 — 열 때마다 먼지가 다른 자리에 있으면 카드가
   아니라 스크린세이버가 된다. 그래서 카드 id 로 씨를 만들어 쓴다. */
const seedOf = (str) => [...str].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

function rngFrom(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

const vars = (o) =>
  Object.entries(o).map(([k, v]) => `--${k}:${v}`).join(";");

/* ── 시선 ──────────────────────────────────────────────────
   목표(tx, ty)를 정해 두고 현재값(cx, cy)이 관성으로 따라간다. 마우스를 그대로
   꽂으면 배경이 손을 따라 뚝뚝 끊기고, 자이로는 값이 떨려서 그대로 쓸 수 없다. */

let dio = null;
let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;

function tick() {
  raf = 0;
  if (!dio) return;
  cx += (tx - cx) * .11;
  cy += (ty - cy) * .11;
  dio.style.setProperty("--px", cx.toFixed(4));
  dio.style.setProperty("--py", cy.toFixed(4));
  if (Math.abs(tx - cx) > 4e-4 || Math.abs(ty - cy) > 4e-4) raf = requestAnimationFrame(tick);
}

function aim(x, y) {
  if (reducedMotion) return;
  tx = Math.max(-1, Math.min(1, x));
  ty = Math.max(-1, Math.min(1, y));
  if (!raf) raf = requestAnimationFrame(tick);
}

/* 자이로. 처음 들어온 자세를 정면으로 삼는다 — 누워서 보든 앉아서 보든
   "지금 들고 있는 각도"가 기준이어야 한다. */
let gyroBase = null;

function onOrient(e) {
  if (e.gamma == null || e.beta == null) return;
  if (!gyroBase) gyroBase = { g: e.gamma, b: e.beta };
  aim((e.gamma - gyroBase.g) / 30, (e.beta - gyroBase.b) / 30);
}

async function startGyro() {
  const DOE = window.DeviceOrientationEvent;
  if (!DOE || reducedMotion) return;
  try {
    // iOS 는 사용자 제스처 안에서만 물어볼 수 있다. 꾹 누른 520ms 는 아직
    // 제스처 유효 시간(수 초) 안이라 여기서 요청해도 통과한다.
    if (typeof DOE.requestPermission === "function") {
      if (await DOE.requestPermission() !== "granted") return;
    }
  } catch {
    return;   // 권한 거부. 포인터/드래그로만 본다
  }
  gyroBase = null;
  addEventListener("deviceorientation", onOrient);
}

function stopGyro() {
  removeEventListener("deviceorientation", onOrient);
  gyroBase = null;
}

/* ── 장면 조립 ─────────────────────────────────────────── */

function build(card) {
  const rng = rngFrom(seedOf(card.id));
  const scene = card.scene || {};

  const el = document.createElement("div");
  el.className = "dio";
  el.style.setProperty("--accent", card.accent);
  el.style.setProperty("--accent2", card.accent2);
  // 레이어 원화가 없으면 카드 그림으로 때운다. 보기엔 이상하지만 터지지는 않는다.
  el.style.setProperty("--back", `url("${scene.back ?? card.art}")`);

  // --i 는 진입 시차 순번이다. 뒤에서 앞으로 0..6.
  el.innerHTML = `
    <div class="dio-layer dio-sky" style="--i:0"><div class="dio-move"></div></div>
    <div class="dio-layer dio-back" style="--i:1"><div class="dio-move"></div></div>
    <div class="dio-layer dio-rays" style="--i:2"><div class="dio-move"></div></div>
    <div class="dio-layer dio-motes" style="--i:3"><div class="dio-move"></div></div>
    <div class="dio-layer dio-subject" style="--i:4"><div class="dio-move">
      <img class="dio-hero" src="${esc(scene.subject ?? card.art)}"
           alt="${esc(altText(card))}" decoding="async">
    </div></div>
    <div class="dio-layer dio-hud" style="--i:5"><div class="dio-move">
      <div class="dio-meta">
        <span class="dio-rank" aria-hidden="true">★★★</span>
        <strong>${esc(card.name)}</strong>
        <span class="dio-code">${esc(card.code)} · ${esc(card.edition)}</span>
        ${scene.place ? `<span class="dio-place">${esc(scene.place)}</span>` : ""}
      </div>
    </div></div>
    <div class="dio-layer dio-fore" style="--i:6"><div class="dio-move"></div></div>
    <button type="button" class="dio-exit">닫기 (Esc)</button>
    <p class="dio-tip">기울이거나 끌어서 둘러보세요</p>`;

  // 먼지 — 카드 뒤에서 느리게 떠다닌다
  const motes = el.querySelector(".dio-motes .dio-move");
  for (let i = 0; i < (scene.motes ?? 30); i++) {
    const m = document.createElement("span");
    m.className = "mote";
    m.style.cssText = vars({
      x: (rng() * 100).toFixed(1),
      y: (rng() * 100).toFixed(1),
      s: (1.4 + rng() * 3.2).toFixed(1),
      o: (.22 + rng() * .6).toFixed(2),
      t: (7 + rng() * 9).toFixed(1),
      d: (rng() * 14).toFixed(1),
      dx: (-22 + rng() * 44).toFixed(0),
      dy: (-28 + rng() * 14).toFixed(0),
    });
    motes.append(m);
  }

  // 앞 잎사귀 — 크고 흐리게. 초점이 카드에 맞은 것처럼 보이게 하는 층이다
  const fore = el.querySelector(".dio-fore .dio-move");
  for (let i = 0; i < (scene.leaves ?? 7); i++) {
    const l = document.createElement("span");
    l.className = "leaf";
    l.style.cssText = vars({
      x: (rng() * 108 - 6).toFixed(1),
      y: (rng() * 108 - 6).toFixed(1),
      s: (60 + rng() * 120).toFixed(0),
      o: (.16 + rng() * .26).toFixed(2),
      b: (7 + rng() * 12).toFixed(1),
      r: (rng() * 360).toFixed(0),
      t: (9 + rng() * 8).toFixed(1),
      d: (rng() * 16).toFixed(1),
      dx: (-16 + rng() * 32).toFixed(0),
      dy: (-10 + rng() * 26).toFixed(0),
    });
    fore.append(l);
  }

  return el;
}

/* ── 열고 닫기 ─────────────────────────────────────────── */

export const isImmersive = (card) => card?.rarity === "immersive";

export function openImmersive(card) {
  if (!dialog || dialog.open || !isImmersive(card)) return;

  dialog.replaceChildren(build(card));
  dio = dialog.querySelector(".dio");
  tx = ty = cx = cy = 0;

  dialog.showModal();
  document.body.classList.add("is-immersed");

  if (!reducedMotion) {
    dialog.classList.add("is-entering");
    // 마지막 평면이 들어오는 시각 = 620 + 6*55 = 950ms
    setTimeout(() => dialog.classList.remove("is-entering"), 1000);
  }

  bindScene(dio);
  startGyro();
  dialog.querySelector(".dio-exit")?.focus();
}

/* 뒷정리를 dialog 의 close 이벤트에 맡기지 않는 이유는 main.js 의 확대 뷰와 같다 —
   테스트한 Chrome 에서 close 가 발화하지 않았다. 닫는 길을 전부 여기로 모으고
   close/cancel 은 보조 수단으로만 둔다. finish 는 여러 번 불려도 안전하다. */
let closing = false;

function finish() {
  closing = false;
  dialog.classList.remove("is-entering", "is-leaving");
  if (dialog.open) dialog.close();
  dialog.replaceChildren();
  dio = null;
  document.body.classList.remove("is-immersed");
}

export function closeImmersive() {
  if (!dialog?.open) return;
  if (closing) return;

  stopGyro();
  cancelAnimationFrame(raf);
  raf = 0;

  if (reducedMotion) return finish();

  closing = true;
  dialog.classList.remove("is-entering");
  dialog.classList.add("is-leaving");
  // 앞에서부터 접히므로 마지막 평면이 사라지는 시각 = 260 + 6*22 = 392ms
  setTimeout(finish, 400);
}

/* ── 장면 안에서 둘러보기 ──────────────────────────────────
   마우스는 커서 위치를 그대로 쓰고, 터치는 끌어야 움직인다 — 손가락은 화면 위에
   머물러 있지 않아서 "지금 어디를 보고 있는지"를 위치로 표현할 수 없다. */

function bindScene(el) {
  let drag = null;

  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;
    drag = { x: e.clientX, y: e.clientY, tx, ty };
    el.setPointerCapture?.(e.pointerId);
  });

  el.addEventListener("pointermove", (e) => {
    const r = el.getBoundingClientRect();
    if (drag) {
      aim(drag.tx + (e.clientX - drag.x) / (r.width * .35),
          drag.ty + (e.clientY - drag.y) / (r.height * .35));
    } else if (e.pointerType === "mouse") {
      aim((e.clientX - r.left) / r.width * 2 - 1,
          (e.clientY - r.top) / r.height * 2 - 1);
    }
  });

  for (const ev of ["pointerup", "pointercancel"]) {
    el.addEventListener(ev, () => { drag = null; });
  }

  // 마우스가 창을 벗어나면 정면으로 돌아온다
  el.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") aim(0, 0);
  });
}

dialog?.addEventListener("click", (e) => {
  if (e.target.closest(".dio-exit")) closeImmersive();
});

// Esc 는 dialog 가 알아서 닫지만, 그러면 정리가 안 된다. 가로채서 우리 길로 보낸다.
dialog?.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeImmersive();
});

/* ── 꾹 누르기 ─────────────────────────────────────────────
   꾹이 발동하면 뒤따라오는 click 을 삼켜야 한다. 안 그러면 이머시브가 열리는 동시에
   main.js 의 확대 뷰까지 같이 열린다.

   같은 요소에 붙은 리스너는 capture 여부와 상관없이 등록 순서대로 불리므로,
   main.js 보다 먼저 등록되기를 기대하면 안 된다. 그래서 document 의 capture 단계에
   하나만 둔다 — 어느 요소의 리스너보다 확실히 먼저 지나간다. */

let swallow = 0;

document.addEventListener("click", (e) => {
  if (!swallow) return;
  swallow = 0;
  e.preventDefault();
  e.stopPropagation();
}, true);

/**
 * @param {HTMLElement} stage  게이지를 그릴 .stage
 * @param {HTMLElement} target 실제로 눌리는 .card
 * @param {() => void} onFire  꾹이 완성됐을 때
 */
export function bindLongPress(stage, target, onFire) {
  let timer = 0;
  let sx = 0;
  let sy = 0;

  const stop = () => {
    clearTimeout(timer);
    timer = 0;
    stage.classList.remove("is-holding");
  };

  target.addEventListener("pointerdown", (e) => {
    if (e.button > 0) return;   // 오른쪽/가운데 버튼은 무시
    sx = e.clientX;
    sy = e.clientY;
    stage.style.setProperty("--hold-ms", `${HOLD_MS}ms`);
    stage.classList.add("is-holding");
    clearTimeout(timer);
    timer = setTimeout(() => {
      stop();
      // click 은 pointerup 뒤에 오므로, 손을 떼기 전에 미리 걸어 둔다.
      // 손가락을 계속 대고 있다가 한참 뒤에 떼는 경우를 위해 넉넉히 두되,
      // 영영 안 오는 경우(pointercancel)를 대비해 시간 제한도 건다.
      swallow = 1;
      setTimeout(() => { swallow = 0; }, 1500);
      onFire();
    }, HOLD_MS);
  });

  target.addEventListener("pointermove", (e) => {
    if (!timer) return;
    if (Math.hypot(e.clientX - sx, e.clientY - sy) > SLOP) stop();
  });

  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) {
    target.addEventListener(ev, stop);
  }

  // 꾹 누르는 동안 브라우저가 컨텍스트 메뉴를 띄우면 제스처가 끊긴다
  target.addEventListener("contextmenu", (e) => {
    if (timer || swallow) e.preventDefault();
  });
}

/* ── ?im=<카드 id> ─────────────────────────────────────────
   live-server 는 파일을 고칠 때마다 페이지를 통째로 새로 고친다. 그때마다 dialog 가
   닫히므로 다시 꾹 누르고 있어야 해서, 주소에 붙여 두면 바로 들어가게 한다.
   개발 편의용이고 평소 경로에는 영향이 없다. */
export function autoOpenFromQuery() {
  const want = new URLSearchParams(location.search).get("im");
  if (!want) return;
  const card = CARDS.find((c) => c.id === want);
  if (isImmersive(card)) openImmersive(card);
}
