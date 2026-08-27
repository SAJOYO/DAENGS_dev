/* ── 기울기 엔진 ───────────────────────────────────────────
   포인터 · 키보드 · 자이로 · 네이티브 브릿지를 받아 카드의 CSS 변수로 내보낸다.
   **홀로 스튜디오(studio.mjs)와 거기서 내보낸 HTML 이 같이 쓴다.**

   ## 왜 모듈(.mjs)이 아니라 고전 스크립트(.js)인가

   내보낸 파일은 파일 하나로 끝나야 하고 `file://` 로도 열려야 한다. `import` 는
   거기서 못 쓰므로, 내보내기가 **이 파일의 텍스트를 그대로 fetch 해서 `<script>`
   안에 넣는다** — CSS 를 인라인하는 것과 같은 방식이다. 사본을 studio.mjs 안에
   문자열로 박아 두면 여기를 고쳤을 때 내보낸 파일만 옛 동작으로 남는다.

   그래서 이 파일은 어떤 것도 import 하지 않고, 아무것도 export 하지 않는다.
   `window.NeoTilt` 하나만 만든다.

   ## main.js(도감)는 여기를 안 쓴다

   도감에는 같은 계산을 하는 자기 코드가 있다. 저쪽은 도감의 사정(카드 열두 장,
   확대 뷰의 ‹ › 로 노드가 갈리는 것, 화살표는 카드 이동이라 Shift 를 요구하는 것)이
   섞여 있어서, 하나로 합치려면 잘 돌던 화면을 고쳐야 한다. **변수 이름과 계산식은
   양쪽이 같아야 한다** — rarity.css 의 '다리' 블록이 그 이름을 읽어 vendor 포일에
   넘기므로, 한쪽만 고치면 그쪽 포일이 통째로 죽는다. */

