import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // PM2 cluster 모드로 띄우려면 순수 Node 진입점이 필요합니다.
  // 이 설정을 켜면 빌드 후 .next/standalone/server.js 가 생성됩니다.
  output: "standalone",
};

export default nextConfig;
