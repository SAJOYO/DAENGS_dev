// 카드 6장의 데이터.
//
// name/code/move/stat/flavor 는 지어낸 게 아니라 그림 안에 이미 인쇄돼 있는 문구를
// 그대로 옮긴 것이다. 이미지는 홀로그램 효과를 입힐 밑그림이 아니라 그 자체로 완성된
// 트레이딩 카드라서(프레임·제목바·스탯바 포함), 여기 텍스트는 그림의 사본 역할을 한다.
// 확대 뷰 캡션과 alt 텍스트가 이걸 쓰기 때문에 그림을 못 봐도 내용이 전달된다.
//
// no  : 도감 번호. 원본 PNG 를 만든 순서(파일 수정시각) 그대로다.
// w/h : art/*.webp 의 실제 픽셀. 크롭을 안 했으므로 비율이 두 종류로 갈린다
//       (0.80 넉 장, 0.72 두 장). CSS 가 이 값으로 카드마다 aspect-ratio 를 잡는다.
// accent/accent2 : 카드 뒤 글로우와 테두리에 쓸 색. 그림에서 뽑았다.
// frame : 프레임 디자인. 크롬(chrome) 넉 장, 초록 홀로(leaf) 두 장.
// rarity : 레어도. 등급표가 아니라 **연출 방식**이다 — 포켓몬 카드 게임 포켓의 체계를
//       빌렸는데, "위로 갈수록 더 반짝이게"가 아니라 티어마다 서로 다른 렌더링 기법을
//       쓰도록 나눴다. 기법이 겹치면 시험할 게 없어지기 때문이다.
//         flat      ◇◇◇   포일 없음. 기울기와 그림자만 — 이머시브의 대조군
//         ex        ◇◇◇◇  mask-image 로 아트 창에만 홀로
//         fullart   ☆     무지개 conic 포일 — style.css 의 기본값
//         etched    ☆☆    글레어가 지나갈 때만 드러나는 각인 텍스처
//         crown     ♛     금속 금박. 색상환을 안 돌고 명도만 오르내리는 이방성이라
//                          기존 포일 코드를 그대로 못 쓴다
//         immersive ☆☆☆   꾹 누르면 카드 안으로 들어간다 (immersive.css / .mjs)
//       **지금 구현된 건 immersive 하나뿐이다.** 나머지는 배정만 적어 둔 것이고
//       화면에는 전부 기본 풀아트로 보인다. 다음 PR 에서 하나씩 붙인다.
// scene  : immersive 카드만 갖는다. 무대에 넘길 값 (immersive.mjs 가 읽는다).

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
      motes: 34,
      leaves: 7,
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
    rarity: "etched",
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
    rarity: "fullart",
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
    rarity: "crown",
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
    rarity: "ex",
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
    rarity: "flat",
    accent: "#cbb08a",
    accent2: "#9fd06a",
    frame: "leaf",
  },
];

/** 스크린리더용 카드 설명. 그림 속 인쇄 문구를 그대로 읽어준다. */
export const altText = (card) =>
  `${card.name} — ${card.type}, ${card.code}. ` +
  `기술 ${card.move}, ${card.statLabel} ${card.stat}. ${card.flavor}`;
