// 카드 6장의 데이터.
//
// name/code/move/stat/flavor 는 지어낸 게 아니라 그림 안에 이미 인쇄돼 있는 문구를
// 그대로 옮긴 것이다. 이미지는 홀로그램 효과를 입힐 밑그림이 아니라 그 자체로 완성된
// 트레이딩 카드라서(프레임·제목바·스탯바 포함), 여기 텍스트는 그림의 사본 역할을 한다.
// 확대 뷰 캡션과 alt 텍스트가 이걸 쓰기 때문에 그림을 못 봐도 내용이 전달된다.
//
// no  : 도감 번호. 원본 PNG 를 만든 순서(파일 수정시각) 그대로다.
// w/h : art/*.webp 의 실제 픽셀. 크롭을 안 했으므로 비율이 세 종류로 갈린다
//       (0.80 다섯 장, 0.72 두 장, 0.725 다섯 장). CSS 가 이 값으로 카드마다
//       aspect-ratio 를 잡는다. 0.72 와 0.725 는 눈으로는 같아 보이지만, 원본을
//       늘이지 않는 게 원칙이라 실제 픽셀을 그대로 적는다.
// accent/accent2 : 카드 뒤 글로우와 테두리에 쓸 색. 그림에서 뽑았다.
// frame : 프레임 디자인. 크롬(chrome) 넉 장, 초록 홀로(leaf) 두 장.
// rarity : 레어도. 등급표가 아니라 **연출 방식**이다 — 포켓몬 카드 게임 포켓의 체계를
//       빌렸는데, "위로 갈수록 더 반짝이게"가 아니라 카드마다 서로 다른 렌더링 기법을
//       쓰도록 나눴다. 기법이 겹치면 시험할 게 없어지기 때문이다.
//
//       **No.01 만 우리가 짠 것이고, 나머지 열 장은 @kongyo2/cards-css 의 포일이다.**
//       그래서 여기 이름은 우리가 지은 게 아니라 그쪽 포일 이름 그대로이고, 그대로
//       DOM 의 data-effect 값이 된다 — 바꾸려면 vendor/cards-css/ 에 그 이름의 파일이
//       있어야 하고 index.html 에 링크도 있어야 한다. 목록은 CARDS_CSS_EFFECTS.
//
//         01 immersive  꾹 누르면 카드 안으로 들어간다 (immersive.css / .mjs — 우리 것)
//         02 prism      무지개가 각도로 쪼개지는 분광
//         03 crystal    결정 패싯. 면마다 따로 꺾인다
//         04 gold       금박. 색상환을 안 돌고 명도만 오르내리는 이방성
//         05 oilslick   어두운 기름막 무지개. 밝은 원화 위에서는 안 보인다
//         06 sunburst   중심에서 뻗는 광선
//         07 holo       무지개 밴드 + 세로 스캔라인 (제일 고전적인 홀로)
//         08 reverse    가운데를 죽이고 가장자리를 살리는 역전 폴오프
//         09 aurora     넓고 부드러운 색 띠
//         10 cosmos     성운 결
//         11 mosaic     격자로 잘린 타일. 유일하게 무늬가 기하학이다
//         12 metal      세로로 긁힌 브러시드 결. 색상환을 안 돌고 명도만 오르내린다
//
//       **여기 없는 포일도 있다.** cards-css 는 14종인데, 그중 glitter 와 rainbow 만
//       CSS 로 못 쓴다 — 반짝이 텍스처(`--glitter`)를 JS 가 런타임에 만들어 넣기
//       때문이다. radiant 는 못 쓰는 게 아니라 안 고른 것이다 — reverse 와 느낌이
//       겹쳐서 11번에서 뺐다.
//
//       **`flat`(포일 없는 대조군)은 지금 아무 카드도 안 쓴다.** 규칙은 rarity.css 에
//       남아 있으니 되살리려면 여기 값만 바꾸면 된다.
// scene  : immersive 카드만 갖는다. 무대에 넘길 값 (immersive.mjs 가 읽는다).
//       back/subject 는 art/*.webp 와 별개인 **레이어 원화**다. 카드 그림이 아니라
//       프레임 없는 배경 한 장과 알파가 있는 주인공 한 장 — tools/neo-hologram-layers.py
//       로 뽑는다. 없으면 카드 그림으로 때우는데 보기엔 이상하다.

