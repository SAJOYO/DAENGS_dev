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

const spanWith = (cls, css) => {
  const el = document.createElement("span");
  el.className = cls;
  el.style.cssText = css;
  return el;
};

/* 방울 종류. 대부분은 평범한 방울로 두고 일부만 다른 모양으로 만든다 —
   반반 섞으면 종류가 아니라 그냥 제각각인 얼룩으로 보인다. */
function dewKind(rng) {
  const v = rng();
  if (v < .17) return "is-streak";   // 흘러내리다 멈춘 자국
  if (v < .36) return "is-twin";     // 옆에 작은 게 붙은 것
  return "";
}

/* 화면 한가운데를 피해 자리를 잡는다 — 이슬이 주인공 얼굴에 앉으면 캐릭터가 안 읽힌다.
   가운데 타원 안에 걸리면 다시 뽑되, 씨가 나쁠 때 무한히 도는 일이 없도록 횟수를 막는다
   (타원이 화면의 3분의 1이라 보통 한두 번이면 빠져나온다). */
function offCenter(rng) {
  let x = 0;
  let y = 0;
  for (let i = 0; i < 8; i++) {
    x = rng() * 100;
    y = rng() * 100;
    if (Math.hypot((x - 50) / 30, (y - 48) / 38) >= 1) break;
  }
  return [x, y];
}

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

/* ── 장면 에셋 선로딩 ───────────────────────────────────────
   이머시브는 배경·누끼·카드 그림 세 장(합쳐 600KB)을 쓰는데, 예전엔 꾹이 완성된
   **그 순간에 처음** 받았다. 느린 망에서는 틀이 녹는 동안 텃밭이 아직 안 와서 빈
   화면이 보였다. 꾹 누르는 520ms 는 어차피 기다리는 시간이니 거기에 받아 둔다.

   **blob 으로 들고 있는 이유**: 개발 서버가 `Cache-Control: no-store` 를 보낸다
   (고친 게 바로 반영돼야 해서 일부러 그렇게 뒀다). 그래서 `new Image()` 로 미리
   받아 봐야 브라우저가 저장하지 않고, 정작 <img> 를 만들 때 **다시 받는다.**
   바이트를 blob 으로 쥐고 있으면 캐시 정책과 무관하게 두 번 안 받는다.
   배경은 CSS background-image 라 요소를 재사용할 수도 없으니 이 방법이어야 한다.

   decode() 까지 해 둔다. 받아만 놓으면 첫 페인트에서 디코딩을 기다리는데, 이 그림들은
   1376x768 · 875x1216 짜리라 그것도 눈에 띈다.

   실패하면 지도에서 지운다 — 그러면 asset() 이 원래 주소를 돌려주고 평소대로 돈다.
   선로딩은 어디까지나 앞당기기지, 없으면 안 되는 게 아니다. */
const scenePreload = new Map();   // 원래 주소 → { url, img } · 진행 중이면 null

/** 주 입력이 터치인가. main.js · touch.css 와 같은 기준이라 셋이 같은 기기를 가리킨다. */
const coarsePointer = matchMedia("(hover: none) and (pointer: coarse)");

/**
 * 터치에서는 입자를 절반으로 줄인다.
 *
 * 이 장면은 먼지 52 · 이슬 15 · 배추 이슬 11 · 잎 7 로 **88개**를 만든다. 하나하나는
 * 작지만 전부 무한 애니메이션을 달고 있어서(먼지는 흐르기와 밝아지기 두 개) 크롬이
 * 대부분 개별 합성 레이어로 승격시킨다. 게다가 먼지는 box-shadow 가 두 겹이고 반경이
 * `--s * 9px` 라, 4px 짜리 점 하나가 훨씬 큰 텍스처를 차지한다.
 *
 * 절반으로 줄여도 밀도가 눈에 띄게 빠지지는 않는다 — 원래 무작위로 흩뿌린 것이라
 * 개수보다 "여기저기 있다"가 읽히면 된다. 최소값을 두는 건 너무 줄어서 **한둘만
 * 떠 있는 게 보이는** 상태를 막기 위해서다. 그건 없느니만 못하다.
 *
 * 되돌리려면 0.5 를 1 로 바꾸면 된다.
 */