(function () {
  "use strict";

  var MOTION_VARS = [
    "--mouse-x", "--mouse-y", "--rotate-x", "--rotate-y", "--glare", "--shadow-x", "--shadow-y",
    // cards-css 포일이 읽는 값. 우리가 안 쓰더라도 같이 지워야 손을 뗐을 때 정면으로 돌아간다.
    "--pointer-from-left", "--pointer-from-top", "--pointer-from-center",
  ];

  /** 이 각도(도)만큼 기울이면 카드가 끝까지 돈다. 키우면 둔해지고 줄이면 예민해진다. */
  var TILT_RANGE = 20;

  var reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** 지금 기울일 카드. 마지막으로 bind 한 것 하나다 (두 페이지 다 카드가 한 장이다). */
  var current = null;
  /** 포인터가 카드 위에 있나. 있으면 자이로가 비켜 준다 — 한 카드를 두고 매 프레임 싸우면 안 된다. */
  var pointerOn = false;

  function clamp01(n) { return Math.max(0, Math.min(1, n)); }

  /**
   * 입력 소스가 뭐든 결국 여기로 들어온다.
   * @param {HTMLElement} el .stage
   * @param {number} px 카드 안 가로 위치 0~1 (0=왼쪽 끝)
   * @param {number} py 세로 위치 0~1
   * @param {number} intensity 포일 세기 0~1
   */
  function write(el, px, py, intensity) {
    el.style.setProperty("--mouse-x", (px * 100).toFixed(2) + "%");
    el.style.setProperty("--mouse-y", (py * 100).toFixed(2) + "%");
    el.style.setProperty("--rotate-x", ((0.5 - py) * 22).toFixed(2) + "deg");
    el.style.setProperty("--rotate-y", ((px - 0.5) * 25).toFixed(2) + "deg");
    el.style.setProperty("--glare", intensity);
    el.style.setProperty("--shadow-x", ((0.5 - px) * 38).toFixed(1) + "px");
    el.style.setProperty("--shadow-y", (18 + (0.5 - py) * 28).toFixed(1) + "px");

    // 0~1 의 맨숫자(단위 없음)라 위의 % · deg 와 섞이지 않는다. from-center 는 모서리가
    // 1 이 아니라 반지름 0.5 를 1 로 보는 값이다 — reverse 포일이 이걸로 가운데를 죽인다.
    el.style.setProperty("--pointer-from-left", px.toFixed(3));
    el.style.setProperty("--pointer-from-top", py.toFixed(3));
    el.style.setProperty("--pointer-from-center",
      Math.min(Math.hypot(px - 0.5, py - 0.5) / 0.5, 1).toFixed(3));
  }

  /** 인라인으로 덮어썼던 값만 지우면 style.css 의 기본값(정면)으로 돌아간다. */
  function clear(el) {
    for (var i = 0; i < MOTION_VARS.length; i++) el.style.removeProperty(MOTION_VARS[i]);
  }

  /* ── 입력 (1) 포인터 · 키보드 ───────────────────────────── */

  /**
   * 카드 한 장에 포인터/키보드 기울기를 붙이고, 자이로의 대상으로 삼는다.
   * 카드를 새로 만들 때마다 부르면 된다 — 마지막에 부른 것이 대상이 된다.
   */
  function bind(el) {
    var pending = null;
    var frame = 0;

    function flush() {
      frame = 0;
      var job = pending;
      pending = null;
      if (!job) return;
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      write(el, clamp01((job.x - r.left) / r.width), clamp01((job.y - r.top) / r.height), job.i);
    }

    function paint(x, y, i) {
      if (reduced) return;
      pending = { x: x, y: y, i: i };
      if (!frame) frame = requestAnimationFrame(flush);
    }

    function reset() {
      pending = null;
      pointerOn = false;
      clear(el);
    }

    el.addEventListener("pointerenter", function (e) { pointerOn = true; paint(e.clientX, e.clientY, 0.8); });
    el.addEventListener("pointermove", function (e) { pointerOn = true; paint(e.clientX, e.clientY, 1); });
    el.addEventListener("pointerleave", reset);
    el.addEventListener("pointercancel", reset);

    var card = el.querySelector(".card");
    card.addEventListener("blur", reset);

    // 터치에는 hover 가 없어 손가락이 닿아 있는 동안에만 기운다. 카드가 한 장이라
    // 스크롤에 제스처를 뺏길 일도 없으므로 도감 확대 뷰와 같은 조건으로 둔다.
    if (matchMedia("(hover: none) and (pointer: coarse)").matches) el.style.touchAction = "none";

    // 마우스가 없어도 포일을 볼 수 있어야 한다 — 카드에 포커스를 준 뒤 화살표.
    // 도감과 달리 여기서는 화살표에 다른 임자가 없어서 Shift 를 요구하지 않는다.
    var kx = 0;
    var ky = 0;
    card.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { kx = ky = 0; reset(); return; }
      var step = 9;
      if (e.key === "ArrowLeft") kx -= step;
      else if (e.key === "ArrowRight") kx += step;
      else if (e.key === "ArrowUp") ky -= step;
      else if (e.key === "ArrowDown") ky += step;
      else return;
      e.preventDefault();
      kx = Math.max(-45, Math.min(45, kx));
      ky = Math.max(-45, Math.min(45, ky));
      var r = el.getBoundingClientRect();
      paint(r.left + r.width * (0.5 + kx / 100), r.top + r.height * (0.5 + ky / 100), 1);
    });

    current = el;
    return { reset: reset };
  }

  /* ── 입력 (2) 자이로 · 네이티브 ─────────────────────────────
     폰을 기울이면 카드가 따라 기운다. **PC 는 아무것도 안 바뀐다** — 데스크톱에는
     센서가 없어서 이벤트가 한 번도 안 온다.

     값이 들어오는 문은 두 개다.
       - 브라우저: `deviceorientation` 이벤트
       - 네이티브 앱: WebView 에서 `window.__neoTilt(beta, gamma)` 를 부른다
     둘 다 feed() 하나로 모이고, 거기서 write() 로 나간다.

     **브라우저 쪽은 secure context 에서만 온다.** https 이거나 localhost 여야 한다.
     크롬·파이어폭스는 `file://` 도 secure context 로 치므로 내보낸 파일을 PC 에서
     열면 켜지지만, 안드로이드에서 다운로드한 HTML 은 `content://` 로 열려서 안
     켜진다. 폰에서 확실히 보려면 https 로 서빙하는 게 맞다
     (`node projects/neo-hologram/https-server.mjs`).
     **아무 경고도 안 뜬다** — 리스너는 멀쩡히 붙고 이벤트만 영영 안 온다. */

  var base = null;          // 처음 들어온 값을 '정면'으로 삼는다
  var gyroPending = null;
  var gyroFrame = 0;
  var onLive = null;        // 진짜로 값이 오기 시작하면 한 번 부르는 콜백 (UI 안내용)
  var live = false;

  function gyroFlush() {
    gyroFrame = 0;
    var job = gyroPending;
    gyroPending = null;
    if (!job || !current || !current.isConnected) return;
    // 손가락이 올라가 있으면 포인터가 이긴다. 폰에서도 문지르면 그쪽이 우선이다.
    if (pointerOn) return;
    write(current, job.px, job.py, 1);
  }

  /**
   * @param {number} beta  앞뒤 기울기 (deviceorientation 규약, 도 단위)
   * @param {number} gamma 좌우 기울기
   */
  function feed(beta, gamma) {
    if (reduced) return;
    if (typeof beta !== "number" || typeof gamma !== "number") return;
    if (isNaN(beta) || isNaN(gamma)) return;

    // 절대 각도가 아니라 '처음 든 자세에서 얼마나 움직였는지'를 쓴다. 폰을 눕혀서 보든
    // 세워서 보든 처음 자세가 정면이 되므로, 들자마자 카드가 홱 돌아가지 않는다.
    if (!base) base = { beta: beta, gamma: gamma };
    var dx = gamma - base.gamma;   // 좌우
    var dy = beta - base.beta;     // 앞뒤

    // 가로로 눕히면 센서 축과 화면 축이 어긋난다. 화면이 돈 만큼 되돌려 준다.
    var angle = (screen.orientation && screen.orientation.angle) || 0;
    var t;
    if (angle === 90) { t = dx; dx = dy; dy = -t; }
    else if (angle === 180) { dx = -dx; dy = -dy; }
    else if (angle === 270) { t = dx; dx = -dy; dy = t; }

    if (!live) { live = true; if (onLive) onLive(); }

    gyroPending = {
      px: clamp01(0.5 + dx / TILT_RANGE / 2),
      py: clamp01(0.5 + dy / TILT_RANGE / 2),
    };
    if (!gyroFrame) gyroFrame = requestAnimationFrame(gyroFlush);
  }

  /** 지금 자세를 '정면'으로 다시 잡는다. 자세를 바꿔 앉았을 때 쓴다. */
  function recenter() { base = null; }

  /**
   * 자이로를 켠다. 여러 번 불러도 한 번만 붙는다.
   *
   * iOS 13+ 는 **사용자 제스처 안에서** 권한을 물어야 해서 첫 탭까지 기다렸다가
   * 붙인다. 안드로이드는 물을 게 없어서 바로 붙는다. 거절해도 아무 일도 안
   * 일어난다 — 포인터 기울기가 그대로 남는다.
   *
   * @param {function} opts.onLive 첫 값이 실제로 들어왔을 때 한 번 불린다
   */
  var started = false;
  function gyro(opts) {
    if (opts && opts.onLive) onLive = opts.onLive;
    if (started || reduced || typeof DeviceOrientationEvent === "undefined") return;
    started = true;

    function listen() {
      addEventListener("deviceorientation", function (e) { feed(e.beta, e.gamma); });
    }

    if (typeof DeviceOrientationEvent.requestPermission === "function") {
      document.addEventListener("pointerdown", function ask() {
        document.removeEventListener("pointerdown", ask);
        DeviceOrientationEvent.requestPermission()
          .then(function (r) { if (r === "granted") listen(); })
          .catch(function () {});
      }, { once: true });
    } else {
      listen();
    }
  }

  /** 네이티브 앱용 문. Android 쪽에서 SensorManager 값을 그대로 넘기면 된다:
   *  `webView.evaluateJavascript("window.__neoTilt(" + beta + "," + gamma + ")", null)` */
  window.__neoTilt = feed;

  window.NeoTilt = {
    bind: bind,
    gyro: gyro,
    feed: feed,
    recenter: recenter,
    write: write,
    clear: clear,
    /** 자이로가 아예 불가능한 환경인지 (UI 안내에 쓴다). 켜지는지는 값이 와 봐야 안다. */
    canGyro: !reduced && typeof DeviceOrientationEvent !== "undefined" && isSecureContext,
  };
})();
