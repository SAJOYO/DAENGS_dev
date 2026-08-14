const path = require("path");

// PM2 설정 파일.
//   pm2 start ecosystem.config.js     처음 실행
//   pm2 reload daengs-web             무중단 재시작 (cluster 모드에서만 무중단)
//   pm2 list / logs / monit           상태 확인
module.exports = {
  apps: [
    {
      name: "daengs-web",

      // next.config.ts 의 output: "standalone" 이 만들어 내는 순수 Node 진입점.
      // cluster 모드는 대상이 Node 스크립트일 때만 제대로 동작하므로
      // "npm start" 가 아니라 이 파일을 직접 가리켜야 합니다.
      cwd: path.join(__dirname, "frontend", ".next", "standalone"),
      script: "server.js",

      // fork = 프로세스 1개(1코어). cluster = 여러 개가 한 포트를 나눠 씀.
      exec_mode: "cluster",
      instances: 2, // 코어를 전부 쓰려면 "max"

      env: {
        NODE_ENV: "production",
        PORT: 3000,
        // nginx 가 컨테이너 안에서 호스트로 접속해 오므로
        // 127.0.0.1 이 아닌 모든 인터페이스에서 받아야 합니다.
        HOSTNAME: "0.0.0.0",
      },

      max_memory_restart: "500M", // 메모리 누수 시 자동 재시작
      autorestart: true,
      time: true, // 로그에 타임스탬프
    },
  ],
};