const count = (n, min) =>
  coarsePointer.matches ? Math.max(min, Math.round(n * 0.5)) : n;

const sceneUrls = (card) => {
  const s = card?.scene;
  return s ? [s.back, s.subject, s.card, s.frame].filter(Boolean) : [];
};

/** 여러 번 불러도 안전하다. 이미 받았거나 받는 중이면 그냥 넘어간다. */
export function preloadScene(card) {
  for (const u of sceneUrls(card)) {
    if (scenePreload.has(u)) continue;
    scenePreload.set(u, null);
    (async () => {
      const res = await fetch(u);
      if (!res.ok) throw new Error(`${res.status} ${u}`);
      const url = URL.createObjectURL(await res.blob());
      const img = new Image();
      img.src = url;
      await img.decode().catch(() => {});   // 디코딩 실패해도 바이트는 쓸 수 있다
      scenePreload.set(u, { url, img });    // img 를 같이 쥐어 디코딩 결과를 붙잡아 둔다
    })().catch(() => scenePreload.delete(u));
  }
}

/** 선로딩이 끝났으면 blob 주소, 아니면 원래 주소. 둘 다 그냥 쓰면 된다. */
const asset = (u) => scenePreload.get(u)?.url ?? u;

function build(card) {
  const rng = rngFrom(seedOf(card.id));
  const scene = card.scene || {};

  const el = document.createElement("div");
  el.className = "dio";
  el.style.setProperty("--accent", card.accent);
  el.style.setProperty("--accent2", card.accent2);
  // 레이어 원화가 없으면 카드 그림으로 때운다. 보기엔 이상하지만 터지지는 않는다.
  el.style.setProperty("--back", `url("${asset(scene.back ?? card.art)}")`);

  // 들어올 때 겹칠 카드의 자리. scene.fit 이 "카드 안에서 누끼가 차지하는 자리"이므로
  // 뒤집으면 "누끼에 맞추려면 카드가 어디 있어야 하는지"가 된다.
  // 단위는 전부 누끼 높이의 배수다 — CSS 가 --hh 를 곱한다.
  const fit = scene.fit;
  if (fit) {
    const ch = 100 / fit.h;                    // 카드 높이
    const cw = ch * (card.w / card.h);         // 카드 폭
    const hw = (fit.w / 100) * cw;             // 누끼 폭
    el.style.setProperty("--card-h", ch.toFixed(4));
    el.style.setProperty("--card-x", (-hw / 2 - (fit.x / 100) * cw).toFixed(4));
    el.style.setProperty("--card-y", (-0.5 - (fit.y / 100) * ch).toFixed(4));
  }

  // --i 는 진입 시차 순번이다. 뒤에서 앞으로 0..6.
  el.innerHTML = `
    <div class="dio-void" aria-hidden="true"></div>
    <div class="dio-world">
      <div class="dio-layer dio-sky" style="--i:0"><div class="dio-move"></div></div>
      <div class="dio-layer dio-back" style="--i:1"><div class="dio-move"></div></div>
      <div class="dio-layer dio-rays" style="--i:2"><div class="dio-move"></div></div>
      <div class="dio-layer dio-motes" style="--i:3"><div class="dio-move"></div></div>
    </div>
    <div class="dio-layer dio-subject" style="--i:4"><div class="dio-move">
      <div class="dio-plate">
        <span class="dio-ground" aria-hidden="true"></span>
        ${scene.frame ? `<img class="dio-plate-frame" src="${esc(asset(scene.frame))}" alt="" aria-hidden="true" decoding="async">` : ""}
        ${fit ? `<img class="dio-plate-art" src="${esc(asset(scene.card ?? card.art))}" alt="" aria-hidden="true" decoding="async">` : ""}
        <img class="dio-hero" src="${esc(asset(scene.subject ?? card.art))}"
             alt="${esc(altText(card))}" decoding="async">
        <span class="dio-skin" aria-hidden="true"></span>
      </div>
    </div></div>
    <div class="dio-world">
      <div class="dio-layer dio-hud" style="--i:5"><div class="dio-move">
        <div class="dio-meta">
          <span class="dio-rank" aria-hidden="true">★★★</span>
          <strong>${esc(card.name)}</strong>
          <span class="dio-code">${esc(card.code)} · ${esc(card.edition)}</span>
          ${scene.place ? `<span class="dio-place">${esc(scene.place)}</span>` : ""}
        </div>
      </div></div>
      <div class="dio-layer dio-fore" style="--i:6"><div class="dio-move"></div></div>
      <div class="dio-layer dio-dew" style="--i:7"><div class="dio-move"></div></div>
    </div>
    <button type="button" class="dio-exit">닫기 (Esc)</button>
    <p class="dio-tip">기울이거나 끌어서 둘러보세요</p>`;

  // 먼지 — 카드 뒤에서 느리게 떠다닌다
  const motes = el.querySelector(".dio-motes .dio-move");
  for (let i = 0; i < count(scene.motes ?? 30, 12); i++) {
    motes.append(spanWith("mote", vars({
      x: (rng() * 100).toFixed(1),
      y: (rng() * 100).toFixed(1),
      s: (1.3 + rng() * 4.4).toFixed(1),
      o: (.24 + rng() * .62).toFixed(2),
      t: (7 + rng() * 9).toFixed(1),
      d: (rng() * 14).toFixed(1),
      dx: (-22 + rng() * 44).toFixed(0),
      dy: (-28 + rng() * 14).toFixed(0),
      // 밝아지는 주기. 흐르는 주기(--t)와 일부러 다른 값을 줘서 둘이 안 겹치게 한다
      gt: (3.4 + rng() * 5.2).toFixed(1),
      gd: (rng() * 8).toFixed(1),
    })));
  }

  // 앞 잎사귀 — 크고 흐리게. 초점이 카드에 맞은 것처럼 보이게 하는 층이다.
  //
  // 자리는 이슬과 같은 규칙으로 가운데를 피한다. 예전엔 화면 전체에 균등하게
  // 뿌려서 얼굴 위에 그대로 앉았다 — 이 층은 주인공보다 **앞**에 그려지므로
  // 가리면 캐릭터가 안 읽힌다. 렌즈 이슬(offCenter)과 표면 이슬(반지름 28~42%
  // 고리)은 진작 그렇게 하고 있었는데 잎만 빠져 있었다.
  //
  // 크기(--s)와 번짐(--b)의 단위는 px 이 아니라 **누끼 높이의 %** 다. immersive.css
  // 의 .leaf 가 --hh 를 곱한다. px 로 두면 폰에서 화면만 작아지고 잎은 그대로라
  // 캐릭터를 덮는다.
  const fore = el.querySelector(".dio-fore .dio-move");
  for (let i = 0; i < count(scene.leaves ?? 7, 3); i++) {
    const l = document.createElement("span");
    l.className = "leaf";
    const [lx, ly] = offCenter(rng);
    l.style.cssText = vars({
      x: lx.toFixed(1),
      y: ly.toFixed(1),
      s: (11 + rng() * 22).toFixed(1),
      o: (.16 + rng() * .26).toFixed(2),
      b: (1.25 + rng() * 2.2).toFixed(2),
      r: (rng() * 360).toFixed(0),
      t: (9 + rng() * 8).toFixed(1),
      d: (rng() * 16).toFixed(1),
      dx: (-16 + rng() * 32).toFixed(0),
      dy: (-10 + rng() * 26).toFixed(0),
    });
    fore.append(l);
  }

  // 겉잎 겹. 바깥일수록 높이 띄운다 — 배추는 잎이 여러 겹이라 계단이 하나면
  // "두 장"으로 보이고, 두세 단이면 두께로 읽힌다.
  // --k 는 원근이 키우는 만큼을 되돌리는 배율이다. 1500 은 immersive.css 의
  // .dio-subject .dio-move 에 걸린 perspective 값이라 **둘이 같이 움직여야 한다.**
  const shells = scene.shells ?? [];
  const plate = el.querySelector(".dio-plate");
  const skinEl = el.querySelector(".dio-skin");
  const heroSrc = esc(asset(scene.subject ?? card.art));

  for (const sh of shells) {
    const layer = document.createElement("div");
    layer.className = "dio-rind";
    layer.setAttribute("aria-hidden", "true");
    layer.style.cssText = vars({
      z: sh.z,
      k: ((1500 - sh.z) / 1500).toFixed(4),
      r0: sh.r0,
      r1: sh.r1,
      ...(sh.r2 == null ? {} : { r2: sh.r2, r3: sh.r3 }),
      ...(sh.shadow == null ? {} : { sa: sh.shadow }),
    });
    layer.innerHTML = `<img src="${heroSrc}" alt="" decoding="async">`;
    plate.insertBefore(layer, skinEl);
  }

  // 캐릭터에 맺힌 이슬 — 렌즈가 아니라 **배추 표면**에 붙어 있다. 그래서 이 방울들만
  // 주인공 평면 안에 있고, 배추가 기울면 같이 기운다.
  // 자리는 잎이 있는 바깥 고리로 한정한다. 가운데는 강아지 얼굴이라 방울이 앉으면
  // 표정이 안 읽힌다 — 얼굴 반지름이 22% 쯤이라 28% 부터 시작한다.
  const skin = el.querySelector(".dio-skin");
  const frontZ = shells.reduce((m, sh) => Math.max(m, sh.z), 0);
  for (let i = 0; i < count(scene.skinDew ?? 11, 4); i++) {
    const a = rng() * Math.PI * 2;
    const r = 28 + rng() * 14;
    const kind = dewKind(rng);
    const drop = spanWith("dew", vars({
      x: (50 + Math.cos(a) * r).toFixed(1),
      y: (46 + Math.sin(a) * r).toFixed(1),
      // 여기서 --s 는 px 이 아니라 **누끼 높이의 %** 다 (immersive.css 의 .dio-skin .dew)
      s: (1.05 + rng() * 1.7).toFixed(2),
      e: kind === "is-streak" ? (1.9 + rng() * .8).toFixed(2) : (1.02 + rng() * .2).toFixed(2),
      o: (.55 + rng() * .37).toFixed(2),
      // 표면에서 띄우는 높이. **제일 앞 겹보다 높아야** 겉잎 뒤로 안 숨는다.
      // 크게 줄수록 기울일 때 원화와 많이 어긋나지만, 원근이 방울을 바깥으로도
      // 밀어내서 너무 키우면 배추 실루엣 밖으로 새어 나간다.
      z: (frontZ + 6 + rng() * 24).toFixed(0),
    }));
    if (kind) drop.classList.add(kind);
    skin.append(drop);
  }

  // 누끼의 가로세로비. .dio-skin 이 누끼와 똑같은 상자여야 방울을 % 로 찍을 수 있다.
  const heroImg = el.querySelector(".dio-hero");
  const setAspect = () => {
    if (heroImg.naturalHeight) {
      el.style.setProperty("--subject-ar", (heroImg.naturalWidth / heroImg.naturalHeight).toFixed(4));
    }
  };
  if (heroImg.complete) setAspect();
  else heroImg.addEventListener("load", setAspect, { once: true });

  // 이슬 — 카메라 유리에 맺힌 것처럼 **패럴랙스 없이** 제자리에 있다(--par 0).
  // 뒤 세상이 통째로 밀리는데 이것만 안 움직여서, 화면과 나 사이에 유리가 한 장
  // 있다는 게 읽힌다. 밀리게 하면 그냥 떠다니는 동그라미가 된다.
  const dew = el.querySelector(".dio-dew .dio-move");
  const runners = count(scene.dewRun ?? 3, 1);   // 흘러내리는 것은 dew 의 부분집합
  for (let i = 0; i < count(scene.dew ?? 15, 5); i++) {
    const [x, y] = offCenter(rng);
    const size = 8 + rng() * 18;
    const kind = dewKind(rng);
    const drop = spanWith("dew", vars({
      x: x.toFixed(1),
      y: y.toFixed(1),
      s: size.toFixed(1),
      // 자국은 세로로 길게 늘어진다. 나머지는 중력에 살짝 처지는 정도
      e: kind === "is-streak" ? (1.9 + rng() * .8).toFixed(2) : (1.02 + rng() * .2).toFixed(2),
      o: (.5 + rng() * .4).toFixed(2),
    }));
    if (kind) drop.classList.add(kind);
    // 큰 방울일수록 렌즈에 가깝다 = 초점에서 더 벗어난다. 몇 개가 흐려야
    // 나머지 또렷한 방울이 "유리에 붙어 있다"로 읽힌다.
    const soft = size > 23 ? 1.3 : size > 20 ? .5 : 0;   // 15개 중 서너 개만
    if (soft) {
      drop.classList.add("is-soft");
      drop.style.setProperty("--b", soft);
    }
    // 몇 개만 흘러내린다. 전부 움직이면 비 오는 창문이 되고, 여긴 이슬이다.
    if (i < runners) {
      drop.classList.add("is-run");
      drop.style.setProperty("--fall", (18 + rng() * 26).toFixed(0));
      drop.style.setProperty("--t", (9 + rng() * 7).toFixed(1));
      drop.style.setProperty("--d", (rng() * 9).toFixed(1));
    }
    dew.append(drop);
  }

  return el;
}

