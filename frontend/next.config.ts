import type { NextConfig } from "next";

// 개발 중 API 를 어디로 보낼지. 기본값은 개발 PC 에서 `uv run dev` 로 띄운 backend 입니다.
// 서버 DB 를 보는 backend 에 붙이고 싶으면 .env.local 에 다른 값을 넣으세요.
const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // 같은 네트워크의 폰에서 개발 서버를 열 수 있게 합니다 (`npm run dev:https`).
  //
  // Next 는 기본적으로 localhost 가 아닌 곳에서 오는 dev 리소스 요청(`/_next/...`, HMR)을
  // 막습니다. 막혀도 `public/` 정적 파일은 그냥 열리지만, 앱 페이지는 청크를 못 받아
  // 하얗게 뜹니다. 폰 실기 확인 때 이것 때문에 한 번 헤맵니다.
  //
  // **개발용입니다.** `next dev` 에서만 읽고 운영 빌드와는 무관합니다.
  // 192.168.0.* 는 팀의 LAN 대역이라, DHCP 로 IP 가 바뀌어도 계속 맞습니다.
  allowedDevOrigins: ["192.168.0.*"],

  // PM2 cluster 모드로 띄우려면 순수 Node 진입점이 필요합니다.
  // 이 설정을 켜면 빌드 후 .next/standalone/server.js 가 생성됩니다.
  output: "standalone",

  // 로컬에서도 API 를 같은 오리진(/api/)으로 보이게 합니다.
  //
  // **httpOnly 쿠키 때문에 필요합니다** (D-015). 브라우저에게 localhost:3000 과
  // 127.0.0.1:8000 은 다른 오리진이라 쿠키가 그냥은 실리지 않습니다. 배포에서는
  // nginx 의 /api/ 블록이 같은 일을 하므로, 프론트 코드는 양쪽에서 똑같이
  // `fetch("/api/auth/login")` 을 쓰면 됩니다.
  //
  // **API 주소를 코드에 박지 마세요.** `http://daengback.~:8000` 으로 직접 부르면
  // 오리진이 갈려서 로그인은 되는데 쿠키가 안 남습니다.
  //
  // rewrites 는 개발 서버와 `next start` 에서 동작합니다. 배포에서는 요청이
  // nginx 를 먼저 지나므로 여기까지 오지 않습니다.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/:path*`,
      },
    ];
  },

  // 위 rewrites 프록시가 backend 응답을 기다리는 시간. 기본 30초인데 도감 카드 생성(#496)은
  // 한 장 20~60초, 유사도 미달로 한 번 더 만들면 그 두 배라 backend 가 200 을 내고도 브라우저는
  // 500(`socket hang up`)을 받는다 — 09-14 콘솔 실측. 배포의 nginx `/api/admin/cardimage/` 300s 와 맞춘다.
  experimental: {
    proxyTimeout: 300_000,
  },
};

export default nextConfig;
