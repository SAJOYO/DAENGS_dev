"""커밋 대상 산출물에 청크 본문이 실려 나가지 않는다.

`test_run_combined_retrieval_eval.py`의 `without_chunk_text` 테스트는 **함수 하나**를
검증한다. 그건 "그 함수를 호출하는 경로에서는 본문이 빠진다"는 뜻이지
"어떤 산출물에도 본문이 없다"는 뜻이 아니다 — 새 스크립트가 새 경로로 본문을 쓰면
그 테스트는 통과한 채로 원문이 커밋된다. 실제로 그렇게 들어간 것이
`data/eval/results/combined_v4_e5_metrics.json`(청크 본문 307개 116,681자)과
`data/eval/queries/_synthetic_prompt.md`(유튜브 자막 20개 6,671자)였다.
`reports/license_premise_audit_0825.md`.

그래서 불변식을 산출물 기준으로 올린다: **git이 추적하는 파일 안에 청크 본문이
없다.** 어떤 스크립트가 무슨 경로로 썼는지와 무관하게, 커밋된 결과만 본다.

두 가지를 본다.
1. 구조 — `chunk_id`를 가진 객체가 `text` 같은 본문 필드를 함께 갖고 있는가.
2. 분량 — 사람이 쓴 설명이 아니라 원문을 통째로 나르는 크기인가.

`data/processed/`는 대상이 아니다. 코퍼스 자체는 gitignore로 이미 빠져 있고,
이 테스트는 "코퍼스가 다른 경로로 새어 나갔는가"만 본다.
"""

import json
import subprocess
import unittest

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[2]
PACKAGE_PREFIX = "backend/src/daengs_training/"

# 청크를 식별하는 키. 이 중 하나를 가진 객체는 청크를 가리키는 레코드다.
CHUNK_KEYS = ("chunk_id", "chunk_index")
# 본문이 실릴 수 있는 필드 이름.
BODY_KEYS = ("text", "chunk_text", "body", "passage", "content")
# 이보다 길면 인용이 아니라 본문 적재로 본다. run_combined_retrieval_eval.py의
# SNIPPET_CHARS(150)와 같은 값 — 리포트가 찍는 발췌는 이 길이를 넘지 않는다.
SNIPPET_LIMIT = 150

# 면제는 비어 있다. 2026-08-25에 마지막 항목
# (data/eval/results/combined_v4_e5_metrics.json, 청크 본문 300개 105,326자)을
# 리댁션해서 없앴다 — reports/license_premise_audit_0825.md 13절 A안.
#
# **여기에 항목을 추가하려면 사람의 승인이 필요하다.** 새로 걸리는 파일은 고칠
# 대상이지 면제 대상이 아니다. 면제가 경로 기준이라, 한 번 넣어두면 같은 경로가
# 재생성되며 내용이 늘어도 통과한다 — 실제로 그 구멍으로 wayopet 본문이 스냅샷에
# 다시 들어왔다.
KNOWN_LEGACY: set[str] = set()


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", PACKAGE_PREFIX], cwd=REPO, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def chunk_bodies(node: object, path: str = "") -> list[tuple[str, int]]:
    """(위치, 길이) — chunk를 가리키는 객체에 실린 본문 필드."""
    found: list[tuple[str, int]] = []
    if isinstance(node, dict):
        identifies_chunk = any(k in node for k in CHUNK_KEYS)
        for key, value in node.items():
            if identifies_chunk and key in BODY_KEYS and isinstance(value, str):
                if len(value) > SNIPPET_LIMIT:
                    found.append((f"{path}/{key}", len(value)))
            found.extend(chunk_bodies(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(chunk_bodies(value, f"{path}[{index}]"))
    return found


class CommittedArtifactsTests(unittest.TestCase):
    def test_no_tracked_json_carries_a_chunk_body(self):
        offenders: dict[str, list[tuple[str, int]]] = {}
        for rel in tracked_files():
            if not rel.endswith((".json", ".jsonl")):
                continue
            if rel in KNOWN_LEGACY:
                continue
            raw = (REPO / rel).read_text(encoding="utf-8")
            try:
                docs = (
                    [json.loads(l) for l in raw.splitlines() if l.strip()]
                    if rel.endswith(".jsonl") else [json.loads(raw)]
                )
            except json.JSONDecodeError:
                continue
            hits = [h for doc in docs for h in chunk_bodies(doc)]
            if hits:
                offenders[rel] = hits[:5]
        self.assertEqual(
            offenders, {},
            "청크 본문이 실린 추적 파일이 있습니다. 산출물에서 본문을 빼고 다시 만드세요 "
            "— KNOWN_LEGACY에 추가하는 것은 답이 아닙니다.",
        )

    def test_the_legacy_exception_list_still_describes_something_real(self):
        """면제 목록이 낡으면 조용히 무의미해지므로, 아직 위반 중인지 확인한다."""
        for rel in KNOWN_LEGACY:
            path = REPO / rel
            if not path.is_file():
                continue  # 이미 제거됐다면 목록에서 빼면 된다
            hits = chunk_bodies(json.loads(path.read_text(encoding="utf-8")))
            self.assertTrue(
                hits,
                f"{rel}에 더 이상 청크 본문이 없습니다. KNOWN_LEGACY에서 지우세요.",
            )

    def test_the_youtube_subtitle_prompt_is_not_tracked_again(self):
        """자막 지문을 나르던 두 경로가 다시 추적 대상이 되지 않는지 본다."""
        tracked = set(tracked_files())
        for rel in (
            "data/eval/queries/_synthetic_prompt.md",
            "data/eval/queries/_synthetic_mapping.json",
        ):
            self.assertNotIn(
                rel, tracked,
                f"{rel}은 유튜브 자막 원문 또는 그 재현 좌표를 담습니다. "
                "docs/SOURCES.md가 금지라고 적어둔 것과 같은 자료입니다.",
            )


if __name__ == "__main__":
    unittest.main()