/* ── 열고 닫기 ─────────────────────────────────────────── */

export const isImmersive = (card) => card?.rarity === "immersive";

/**
 * 눌린 카드에서 큰 카드로 가는 변형을 재서 --flip-t 에 넣는다.
 *
 * 판(.dio-plate)을 판 중심 P 기준으로 s 배 줄이면 카드 중심 A 는 P + s*(A-P) 로 간다.
 * 그게 눌린 카드의 중심 F 가 되도록 남은 만큼을 밀어 준다. 판과 카드의 중심이 서로
 * 다르기 때문에(카드가 판 안에서 위쪽으로 치우쳐 있다) 이 보정이 필요하다.
 */
function setFlight(root, fromRect) {
  const plate = root.querySelector(".dio-plate");
  const art = root.querySelector(".dio-plate-art");
  if (!plate || !art || !fromRect?.width) return;

  const p = plate.getBoundingClientRect();
  const a = art.getBoundingClientRect();
  if (!a.width) return;

  const s = fromRect.width / a.width;
  const tx0 = (fromRect.left + fromRect.width / 2) - (p.left + p.width / 2)
            - s * ((a.left + a.width / 2) - (p.left + p.width / 2));
  const ty0 = (fromRect.top + fromRect.height / 2) - (p.top + p.height / 2)
            - s * ((a.top + a.height / 2) - (p.top + p.height / 2));

  plate.style.setProperty(
    "--flip-t",
    `translate(${tx0.toFixed(1)}px, ${ty0.toFixed(1)}px) scale(${s.toFixed(4)})`);

  // 중간에 한 번 "카드 크기"로 서는 지점. 고정값을 쓰면 안 된다 — 모바일은 그리드
  // 카드가 이미 화면만 해서 s 가 0.7 을 넘는데, 거기서 0.62 로 가면 날아오다가
  // 오히려 **작아졌다가** 커진다. 출발 크기보다 항상 크도록 잡는다.
  // 착지 배율(--set-s)보다 크면 날아오다가 오히려 **작아졌다가** 서게 된다.
  const set = parseFloat(getComputedStyle(plate).getPropertyValue("--set-s")) || 1;
  plate.style.setProperty(
    "--mid-s", Math.min(set * .95, Math.max(s * 1.25, .62)).toFixed(3));
}

