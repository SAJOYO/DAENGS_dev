"""④ 왕복 증분 검증 — 증분이 만든 parquet 이 전량 실행과 같은가 (A1 / RAG-064).

────────────────────────────────────────────────────────────────────────────
왜 "왕복" 인가
────────────────────────────────────────────────────────────────────────────
곧이곧대로 재려면 전량 재인코딩(62분)을 한 번 더 해서 증분 결과와 비교해야 한다.
왕복은 그 62분을 안 내고 같은 것을 잰다:

    ① 실제 코퍼스와 실제 parquet 을 격리 폴더로 복사
       (실제 parquet 은 2026-08-30 의 **순수한 전량 실행** 결과다 — 이것이 기준선)
    ② N청크를 고친다 → 증분 실행  → N행이 새 내용으로 인코딩됨
    ③ 그 N청크를 되돌린다 → 증분 실행 → N행이 **원래 내용으로 다시** 인코딩됨
    ④ 결과를 실제 parquet 과 비교

④ 시점에 두 파일은 **같은 코퍼스**다. 차이는 하나뿐이다 — N행은 *오늘 작은 묶음으로*
만들어졌고 나머지는 *8월 전량 실행에서 복사*됐다. 그래서:

    복사된 행    비트 단위로 같아야 한다  → 병합·배치가 정확한지 실물에서 증명
    다시 만든 N행 값만 미세하게 다를 수 있다 → 정찰이 잰 1e-7 수준이면 통과

⚠ **실제 데이터는 읽기만 한다.** 쓰는 곳은 격리 폴더뿐이다.
⚠ 격리 폴더를 Temp 밑에 두지 않는다 — 2026-09-06 에 Temp 안의 청크 파일이 원인 미상으로
  원본으로 되돌아가 비교가 무효가 된 적이 있다.

사용: uv run python tools/a1_roundtrip_verify.py [N]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "data" / "processed"
# **Temp 밑에 두지 않는다** — 2026-09-06 에 Temp 안의 청크가 원인 미상으로 되돌아가
# 비교가 무효가 됐다. 저장소 밖 형제 폴더에 만들고 끝나면 지운다.
DATA_DIR = REPO.parent / "_a1verify"
WORK = DATA_DIR / "processed"
BACKUP = REPO.parent / "_a1verify_backup"
MARK = " [왕복검증 표식]"
TOL = 1e-5          # 정찰(RAG-064 ①)이 잰 7.0e-07 의 14배 여유


def sh(*args: str, data_dir: Path) -> str:
    env = {**os.environ, "DAENGS_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    # `check=False` 로 두고 아래에서 직접 본다 — 실패했을 때 stdout 까지 같이 보여 줘야
    # 어느 단계에서 죽었는지 알 수 있다 (CalledProcessError 는 stdout 을 안 싣는다).
    r = subprocess.run([sys.executable, "-m", "daengs_life.rag", *args],
                       env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=REPO / "backend", check=False)
    if r.returncode != 0:
        raise SystemExit(f"실패: rag {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def pick_chunk_files(want: int) -> list[Path]:
    """청크 `want` 개가 모일 때까지 파일을 고른다. **파일 단위로 고른다** — 증분이 파일이
    아니라 행을 보는지 확인하려면 한 파일 안의 일부만 바꾸는 편이 낫지만, 되돌리기를
    파일 복사로 하는 편이 실수가 없다."""
    picked, total = [], 0
    for p in sorted((WORK / "chunks").glob("*.jsonl")):
        n = sum(1 for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("type") == "chunk")
        if not n:
            continue
        picked.append(p)
        total += n
        if total >= want:
            break
    return picked


def edit(paths: list[Path]) -> set[str]:
    """고른 파일들의 청크 내용에 표식을 붙인다. 바뀐 `chunk_id` 를 돌려준다."""
    touched: set[str] = set()
    for p in paths:
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("type") == "chunk":
                d["content"] += MARK
                d["chars"] = len(d["content"])
                touched.add(d["chunk_id"])
            out.append(json.dumps(d, ensure_ascii=False))
        p.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return touched


def read(path: Path):
    t = pq.read_table(path)
    md = {k.decode(): v.decode() for k, v in (t.schema.metadata or {}).items()}
    return (t["chunk_id"].to_pylist(),
            np.asarray(t["embedding"].to_pylist(), dtype=np.float32),
            t["content_sha256"].to_pylist(), md)


def main() -> int:
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    real_parquet = REAL / "embeddings" / "qwen3-embedding-0.6b.parquet"
    if not real_parquet.is_file():
        raise SystemExit("실제 parquet 이 없다")

    print(f"격리 폴더 {DATA_DIR}")
    for d in (DATA_DIR, BACKUP):
        if d.exists():
            shutil.rmtree(d)
    (WORK / "chunks").mkdir(parents=True)
    (WORK / "embeddings").mkdir(parents=True)
    for p in (REAL / "chunks").glob("*.jsonl"):
        shutil.copy2(p, WORK / "chunks" / p.name)
    shutil.copy2(real_parquet, WORK / "embeddings" / real_parquet.name)
    shutil.copytree(WORK / "chunks", BACKUP)
    print(f"  청크 {len(list((WORK / 'chunks').glob('*.jsonl')))}파일 · parquet 1 복사 (실제는 읽기만)")

    data_dir = DATA_DIR
    files = pick_chunk_files(want)
    touched = edit(files)
    print(f"\n② {len(files)}개 파일의 청크 {len(touched)}개를 고친다")
    out = sh("embed", "--model", "qwen3-embedding-0.6b", "--quiet", data_dir=data_dir)
    print("  " + "\n  ".join(ln for ln in out.splitlines() if "incremental" in ln or "written" in ln))

    for p in BACKUP.glob("*.jsonl"):
        shutil.copy2(p, WORK / "chunks" / p.name)
    print(f"\n③ 되돌린다 → 그 {len(touched)}개가 원래 내용으로 다시 인코딩된다")
    out = sh("embed", "--model", "qwen3-embedding-0.6b", "--quiet", data_dir=data_dir)
    print("  " + "\n  ".join(ln for ln in out.splitlines() if "incremental" in ln or "written" in ln))

    print("\n④ 실제 parquet(2026-08-30 전량 실행)과 대조")
    ids_a, vec_a, sha_a, md_a = read(real_parquet)
    ids_b, vec_b, sha_b, md_b = read(WORK / "embeddings" / real_parquet.name)

    ok = True
    for label, a, b in (("chunk_id 순서", ids_a, ids_b),
                        ("content_sha256", sha_a, sha_b),
                        ("전역 지문", md_a["chunks_sha256"], md_b["chunks_sha256"])):
        same = a == b
        ok &= same
        print(f"  {'OK  ' if same else 'FAIL'} {label} {'같다' if same else '다르다'}")
    if not ok:
        return 1

    redone = np.array([cid in touched for cid in ids_a])
    kept = ~redone
    print(f"\n  재사용 {int(kept.sum()):,}행 · 다시 만든 {int(redone.sum()):,}행")

    kept_exact = int(np.sum(np.all(vec_a[kept] == vec_b[kept], axis=1)))
    print(f"  {'OK  ' if kept_exact == int(kept.sum()) else 'FAIL'} "
          f"재사용 행이 비트 단위로 동일  {kept_exact:,}/{int(kept.sum()):,}")
    ok &= kept_exact == int(kept.sum())

    if not redone.any():
        print("  FAIL 다시 만든 행이 0이다 — 왕복이 성립하지 않았다")
        return 1

    d = np.abs(vec_a[redone] - vec_b[redone])
    exact = int(np.sum(np.all(vec_a[redone] == vec_b[redone], axis=1)))
    print(f"       다시 만든 행 — 비트 동일 {exact:,}/{int(redone.sum()):,} · "
          f"최대 절대차 {d.max():.3e} · 평균 {d.mean():.3e}")
    within = d.max() <= TOL
    ok &= within
    print(f"  {'OK  ' if within else 'FAIL'} 최대 절대차 {d.max():.3e} "
          f"{'<=' if within else '>'} 허용치 {TOL:.0e}")

    print("\n" + ("  ✅ 통과 — 증분 결과가 전량 실행과 같다" if ok else "  ❌ 실패"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
