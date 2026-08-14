const path = require("path");

// 배포 구조
//
//   C:\deploy\daengs\
//      releases\<커밋해시>\   ← 배포마다 새로 쌓임
//      releases\<이전해시>\   ← 롤백용으로 몇 개 남겨 둠
//      current  ──→ releases\<커밋해시>   (junction)
//
// 배포는 "새 폴더에 빌드 결과를 넣고 → current 만 갈아끼우고 → reload" 순서입니다.
// 빌드하는 폴더와 실행 중인 폴더가 서로 달라서
// Windows 파일 잠김(EBUSY)이 발생하지 않습니다.
const DEPLOY_ROOT = process.env.DAENGS_DEPLOY_ROOT || "C:\\deploy\\daengs";

module.exports = {
  apps: [
    {
      name: "daengs-web",

      // current 를 통해 실행합니다. Node 가 링크를 실제 경로로 풀어 주기 때문에
      // reload 로 새로 뜨는 워커는 그 시점의 current 가 가리키는 릴리스를 읽습니다.
      script: path.join(DEPLOY_ROOT, "current", "server.js"),

      // cwd 를 current 로 두면 프로세스가 링크를 붙잡아 교체가 막힙니다.
      // 한 단계 위를 가리켜야 합니다.
      // (standalone 의 server.js 는 시작하면서 스스로 자기 폴더로 chdir 합니다)
      cwd: DEPLOY_ROOT,

      // fork = 프로세스 1개(1코어). cluster = 여러 개가 한 포트를 나눠 씀.
      // reload 의 무중단은 cluster 모드에서만 동작합니다.
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