/**
 * 그림 창이 화면 어디인지 재서 `--win-*` 에 넣는다.
 *
 * **카드가 제자리에 섰을 때(판 배율 1) 기준이다.** is-entering 을 붙이기 전에 재므로
 * 그 상태가 그대로 잡힌다. 그래서 창이 열리는 구간(62% 이후)에는 판이 이미 배율 1 에
 * 멈춰 있어야 한다 — plate-in 이 44% 에 1 로 서서 그대로 있는 이유가 이것이다.
 *
 * clip-path 가 이 값으로 창을 그리고 **틀 이미지는 같은 --winK 로 커진다.** 둘이 같은
 * 변수를 읽으므로 어긋날 수가 없다. 창은 "중심에서 반너비 x 배율" 로, 틀은 창 중심을
 * 기준으로 한 scale 로 커지는데, 기준점이 같아서 결과가 일치한다.
 *
 * 카드가 화면에 들어오는 배율(--set-s)도 여기서 정한다. **판의 배율 1 은 카드가
 * 화면보다 큰 크기다** — 카드 높이가 누끼 높이(--hh)의 1.59배라 폰에서 459x638 이
 * 되어 430 폭을 넘는다. 예전엔 카드가 배율 1 에 닿기 전에 녹아 없어져서 그 사실이
 * 드러나지 않았는데, 이제 판이 44% 에 서서 그대로 보이므로 맞춰 줘야 한다.
 *
 * @param {object} card cards.mjs 의 카드. scene.window 와 w/h 비율을 쓴다.
 */