/**
 * rarity 값 중 @kongyo2/cards-css 가 그리는 것들. 여기 있는 이름은 그대로
 * DOM 의 data-effect 가 되고, vendor/cards-css/<이름>.css 가 그 선택자를 가진다.
 * 셋 다(이 목록 · vendor 파일 · index.html 링크) 맞아야 화면에 나온다.
 *
 * 텍스처를 JS 로 생성해야 하는 포일(glitter / mosaic / rainbow / gold)은 뺐다 —
 * 우리는 CSS 만 가져왔으므로 그것들은 거의 안 보인다.
 */
export const CARDS_CSS_EFFECTS = new Set([
  "prism", "crystal", "gold", "oilslick", "sunburst",   // No.02~06
  "holo", "reverse", "aurora", "cosmos", "mosaic",      // No.07~11
  "metal",                                              // No.12
]);

export const CARDS = [
  {
    no: 1,
    id: "cabbage",
    name: "Cabbage Neo",
    ko: "캐비지 네오",
    tagline: "강아지인지 채소인지 끝내 모를",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "LEAFY LOOK",
    moveNote: "Opponent stunned by awkward cuteness.",
    statLabel: "CRUNCH",
    stat: 820,
    flavor: "Part pup. Part produce. All confusion. Handle with salad.",
    edition: "Leafy Look Edition",
    art: "art/cabbage.webp",
    w: 810,
    h: 1125,
    rarity: "immersive",
    scene: {
      place: "이슬 맺힌 텃밭 · 해 뜨기 직전",
      back: "art/cabbage-back.webp",
      subject: "art/cabbage-subject.webp",
      // 진입 때만 쓰는 카드 그림. art/cabbage.webp 와 같은데 둥근 모서리 바깥의
      // 검정을 알파로 지운 것이다 — 화면만 하게 확대하면 네 귀퉁이가 검게 남는다.
      card: "art/cabbage-card.webp",
      // 그림 영역만 투명하게 지운 틀. 카드 그림과 같은 자리에 깔려 있다가, 카드가
      // 녹으면 드러나서 창틀이 된다.
      frame: "art/cabbage-card-frame.webp",
      // 틀에서 **뒤가 비치는 자리 전부**를 감싸는 상자 (카드 크기 대비 %).
      //
      // 처음엔 그림창만 재서 `y 14.39, h 63.16` 을 넣었는데, 그게 화면에 검은 자국을
      // 남겼다. 알파를 세로 가운데 열 하나로만 훑어서 **제일 긴 연속 구간**을 잡았고,
      // 그게 마침 그림창이었기 때문이다. 틀에는 그림창 말고도 뚫린 데가 더 있다 —
      // 원래 카드에서 **그림 위에 얹혀 있던 반투명 판**들이라, 그림을 지울 때 뒤가
      // 같이 비었다. 전부 훑으면 이렇게 나온다 (875x1216 기준):
      //
      //     y   0~ 20   카드 바깥 둥근 모서리        ← 여기는 비어 있는 게 맞다
      //     y 128~174   VEGGIE DOG 오른쪽 판         ← 검게 남았다
      //     y 175~942   그림창
      //     y 943~995   LEAFY LOOK 줄                ← 검게 남았다
      //     y 996~1111  CRUNCH 줄 왼쪽 (x 5.9~21%)
      //     y 1184~1215 카드 바깥 둥근 모서리        ← 여기도 비어 있는 게 맞다
      //
      // 그래서 창을 **모서리를 뺀 나머지 전부**의 경계상자로 잡는다. 창이 뚫린 데보다
      // 커도 상관없다 — 넘치는 만큼은 틀의 불투명한 부분이 가린다. 모양은 틀의 알파가
      // 잡고, 이 상자는 "뒤를 받쳐 줄 범위"만 정한다.
      // **모서리까지 삼키면 안 된다.** 그건 카드 실루엣 밖이라, 받쳐 주면 둥근 귀퉁이로
      // 텃밭이 새어 나와 카드가 직사각형으로 보인다. 위 10.28% · 아래 91.86% 는
      // 모서리(1.6% / 97.4%)와 넉넉히 떨어뜨린 값이다.
      // 실측 경계에서 사방 3px 씩 넓혔다 — 알파가 0 으로 떨어지기 전 안티에일리어싱
      // 구간(3px)이 검은 바탕과 섞이면 실금처럼 보인다.
      window: { x: 4.91, y: 10.28, w: 90.51, h: 81.58 },
      // (아래는 이 연출을 왜 이렇게 짰는지의 기록이다.) 카드 안으로 들어가는 연출을 제대로 하려고 미리
      // 만들어 둔 에셋이다 — 같은 카드에서 그림 영역만 투명하게 지운 틀이다
      // (art/cabbage-card-frame.webp, 875x1216, 알파 있음).
      //
      // 지금 연출은 카드가 제자리에서 녹고 누끼가 그 자리에 나타난다. "안으로
      // 들어갔다" 가 되려면 창틀이 시야 밖으로 밀려나야 하는데, 카드 그림 한 장에
      // 틀·인쇄배경·캐릭터가 다 들어 있어서 틀을 키우면 캐릭터도 같이 커진다.
      // 한 번 그렇게 해 봤다가 되돌렸다 — 카드와 누끼가 같은 배율로 픽셀까지 겹쳐
      // 있어서 크로스페이드가 안 보이던 건데, 배율이 달라지면 "큰 배추 위에 작은
      // 배추" 가 되어 뿌옇게 겹친다.
      //
      // 틀이 따로 있으면 정렬이 필요한 구간과 움직이는 구간을 나눌 수 있다:
      //   1) FLIP 은 그대로
      //   2) 카드 그림을 페이드아웃해 밑의 틀만 남기고, 창 안에 텃밭+누끼를 넣는다
      //      — 이 구간엔 아무것도 안 움직이므로 정렬이 유지된다
      //   3) 틀과 clip-path 창을 같이 키워 화면 밖으로 뺀다 → 통과
      //
      // 틀만으로는 부족하다. 창 밖 세상을 가리려면 clip-path 가 짝이어야 한다.
      // 그 창의 자리는 위 window 참고. (여기 적어 뒀던 그림창만의 값
      //   left 5.49  top 14.39  width 89.49  height 63.16
      // 은 뚫린 데를 다 못 담아서 위아래에 검은 자국이 남았다. 왜인지는 window 주석.)
      // 원본 카드 그림 안에서 누끼가 차지하는 자리 (카드 크기 대비 %).
      // 들어갈 때 카드와 누끼를 겹쳐 놓고 카드만 지우는데, 이 값이 맞아야
      // 틀이 녹는 동안 캐릭터가 한 픽셀도 안 움직인다. 둘이 같은 원화라 계산이 나온다 —
      // 누끼 캔버스(875x1216)의 경계상자를 카드 캔버스(810x1125) 배율로 나눈 값이다.
      fit: { x: 6.06, y: 14.15, w: 87.43, h: 62.70 },
      motes: 52,      // 떠다니는 초록빛
      leaves: 7,
      dew: 15,        // 렌즈 유리에 맺힌 이슬
      dewRun: 3,      // 그중 흘러내리는 것
      skinDew: 11,    // 배추 표면에 맺힌 이슬
      // 겉잎 겹. z = 속에서 띄우는 높이(px), r0~r1 = 안쪽에서 나타나는 구간(%),
      // r2~r3 = 바깥으로 사라지는 구간(%, 없으면 끝까지), shadow = 그림자 진하기.
      // 뒤 겹일수록 경계를 넓게 잡고 그림자를 옅게 준다 — 뒤에서 또렷한 테두리가
      // 보이면 잎이 겹친 게 아니라 원을 오려 붙인 걸로 보인다.
      shells: [
        { z: 34, r0: 14, r1: 44, r2: 54, r3: 86, shadow: .22 },   // 중간 잎
        { z: 70, r0: 44, r1: 62, r2: 62, r3: 72 },                 // 바깥 잎
      ],
    },
    accent: "#8fd94a",
    accent2: "#d8f07a",
    frame: "leaf",
  },
  {
    no: 2,
    id: "pepper",
    name: "Pepper Neo",
    ko: "페퍼 네오",
    tagline: "노랗고 수상하게 강한",
    code: "NEO-Y0824",
    type: "VEGGIE DOG",
    move: "YELLOW SHOCK",
    moveNote: "",
    statLabel: "CRISP",
    stat: 860,
    flavor: "Sweet face. Zero warning. Maximum pepper.",
    edition: "Prismatic Pepper Edition",
    art: "art/pepper.webp",
    w: 900,
    h: 1125,
    rarity: "prism",
    accent: "#ffd838",
    accent2: "#e27016",
    frame: "chrome",
  },
  {
    no: 3,
    id: "eggplant",
    name: "Eggplant Neo",
    ko: "에그플랜트 네오",
    tagline: "보라색으로 반들거리며 아무 생각 없는",
    code: "NEO-E0824",
    type: "VEGGIE DOG",
    move: "NIGHT SHADE",
    moveNote: "",
    statLabel: "GLOSS",
    stat: 900,
    flavor: "Deep purple. Empty thoughts. Unfairly glossy.",
    edition: "Night Shade Edition",
    art: "art/eggplant.webp",
    w: 900,
    h: 1125,
    rarity: "crystal",
    accent: "#a86bff",
    accent2: "#e0a3ff",
    frame: "chrome",
  },
  {
    no: 4,
    id: "carrot",
    name: "Carrot Neo",
    ko: "캐럿 네오",
    tagline: "흙에서 막 나왔는데 과하게 차려입은",
    code: "NEO-C0824",
    type: "VEGGIE DOG",
    move: "ROOT RUSH",
    moveNote: "",
    statLabel: "SNAP",
    stat: 830,
    flavor: "Straight from the dirt. Still overdressed.",
    edition: "Root Rush Edition",
    art: "art/carrot.webp",
    w: 900,
    h: 1125,
    rarity: "gold",
    accent: "#ff8a2b",
    accent2: "#ffc46b",
    frame: "chrome",
  },
  {
    no: 5,
    id: "danhobak",
    name: "Danhobak Neo",
    ko: "단호박 네오",
    tagline: "껍질만 단단하고 속은 물렁한",
    code: "NEO-D0824",
    type: "VEGGIE DOG",
    move: "SWEET IMPACT",
    moveNote: "",
    statLabel: "CRUNCH",
    stat: 840,
    flavor: "Hard shell. Soft Neo.",
    edition: "Hard Shell Edition",
    art: "art/danhobak.webp",
    w: 900,
    h: 1125,
    rarity: "oilslick",
    accent: "#7d9b46",
    accent2: "#d8bb4e",
    frame: "chrome",
  },
  {
    no: 6,
    id: "mushroom",
    name: "Mushroom Neo",
    ko: "머쉬룸 네오",
    tagline: "나비넥타이까지 맨 포자 살포자",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "FUNGAL FACE",
    moveNote: "Mushroom master of confusing cuteness.",
    statLabel: "MYCELIUM MASH",
    stat: 820,
    flavor: "Part pup. Part fungi. Totally bizarre. Watch for spores.",
    edition: "Spore Bloom Edition",
    art: "art/mushroom.webp",
    w: 810,
    h: 1125,
    rarity: "sunburst",
    accent: "#cbb08a",
    accent2: "#9fd06a",
    frame: "leaf",
  },
  {
    no: 7,
    id: "broccoli",
    name: "Broccoli Neo",
    ko: "브로콜리 네오",
    tagline: "왕관은 큰데 판단력은 작은",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "FLORET FORCE",
    moveNote: "Big crown. Tiny judgment.",
    statLabel: "MYCELIUM MASH",
    stat: 850,
    flavor: "Big crown. Tiny judgment.",
    edition: "Floret Force Edition",
    art: "art/broccoli.webp",
    w: 816,
    h: 1125,
    rarity: "holo",
    accent: "#7bbf3a",
    accent2: "#cfe89a",
    frame: "leaf",
  },
  {
    no: 8,
    id: "cucumber",
    name: "Cucumber Neo",
    ko: "큐컴버 네오",
    tagline: "거의 물인데 태도만은 확실한",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "COOL CRUNCH",
    moveNote: "Mostly water. Entirely attitude.",
    statLabel: "MYCELIUM MASH",
    stat: 810,
    flavor: "Mostly water. Entirely attitude.",
    edition: "Cool Crunch Edition",
    art: "art/cucumber.webp",
    w: 816,
    h: 1125,
    rarity: "reverse",
    accent: "#4fae52",
    accent2: "#bde89e",
    frame: "leaf",
  },
  {
    no: 9,
    id: "spinach",
    name: "Spinach Neo",
    ko: "스피니치 네오",
    tagline: "잎은 부드러운데 힘이 말이 안 되는",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "IRON LEAF",
    moveNote: "Soft leaf. Unreasonable power.",
    statLabel: "MYCELIUM MASH",
    stat: 860,
    flavor: "Part pup. Part fungi. Totally bizarre. Watch for spores.",
    edition: "Iron Leaf Edition",
    art: "art/spinach.webp",
    w: 816,
    h: 1125,
    rarity: "aurora",
    accent: "#3f8f3f",
    accent2: "#a8d97a",
    frame: "leaf",
  },
  {
    no: 10,
    id: "sweet-potato",
    name: "Sweet Potato Neo",
    ko: "스위트포테이토 네오",
    tagline: "깊이 묻혀 있다가 더 깊이 차려입고 나온",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "ROOT RUMBLE",
    moveNote: "Buried deep. Dressed deeper.",
    statLabel: "MYCELIUM MASH",
    stat: 830,
    flavor: "Buried deep. Dressed deeper.",
    edition: "Root Rumble Edition",
    art: "art/sweet-potato.webp",
    w: 816,
    h: 1125,
    rarity: "cosmos",
    accent: "#a0656f",
    accent2: "#d8a89e",
    frame: "leaf",
  },
  {
    no: 11,
    id: "tomato",
    name: "Tomato Neo",
    ko: "토마토 네오",
    tagline: "잘 익고 둥글고 준비까지 끝난",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "JUICY BLAST",
    moveNote: "",
    // 이 카드만 스탯 바에 라벨이 안 찍혀 있다. 숫자와 별만 있다 — 지어내지 않고 비워 둔다.
    statLabel: "",
    stat: 840,
    flavor: "Ripe, round, and ready.",
    edition: "Juicy Blast Edition",
    art: "art/tomato.webp",
    w: 816,
    h: 1125,
    rarity: "mosaic",
    accent: "#cc351a",
    accent2: "#f29483",
    frame: "leaf",
  },
  {
    no: 12,
    id: "lettuce",
    name: "Lettuce Neo",
    ko: "레터스 네오",
    tagline: "잎은 제멋대로인데 웃음만 큰",
    code: "NEO-0824",
    type: "VEGGIE DOG",
    move: "LEAF PARADE",
    moveNote: "Loose leaves strut in a fresh breeze.",
    statLabel: "FRESH FLUTTER",
    stat: 800,
    flavor: "Loose leaves. Loud smile.",
    edition: "Leaf Parade Edition",
    art: "art/lettuce.webp",
    w: 900,
    h: 1125,
    rarity: "metal",
    accent: "#b2d121",
    accent2: "#e3f493",
    frame: "leaf",
  },
];

/** 스탯 표기. No.11 Tomato 는 그림에 라벨이 안 찍혀 있어 숫자만 나온다. */
export const statText = (card) => (card.statLabel ? `${card.statLabel} ${card.stat}` : String(card.stat));

/** 스크린리더용 카드 설명. 그림 속 인쇄 문구를 그대로 읽어준다. */
export const altText = (card) =>
  `${card.name} — ${card.type}, ${card.code}. ` +
  `기술 ${card.move}, ${statText(card)}. ${card.flavor}`;