function setWindow(root, card) {
  const win = card.scene?.window;
  const art = root.querySelector(".dio-plate-art");
  if (!art || !win) return;

  // **이미지가 안 와도 잴 수 있어야 한다.** offsetHeight 는 calc(--card-h * --hh) 라
  // 이미지와 무관하지만 offsetWidth 는 width:auto 라 원본 비율을 알아야 나온다.
  // 그 비율은 cards.mjs 에 이미 있으므로 계산으로 대신한다.
  //
  // getBoundingClientRect 를 안 쓰는 이유: 그건 transform 이 반영된 값이라
  // plate-in 이 도는 중에 재면 엉뚱한 크기가 잡힌다. offset* 은 레이아웃 값이다.
  const h0 = art.offsetHeight;
  if (!h0) return;
  const w0 = h0 * (card.w / card.h);

  const vw = root.clientWidth;
  const vh = root.clientHeight;
  if (!vw || !vh) return;

  const set = Math.min(1, (vw * 0.86) / w0, (vh * 0.80) / h0);
  root.style.setProperty("--set-s", set.toFixed(4));
  // 판이 제 크기로 돌아왔을 때의 --pg. 판의 배율이 --set-s * --pg 라 이 값이 끝값이고,
  // 곱이 정확히 1 이 되어야 is-entering 이 걷힐 때 안 튄다. 자릿수를 넉넉히 준다 —
  // 4 자리로 자르면 곱이 0.9999 가 되어 폰에서 반 픽셀쯤 어긋난다.
  root.style.setProperty("--set-inv", (1 / set).toFixed(6));

  // 창의 자리. 판은 **자기 중심(=화면 중심) 기준**으로 set 배가 되므로 그만큼 옮긴다.
  const cx0 = vw / 2;
  const cy0 = vh / 2;
  const cx = art.offsetLeft + w0 * (win.x + win.w / 2) / 100;
  const cy = art.offsetTop + h0 * (win.y + win.h / 2) / 100;

  const wx = cx0 + (cx - cx0) * set;
  const wy = cy0 + (cy - cy0) * set;
  const hw = w0 * win.w / 200 * set;
  const hh = h0 * win.h / 200 * set;

  root.style.setProperty("--win-cx", `${wx.toFixed(1)}px`);
  root.style.setProperty("--win-cy", `${wy.toFixed(1)}px`);
  root.style.setProperty("--win-hw", `${hw.toFixed(1)}px`);
  root.style.setProperty("--win-hh", `${hh.toFixed(1)}px`);

  // 창이 화면 네 변을 다 넘어서는 배율. **이걸 고정값으로 박으면 안 된다.**
  // 창은 카드 그림 영역이라 거의 정사각인데 화면은 가로로 길어서, 같은 배율로
  // 벌리면 위아래가 먼저 끝나고 좌우가 한참 남는다. 예전엔 4.5 를 박아 뒀는데
  // 그 값이 딱 맞는 건 재 본 화면 하나뿐이고, 조금만 넓거나 낮아도 연출이 끝날
  // 때까지 **좌우에 검은 띠가 남았다.** 창은 자기 중심에서 벌어지므로 중심에서
  // 먼 쪽 변까지의 거리를 반너비로 나눈 값이 곧 필요한 배율이다.
  const cover = Math.max(
    Math.max(wx, vw - wx) / hw,
    Math.max(wy, vh - wy) / hh);
  root.style.setProperty("--win-k", cover.toFixed(3));
  // 다 덮고 나서도 조금 더 벌어져야 "통과했다"가 된다. 여기서부터는 화면 밖이라
  // 판이 제 크기로 돌아오는 구간(plate-in 84~100%)과 겹쳐도 아무것도 안 보인다.
  root.style.setProperty("--win-k-end", (cover * 1.22).toFixed(3));

  // 틀 이미지 자기 좌표에서의 창 중심 — 여기를 기준으로 커져야 창과 같이 벌어진다.
  // 판이 set 배로 줄어도 기준점은 비율이라 그대로다.
  root.style.setProperty("--win-ox", `${(win.x + win.w / 2).toFixed(2)}%`);
  root.style.setProperty("--win-oy", `${(win.y + win.h / 2).toFixed(2)}%`);

  // 값이 다 준비됐을 때만 창 연출을 켠다. 틀이 없는 카드는 예전 연출로 돈다.
  root.classList.add("has-window");
}

/**
 * @param {object} card
 * @param {DOMRect} [fromRect] 눌린 카드의 화면 위 자리. 여기서 출발한다.
 *   키보드(★★★ 버튼)로 들어오면 없어도 되고, 그때는 그냥 작게 시작한다.
 */
export function openImmersive(card, fromRect) {
  if (!dialog || dialog.open || !isImmersive(card)) return;

  dialog.replaceChildren(build(card));
  dio = dialog.querySelector(".dio");
  tx = ty = cx = cy = 0;

  dialog.showModal();
  document.body.classList.add("is-immersed");

  if (!reducedMotion) {
    // 재는 건 is-entering 을 붙이기 **전에**. 붙고 나면 이미 변형된 상태라 못 잰다.
    setWindow(dio, card);      // --set-s 를 먼저 정한다 (setFlight 가 읽는다)
    setFlight(dio, fromRect);

    // 들어오는 2.1초 안에 화면 크기가 바뀌면(폰에서 주소창이 접히는 게 대표적)
    // 창은 **옛 화면 기준**으로 남아 틀과 어긋난다. 그 어긋남은 창 가장자리에
    // 검은 띠로 드러나고, 창이 벌어질수록 같이 두꺼워진다.
    // CSS 가 --win-* 와 --set-s 를 var() 로 읽으므로 다시 재서 넣기만 하면
    // 도는 중에도 따라온다 (offset* 은 레이아웃 값이라 변형과 무관하게 잴 수 있다).
    const el = dio;
    reflow = () => setWindow(el, card);
    addEventListener("resize", reflow);

    dialog.classList.add("is-entering");
    // 창이 다 열리는 시각이 2100ms 로 제일 늦다 (배경 평면은 880 + 7*45 + 620 = 1815ms)
    // 타이머를 들고 있어야 한다 — 닫았다 바로 다시 열면 **먼저 걸린 타이머가 새 판의
    // is-entering 을 도중에 걷어내서** 연출이 중간에 툭 끊긴다.
    clearTimeout(enterTimer);
    enterTimer = setTimeout(() => dialog.classList.remove("is-entering"), 2160);
  }

  bindScene(dio);
  startGyro();
  dialog.querySelector(".dio-exit")?.focus();
}

/* 뒷정리를 dialog 의 close 이벤트에 맡기지 않는 이유는 main.js 의 확대 뷰와 같다 —
   테스트한 Chrome 에서 close 가 발화하지 않았다. 닫는 길을 전부 여기로 모으고
   close/cancel 은 보조 수단으로만 둔다. finish 는 여러 번 불려도 안전하다. */
let closing = false;
let enterTimer = 0;
let reflow = null;      // 들어오는 동안만 붙어 있는 resize 핸들러

function finish() {
  closing = false;
  clearTimeout(enterTimer);
  enterTimer = 0;
  if (reflow) {
    removeEventListener("resize", reflow);
    reflow = null;
  }
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
