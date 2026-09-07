"""CLI.

  python -m rag list                                  # 원본 목록 + 파서 구현 여부
  python -m rag parse                                 # 바뀐 것만 파싱
  python -m rag parse --source law-drf-api -v
  python -m rag parse --force                         # 원본이 그대로여도 다시
  python -m rag chunk                                 # parsed → chunks (바뀐 것만)
  python -m rag chunk --source easylaw-pet -v
  python -m rag show <chunk_id 조각>                  # 검문소①용 — 청크를 눈으로 본다
  python -m rag goldenset                             # 골든셋 라벨이 실재하는지 검사 (RAG-022)
  python -m rag goldenset -v                          # 문항별 라벨까지 전부
  python -m rag embed                                 # 4단계 임베딩 — **기본은 서빙 모델 하나** (RAG-064)
  python -m rag embed --all                           # 3종 전부 (6단계 3파전용, 55분 x 3)
  python -m rag embed --backfill-hashes               # 옛 parquet 에 행별 해시만 채운다 (벡터 무변경)
  python -m rag evaluate                              # 6단계 채점 — 기본은 서빙 모델 하나
  python -m rag evaluate --all -v                     # 3파전 판정 (승자 고르기는 셋이 다 있어야 한다)
  python -m rag load                                  # 7단계 documents 적재 (RAG-025)
  python -m rag load --dry-run                        # DB 를 안 건드리고 만들 행만 확인
  python -m rag load --model qwen3-embedding-0.6b     # 모델 교체 = 같은 명령 재실행
  python -m rag load --prune                          # 개정으로 사라진 청크의 행까지 지운다
  python -m rag search "목줄 안 하면 과태료 얼마"      # 8단계 dense 검색 (RAG-026)
  python -m rag search --questions                    # 검증질문 1~7 전부 = 검문소③
  python -m rag search --questions --no-supplementary # 부칙을 뺀 결과와 비교
  python -m rag generate "목줄 안 하면 과태료 얼마"    # 9단계 검색+Gemini (RAG-028)
  python -m rag generate --questions                  # 검증질문 1~7 전부 = **검문소④** + 1랩 덤프
  python -m rag generate --questions --dry-run        # 덤프를 쓰지 않는다
  python -m rag score-laps                            # 저장된 랩을 전부 새 지표로 소급 채점 (RAG-029)
  python -m rag score-laps --by source_id             # 종류별 슬라이스 — 총계가 감추는 것 (RAG-060)
  python -m rag score-laps --by source_id --laps 0    # 최근 6개가 아니라 전부
"""
from __future__ import annotations

import argparse
import collections
import sys
import traceback
from pathlib import Path

from .core import config, io
from .stages import chunk as chunker
from .stages import embed, evaluate, generate as generator, goldenset, parse
from .stages import load as loader
from .stages import score as scorer
from .core import transport, vocabulary
from .stages import search as searcher

# 윈도우 콘솔 기본 인코딩(cp949)으로는 한글이 깨지고 일부 기호는 예외를 낸다 (crawler CLI 와 같은 처리).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_list(args: argparse.Namespace) -> int:
    for doc in io.raw_docs(args.source):
        label, reason = parse.status(doc)
        mark = {"OK": "x", "TODO": " ", "NOT-INDEXED": "-"}[label]
        print(f"[{mark}] {doc.doc_id:44s} {doc.source_id:24s} {reason}")
    print("\n  x=파서 있음   (공백)=파서 없음   -=인덱싱 대상 아님(결정)")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    """raw → parsed. **파싱 자체는 `stages.parse` 가 하고 여기는 출력만 한다** (RAG-023).

    다른 단계는 전부 자기 모듈에 로직이 있는데 parse 만 CLI 안에 있었다.
    """
    docs = io.raw_docs(args.source)
    if args.limit:
        docs = docs[: args.limit]

    n_parsed = n_same = n_skipped = n_failed = 0
    total: collections.Counter[str] = collections.Counter()

    for doc in docs:
        label, reason = parse.status(doc)
        if label != "OK":
            print(f"  {label:11s} {doc.doc_id:44s} {reason}")
            n_skipped += 1
            continue
        if io.is_current(doc, parse.parser_version(doc)) and not args.force:
            n_same += 1
            if args.verbose:
                print(f"  {'same':11s} {doc.doc_id}")
            continue

        try:
            header, elements, counts = parse.parse_doc(doc)
        except Exception:
            print(f"  {'FAIL':11s} {doc.doc_id}\n{traceback.format_exc()}")
            n_failed += 1
            continue

        total += collections.Counter(counts)
        n_parsed += 1
        if args.dry_run:
            print(f"  {'(dry-run)':11s} {doc.doc_id:44s} {counts}")
        else:
            path = io.write(header, elements)
            print(f"  {'parsed':11s} {doc.doc_id:44s} {counts}")
            if args.verbose:
                print(f"              -> {path}")
        for w in header.warnings:
            print(f"              ! {w}")

    print(f"\n요소 합계: {dict(sorted(total.items()))}")
    print(f"parsed {n_parsed}, same {n_same}, skipped {n_skipped}, failed {n_failed}"
          + ("   (dry-run: 아무것도 쓰지 않음)" if args.dry_run else ""))
    return 1 if n_failed else 0


def cmd_chunk(args: argparse.Namespace) -> int:
    """parsed → chunks. 스킵 판단·집계 모양을 `parse` 와 같게 둔다."""
    paths = io.parsed_files()
    n_written = n_same = n_failed = 0
    total: collections.Counter[str] = collections.Counter()
    dropped: collections.Counter[str] = collections.Counter()
    warnings: list[str] = []
    seen: dict[str, str] = {}          # content_hash → 먼저 본 chunk_id
    dups: list[tuple[str, str]] = []

    for path in paths:
        head = io.read_header(path)
        if args.source and (head or {}).get("source_id") != args.source:
            continue
        if io.is_chunk_current(path, chunker.VERSION) and not args.force:
            n_same += 1
            if args.verbose:
                print(f"  {'same':11s} {path.stem}")
            continue
        try:
            header, res = chunker.chunk_file(path)
        except Exception:
            print(f"  {'FAIL':11s} {path.stem}\n{traceback.format_exc()}")
            n_failed += 1
            continue

        total.update(c.element_type for c in res.chunks)
        dropped.update(res.dropped)
        warnings += res.warnings
        for c in res.chunks:
            h = chunker.content_hash(c.content)
            if h in seen:
                dups.append((seen[h], c.chunk_id))
            else:
                seen[h] = c.chunk_id

        n_written += 1
        if args.dry_run:
            print(f"  {'(dry-run)':11s} {path.stem:44s} 청크 {len(res.chunks):4d}")
        else:
            out = io.write_chunks(header, res.chunks)
            print(f"  {'chunked':11s} {path.stem:44s} 청크 {len(res.chunks):4d}")
            if args.verbose:
                print(f"              -> {out}")

    print(f"\n청크 합계: {dict(sorted(total.items()))}  총 {sum(total.values())}")
    if dropped:
        # 무엇을 왜 뺐는지 항상 보여 준다. 조용한 소실이 이 프로젝트에서 두 번 문제가 됐다 (RAG-021 ①·⑤D)
        print(f"제외:      {dict(dropped)}")
    for w in warnings:
        print(f"  ! {w}")
    if dups:
        # 합쳐지는 것 자체는 옳다(내용이 같다). 조용한 것이 문제다 — 7단계 적재기가 content_hash 로
        # 합칠 행을 미리 드러낸다 (RAG-021 ⑤D)
        print(f"  ! content 중복 {len(dups)}건 — 적재 시 content_hash 로 합쳐진다")
        for first, later in dups:
            print(f"      {later}  ==  {first}")
    print(f"chunked {n_written}, same {n_same}, failed {n_failed}"
          + ("   (dry-run: 아무것도 쓰지 않음)" if args.dry_run else ""))
    return 1 if n_failed else 0


def _target_models(args: argparse.Namespace) -> list[str]:
    """이 실행이 다룰 모델. **기본은 서빙 모델 하나다** (RAG-064 ⑦).

    예전 기본은 `MODELS` 전부(3종)였다. 그것은 6단계 3파전(RAG-002 · 024)의 기본값이고,
    RAG-024 가 *"7~9단계 첫 관통은 기준선 `bge-m3` 로 간다"* 고 한 동안은 둘이 다 필요했다.
    **그 기간이 끝났는데 기본값이 안 따라왔다:**

      - 서빙은 `config.settings.embedding_model_key` **하나**만 읽는다 (`app/deps.py`)
      - 그래서 코퍼스가 바뀔 때마다 **55분 × 3 ≈ 165분**을 쓰고 그중 110분은 소비자가 없다
      - `bge-m3`·`kure-v1` 은 2026-08-29 코퍼스(6,368행)에서 멈춰 있다

    실제 피해도 이미 났다 — RAG-045 가 *"방아쇠는 사소했다. `rag embed` 를 `--model` 없이
    돌려 `bge-m3.parquet` 이 생겼다"* 로 시작한다.

    3파전을 다시 돌릴 일이 생기면 `--all` 이 그 자리다.
    """
    if args.model:
        return [args.model]
    if getattr(args, "all_models", False):
        return list(embed.MODELS)
    return [config.settings.embedding_model_key]


def cmd_embed(args: argparse.Namespace) -> int:
    """chunks → embeddings/{key}.parquet. 모델 3종을 나란히 만든다 (RAG-002).

    **가드를 먼저 전부 돌린다.** 토크나이저는 가볍고 가중치 로드는 무거우니, 실패할 것이면
    6.5GB 를 올리기 전에 실패하는 편이 싸다.
    """
    rows = embed.load_chunks()
    if not rows:
        print("chunks 가 비었다 — `python -m rag chunk` 먼저")
        return 1
    keys = _target_models(args)
    unknown = [k for k in keys if k not in embed.MODELS]
    if unknown:
        print(f"모르는 모델: {unknown}   가능: {list(embed.MODELS)}")
        return 1

    fingerprint = embed.chunks_fingerprint()
    texts = [r["content"] for r in rows]
    print(f"청크 {len(rows)}건  chunks_sha256 {fingerprint[:16]}")

    if args.restamp:
        # RAG-025 ⑤ 의 일회성 대가 — 지문 *정의*가 바뀌어 기존 parquet 의 값이 옛 방식이다.
        # 벡터는 손대지 않고 메타만 갱신하되, 행이 실제로 일치할 때만 찍는다
        bad = 0
        for key in keys:
            ok, why = embed.restamp(key, fingerprint, rows)
            print(f"  {'restamped' if ok else 'REFUSED':11s} {key:22s} {why}")
            bad += not ok
        return 1 if bad else 0

    if args.backfill_hashes:
        # RAG-064 ② — 옛 parquet(v1)에 행별 해시를 재인코딩 없이 채운다. 전역 지문이 증명서다
        bad = 0
        for key in keys:
            ok, why = embed.backfill_hashes(key, fingerprint, rows)
            print(f"  {'filled' if ok else 'REFUSED':11s} {key:22s} {why}")
            bad += not ok
        return 1 if bad else 0

    todo: list[tuple[str, dict[str, int], embed.Plan | None]] = []
    for key in keys:
        model = embed.MODELS[key]
        # `--full` 은 **다시 만들라는 뜻**이므로 지문 스킵도 같이 넘긴다 (RAG-064).
        # 안 그러면 코퍼스가 그대로일 때 `--full` 이 조용히 아무것도 안 하는데,
        # 그것을 쓰는 자리가 하필 ④ 대조라 "전량을 만들었다"고 믿고 비교하게 된다.
        if embed.is_current(key, fingerprint) and not (args.force or args.full):
            print(f"  {'same':11s} {key}")
            continue

        # **증분 계획을 가드보다 먼저 세운다** (RAG-064). 가드는 전량 텍스트를 토큰화하는데,
        # 실제로 인코딩할 것이 37건이면 9,451건을 재는 것은 낭비다.
        plan = None if args.full else embed.plan_incremental(key, model, rows)
        if plan is not None and not plan.ok:
            print(f"  {'full':11s} {key:22s} 증분 거부 — {plan.refused}")
            plan = None
        elif plan is not None:
            print(f"  {'incremental':11s} {key:22s} 재사용 {len(plan.reuse):,} · "
                  f"인코딩 {len(plan.encode):,} · 사라짐 {plan.dropped:,}")

        # 가드는 **실제로 인코딩할 텍스트**에만 건다
        target = texts if plan is None else [rows[i]["content"] for i in plan.encode]
        try:
            stats = embed.guard(model, target) if target else {"max": 0, "median": 0, "p95": 0}
        except Exception as exc:
            print(f"  {'GUARD FAIL':11s} {key}\n      {exc}")
            return 1
        if target:
            pct = stats["max"] / model.max_tokens * 100
            print(f"  {'guard ok':11s} {key:22s} 최대 {stats['max']:5d} / 한계 {model.max_tokens} "
                  f"({pct:.0f}%)  중앙 {stats['median']}  p95 {stats['p95']}"
                  f"  ({len(target):,}건 대상)")
        todo.append((key, stats, plan))

    if args.guard_only:
        print("\n(guard-only: 인코딩하지 않음)")
        return 0

    for key, stats, plan in todo:
        model = embed.MODELS[key]
        target = texts if plan is None else [rows[i]["content"] for i in plan.encode]
        print(f"  {'encoding':11s} {key} ({model.repo}) {len(target):,}건 …", flush=True)
        st = embed.load_model(model) if target else None
        try:
            vectors = embed.encode_docs(model, target, batch_size=args.batch,
                                        st=st, progress=not args.quiet) if target else []
        finally:
            # **모델마다 GPU 에서 내린다.** PyTorch 는 파이썬 객체가 사라져도 empty_cache 전까지
            # VRAM 을 붙들고 있어, 3종을 한 프로세스에서 돌리면 누적된다. 6GB GPU 에서 마지막
            # 모델이 남은 공간에 끼여 10배 넘게 느려지는 것을 실측으로 겪었다
            del st
            embed.release()
        if args.dry_run:
            print(f"  {'(dry-run)':11s} {key:22s} 인코딩 {len(target):,}건")
            continue
        if plan is None:
            path = embed.write_parquet(model, rows, vectors,
                                       fingerprint=fingerprint, token_stats=stats)
        else:
            path = embed.write_parquet_incremental(model, rows, plan, vectors,
                                                   fingerprint=fingerprint, token_stats=stats)
        size = path.stat().st_size / 1e6
        how = "전량" if plan is None else f"증분({len(plan.encode):,}/{len(rows):,})"
        print(f"  {'written':11s} {key:22s} {how}  {len(rows):,}행  {size:.1f} MB  "
              f"VRAM {embed.vram_used_mb():.0f} MB  -> {path.name}", flush=True)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """검문소① 용. chunk_id 조각으로 찾아 청크 본문을 그대로 출력한다."""
    hit = 0
    for path in sorted(config.CHUNK_DIR.glob("*.jsonl")):
        for row in io.read_chunks(path):
            if args.pattern not in row["chunk_id"]:
                continue
            hit += 1
            print(f"--- {row['chunk_id']}  ({row['chars']}자)")
            print(f"    citation: {row.get('citation')}   section: {row.get('section')}")
            if row.get("part"):
                print(f"    part: {row['part']}")
            print(row["content"] if args.full else row["content"][:600])
            print()
            if hit >= args.limit:
                print(f"(상한 {args.limit}건에서 멈춤)")
                return 0
    if not hit:
        print("일치하는 청크가 없다")
        return 1
    return 0


def _expect(item) -> str:
    """`expect` 가 기본(`answer`)이 아닌 문항만 화면에 표시한다 (RAG-055).

    기본값을 안 찍는 것은 30문항 중 27개가 `answer` 라서다 — 전부 찍으면 **다른 셋이 묻힌다.**
    """
    if item.expect == "answer":
        return ""
    code = f" ({item.refusal_code})" if item.refusal_code else ""
    return f"   → 기대: {item.expect}{code}"


def cmd_goldenset(args: argparse.Namespace) -> int:
    """골든셋 라벨이 실제 청크를 가리키는지 검사한다 (RAG-022 ⑥).

    없는 주소를 가리키는 must 는 그 문항의 Recall 을 영원히 0 으로 만들고, 증상은 6단계에서
    "이 모델이 유독 못한다" 로만 나타난다. 채점 전에 여기서 먼저 깨뜨린다.
    """
    gs = goldenset.load()
    index = goldenset.corpus_index()
    problems, warnings = goldenset.verify(gs, index)

    origin = collections.Counter(i.origin for i in gs.items)
    expect = collections.Counter(i.expect for i in gs.items)
    addresses = sum(len(i.must_flat) for i in gs.items)
    print(f"골든셋 {len(gs.items)}문항 (hand {origin['hand']} · easylaw {origin['easylaw']})  "
          f"필수 {gs.must_total}요구/{addresses}주소  보강 {sum(len(i.nice) for i in gs.items)}  "
          f"분모 제외 {sum(len(i.unavailable) for i in gs.items)}")
    # **요구와 주소를 갈라 찍는다** (RAG-055). 한 요구 안의 대안을 늘리면 주소만 늘고 요구는
    # 그대로여야 하는데, 한 수만 찍으면 그 불변식이 화면에서 안 보인다
    print(f"기대  답변 {expect['answer']}  ·  기권 {expect['abstain']}  ·  거절 {expect['refuse']}")
    print(f"코퍼스 {len(index)}청크  ·  라벨 기준 {gs.corpus.collected_on}")

    if args.verbose:
        for item in gs.items:
            print(f"\n  [{item.id}] ({item.origin}) {item.question}{_expect(item)}")
            for n, group in enumerate(item.must, 1):
                # **요구 번호를 찍는다** (RAG-055) — 안 찍으면 "대안이 둘"과 "요구가 둘"이
                # 화면에서 똑같아 보이고, 그 둘은 Recall 분모가 다르다
                for j, a in enumerate(group):
                    tier = f"must{n}" if j == 0 else "또는"
                    print(f"    {tier:>6s} {'OK  ' if a in index else '없음 '}{a}")
            for a in item.nice:
                print(f"    {'nice':>6s} {'OK  ' if a in index else '없음 '}{a}")
            for u in item.unavailable:
                print(f"    ----      {u.ref}  ({u.reason})")

    for w in warnings:
        print(f"\n  경고: {w}")
    if problems:
        print(f"\n  라벨 {len(problems)}개가 실재하지 않는 청크를 가리킨다:")
        for p in problems:
            print(f"    {p.item_id:4s} {p.tier:4s} {p.address}")
        return 1
    print("\n  라벨 전부 실재한다")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """6단계 3파전 — 채점하고 승자를 고른다 (RAG-024).

    **골든셋 검증을 먼저 통과해야 채점을 시작한다.** 없는 주소를 가리키는 must 는 그 문항의
    점수를 영원히 0 으로 만들고, 증상은 "이 모델이 유독 못한다" 로만 나타난다 (RAG-022 ⑥).
    같은 이유로 **낡은 parquet 으로는 채점하지 않는다** — 다른 코퍼스를 잰 값이기 때문이다.
    """
    gs = goldenset.load()
    index = goldenset.corpus_index()
    problems, warnings = goldenset.verify(gs, index)
    if problems:
        print(f"골든셋 라벨 {len(problems)}개가 실재하지 않는 청크를 가리킨다 — "
              "`python -m rag goldenset` 으로 먼저 고칠 것")
        return 1
    for w in warnings:
        print(f"  경고: {w}")

    keys = _target_models(args)
    unknown = [k for k in keys if k not in embed.MODELS]
    if unknown:
        print(f"모르는 모델: {unknown}   가능: {list(embed.MODELS)}")
        return 1

    fingerprint = embed.chunks_fingerprint()
    print(f"골든셋 {len(gs.items)}문항 / 필수 {gs.must_total}  ·  코퍼스 {len(index)}청크  ·  "
          f"chunks_sha256 {fingerprint[:16]}")
    print(f"판정 k={evaluate.JUDGE_K}  ·  탈락선 Hit@{evaluate.JUDGE_K} {evaluate.CUT_GAP}문항 차  ·  "
          f"동률이면 사전 순위 {list(evaluate.PREFERENCE)} (RAG-024 ①③)")

    summaries: dict[str, dict] = {}
    for key in keys:
        if not embed.is_current(key, fingerprint) and not args.force:
            print(f"  {'STALE':11s} {key} — parquet 이 지금 청크와 다른 코퍼스를 잰 것이다. "
                  "`python -m rag embed` 먼저 (또는 --force)")
            return 1
        print(f"  {'scoring':11s} {key} …", flush=True)
        res = evaluate.score_model(key, gs, index)
        if res is None:
            print(f"  {'MISSING':11s} {key}.parquet 이 없다 — `python -m rag embed` 먼저")
            return 1
        items, summary = res
        summaries[key] = summary
        if not args.dry_run:
            path = evaluate.write_dump(key, items, gs, fingerprint)
            print(f"  {'dumped':11s} {key:22s} -> {path.name}  (미추적, RAG-024 ④)")
        if args.verbose:
            k = evaluate.JUDGE_K
            for it in items:
                # 요구마다 **가장 좋은 대안**의 순위다 (RAG-055). `-` 는 top 밖이거나 코퍼스 밖
                ranks = ", ".join(str(g["rank"]) if g["rank"] is not None else "-"
                                  for g in it.must)
                print(f"      [{it.item_id:4s}] hit@{k}={int(it.hit[k])} "
                      f"recall@{k}={it.recall[k]:.2f}  필수 순위 [{ranks}]  {it.question}")

    if len(summaries) < len(embed.MODELS):
        print("\n(모델 3종이 다 있어야 판정한다 — 지금은 점수만 냈다)")
        return 0

    verdict = evaluate.judge({k: s["hit_count"] for k, s in summaries.items()})
    print()
    print(evaluate.markdown(summaries, verdict, gs, fingerprint, len(index)))
    print()
    print("  ^ 위 markdown 을 docs/life/decisions-rag.md 의 RAG-024 에 `### 판정 결과` 로 붙인다 (RAG-024 ④).")
    print("    덤프는 미추적이라 이것이 뒤에 남는 전부다.")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """chunks + embeddings → documents (RAG-008, RAG-025).

    **행을 먼저 다 만들고 나서 DB 를 연다.** 벡터 정렬이나 중복 처리에서 실패할 것이면
    연결하기 전에 실패하는 편이 싸고, `--dry-run` 이 같은 경로를 그대로 탄다.
    """
    key = args.model or config.settings.embedding_model_key
    if key not in embed.MODELS:
        print(f"모르는 모델: {key}   가능: {list(embed.MODELS)}")
        return 1

    try:
        prepared = loader.prepare(key)
    except (FileNotFoundError, ValueError) as exc:
        print(f"  {'FAIL':11s} {exc}")
        return 1

    print(f"모델 {key} ({prepared.model_repo})")
    print(f"청크 {len(prepared.rows) + prepared.merged}건 → 행 {len(prepared.rows)}개"
          f"  (content 중복 {prepared.merged}건 합침 — metadata.merged_from 에 남는다)")
    if key != "qwen3-embedding-0.6b":
        # 판정 승자가 아닌 것으로 적재하는 것은 결정이지 사고가 아니다. 다만 조용하면 안 된다
        print("  * 판정 승자는 qwen3-embedding-0.6b 다 — 첫 관통을 기준선으로 가는 중"
              " (RAG-024 `판정 이후`)")

    if args.dry_run:
        for row in prepared.rows[:args.show]:
            print(f"    {row['metadata']['chunk_id']:60s} {row['content_hash'][:12]} "
                  f"{row['category']}/{row['subcategory']}")
        merged = [r for r in prepared.rows if r["metadata"]["merged_from"]]
        for row in merged:
            print(f"    합침: {row['metadata']['chunk_id']}"
                  f"  <- {row['metadata']['merged_from']}")
        print("\n(dry-run: DB 를 열지 않았다)")
        return 0

    with loader.connect() as conn:
        before = loader.existing_models(conn)
        for name, n in before:
            mark = "  " if name == prepared.model_repo else "! "
            print(f"  {mark}기존 {n:5d}행  {name}")
        if any(name not in (prepared.model_repo, "(없음)") for name, _ in before):
            print("  ! 다른 모델의 행이 있다 — upsert 가 같은 content_hash 를 덮어쓴다 (RAG-025 ①)")

        # **DB 에 있는 메타 키가 이번 적재로 통째로 사라지는가** (RAG-066 ①).
        # upsert 는 metadata 를 병합이 아니라 갈아끼우므로, 청크에 없는 키는 그냥 없어진다.
        # 실제로 `org` 2,592행이 그렇게 지워졌고 **아무 에러도 안 났다.**
        if losing := loader.metadata_loss(conn, prepared.rows):
            print("  ! 이번 적재가 DB 의 메타 키를 통째로 지운다:")
            for key, in_db, incoming in losing:
                print(f"      {key:24s} DB {in_db:6,d}행  →  이번 {incoming}행")
            print("    청크 파일이 낡았을 수 있다 — `rag chunk` 를 먼저 돌려 보세요"
                  " (청커 판이 올라갔으면 다시 만든다).")
            print("    마이그레이션으로만 넣은 값이면 **코퍼스가 원천이 되도록** 파서·청커에"
                  " 실어야 합니다.")
            if not args.allow_metadata_loss:
                print("    정말 지우려면 --allow-metadata-loss 를 붙이세요. 적재를 멈춥니다.")
                return 1
            print("    --allow-metadata-loss 가 있어 그대로 진행합니다.")

        loader.upsert(conn, prepared.rows)
        total = loader.count(conn)
        print(f"  {'upserted':11s} {len(prepared.rows)}행  ·  documents 총 {total}행")

        # 이번 적재가 안 건드린 행 = 사라진 청크 (RAG-045 ①). upsert 는 지우지 않는다.
        left = loader.stale(conn, prepared.rows)
        if left:
            print(f"  ! {'stale':9s} {len(left)}행이 이번 적재에 없다 — 개정으로 사라진 청크다.")
            for _, cid in left[:args.show]:
                print(f"      {cid}")
            if len(left) > args.show:
                print(f"      … {len(left) - args.show}행 더")
            if args.prune:
                print(f"  {'pruned':11s} {loader.prune(conn, left)}행 삭제")
                print(f"  {'':11s} documents 총 {loader.count(conn)}행")
            else:
                print("    검색이 이것들을 계속 후보로 본다. 지우려면 --prune 를 붙여 다시 실행할 것")
        else:
            print(f"  {'stale':11s} 없음 — DB 가 코퍼스와 일치한다")

        for name, n in loader.existing_models(conn):
            print(f"    {n:5d}행  {name}")

    print("\n인덱스는 적재 후에 수동으로 만든다 (db/indexes.sql):")
    print("  docker compose exec -T pgvector psql -U postgres -d vectordb < db/indexes.sql")
    return 0


def _print_hits(hits, must=frozenset(), nice=frozenset(), width: int = 150) -> None:
    """검문소③이 눈으로 보는 화면. **`chunk_id` 를 항상 찍는다** — 골든셋 라벨과 같은 주소라
    "이게 정답 청크인가"를 대조할 수 있다 (RAG-026 ②)."""
    if not hits:
        print("      (결과 없음)")
        return
    for h in hits:
        tier = searcher.tier_of(h.chunk_id, set(must), set(nice))
        mark = {"must": "★", "nice": "·"}.get(tier, " ")
        sup = "  [부칙]" if h.part == "supplementary" else ""
        # 인용 확장으로 딸려 온 줄임을 표시한다 (RAG-036). 안 찍으면 두 축이 모두 비어 있는
        # 히트가 왜 top-k 뒤에 붙어 있는지 검문소③이 알 수 없다
        via = f"  ← 인용 확장 ({h.cited_by})" if h.cited_by else ""
        print(f"  {mark} {h.rank}. {h.score:.4f}  {h.citation or h.document_title}{sup}{via}")
        body = " ".join(h.content.split())
        print(f"        {body[:width]}{'…' if len(body) > width else ''}")
        print(f"        {h.chunk_id}")


def cmd_search(args: argparse.Namespace) -> int:
    """8단계 dense 검색 (RAG-026). **검색 자체는 `stages.search` 가 하고 여기는 출력만 한다.**

    RAG-023 이 parse 에서 정리한 모양 그대로이고, 이유는 9단계·FastAPI 가 같은 함수를 부르게
    하기 위해서다 — 검색을 두 번 짜면 검문소③이 확인한 것과 서빙이 하는 것이 달라진다.
    """
    key = args.model or config.settings.embedding_model_key
    if key not in embed.MODELS:
        print(f"모르는 모델: {key}   가능: {list(embed.MODELS)}")
        return 1
    if not args.questions and not args.query:
        print("질의를 주거나 --questions 를 쓸 것")
        return 1

    if args.questions:
        items = searcher.hand_questions()
    else:
        items = [("", " ".join(args.query), set(), set())]

    label = "부칙 포함" if args.supplementary else "부칙 제외"
    print(f"모델 {key} ({embed.MODELS[key].repo})  ·  top-{args.k}  ·  {label}")
    if key != "qwen3-embedding-0.6b":
        print("  * 판정 승자는 qwen3-embedding-0.6b 다 — 첫 관통을 기준선으로 가는 중"
              " (RAG-024 `판정 이후`)")

    # 모델을 한 번만 올린다. 질의 7개마다 6.5GB 를 올렸다 내리는 것은 낭비다
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        # 벡터를 여기서 만들지만 **토큰화는 searcher 가 한다** — 문서 쪽과 같은 함수를
        # 쓰게 하려는 것이고, 그래서 `make_query` 를 거친다 (RAG-035)
        vectors = [(qid, q, must, nice,
                    searcher.encode(q, model_key=key, st=st))
                   for qid, q, must, nice in items]
    finally:
        del st
        embed.release()

    found = 0
    with loader.connect() as conn:
        for qid, q, must, nice, vec in vectors:
            head = f"[{qid}] " if qid else ""
            print(f"\n{head}{q}")
            if must:
                print(f"      필수 {len(must)}개: {', '.join(sorted(must))}")
            if excluded := transport.exclusions(q):
                # 검문소③이 "왜 항공이 안 보이나"를 눈으로 알 수 있게 (RAG-052)
                print(f"      교통수단 {'/'.join(sorted(transport.modes(q)))} → {', '.join(excluded)} 배제")
            if added := vocabulary.aliases(q):
                # 검문소③이 "왜 이게 올라왔나"를 눈으로 알 수 있게 (RAG-066). 위 두 줄과 같은 자리다 —
                # **신호가 켜졌는지 안 켜졌는지가 화면에 안 보이면 오탐을 영영 못 찾는다.**
                print(f"      어휘 확장 → {', '.join(added)}")
            hits = searcher.search(vec, k=args.k, conn=conn,
                                   include_supplementary=args.supplementary,
                                   category=args.category)
            _print_hits(hits, must, nice, width=args.width)
            found += sum(1 for h in hits
                         if searcher.tier_of(h.chunk_id, must, nice) == "must")

    if args.questions:
        # 검문소③은 눈으로 보는 것이지만, 세어 두면 부칙 필터를 켜고 끄며 비교할 수 있다
        print(f"\n★=필수 정답 · ·=보강.  top-{args.k} 안의 필수 {found}개"
              f" / {len(items)}문항 ({label})")
    return 0



def _warn_if_dump_lands_outside_checkout(path: Path) -> None:
    """랩 덤프가 **지금 돌고 있는 코드의 체크아웃 밖**에 떨어졌으면 알린다.

    랩 덤프는 커밋해야 하는 파일이다 (`core.config.ANSWER_DIR` 주석). 그런데 워크트리에서
    작업하면 `backend/.env` 의 `DAENGS_DATA_DIR` 이 **메인 체크아웃을 가리키도록 고정**돼 있어
    (CLAUDE.md "워크트리에서 작업해도 `data/` 는 한 곳에 쌓으세요" — 코퍼스가 갈라지지 않게 한
    설정이다) 덤프는 작업 중인 워크트리가 아닌 곳에 떨어진다. 그러면 그 워크트리의 `git status`
    는 깨끗하고, **파일이 있는 줄도 모른 채** 카드가 머지된다.

    실제로 세 번 났다 — `4db761e`(lap15~18, #177 뒤처리) · `d454da5`(lap20) · lap22(#240).
    셋 다 나중에 다른 사람이 발견해 뒤처리 커밋을 따로 만들었다. 규칙을 하나 더 쓰는 대신
    **파일을 쓴 그 자리에서** 알리는 이유는, 이것이 규칙을 몰라서가 아니라 파일이 안 보여서
    생긴 사고이기 때문이다.

    체크아웃 루트는 이 파일 위치에서 잡는다 (`backend/src/daengs_life/rag/__main__.py` → 5단계 위).
    **이 파일을 옮기면 `parents[4]` 도 같이 고쳐야 한다.**
    """
    checkout = Path(__file__).resolve().parents[4]
    try:
        path.resolve().relative_to(checkout)
    except ValueError:
        print(f"⚠️ 이 덤프는 커밋 대상인데 지금 체크아웃({checkout}) 밖에 떨어졌다 —")
        print("   `DAENGS_DATA_DIR` 이 다른 곳을 가리킨다. 여기서 `git add` 해도 안 잡힌다.")
        print(f"   파일이 있는 체크아웃에서 담을 것:  git -C {path.resolve().parent} add {path.name}")
    else:
        print("   커밋 대상이다 — 이 카드에 같이 담을 것 (`.gitignore` 예외, RAG-017)")


def cmd_generate(args: argparse.Namespace) -> int:
    """9단계 — 검색 위에 Gemini 로 답을 만든다 (RAG-028). **조립은 `stages.generate` 가 하고
    여기는 출력과 덤프만 한다** (RAG-023 이 parse 에서 정리한 모양 그대로).

    `--questions` 가 **검문소④**다. 판정 문항을 사후에 만들지 않으려고 미리 못 박아 뒀다
    (RAG-024 ①의 사전 등록과 같은 장치).

    지표는 둘을 나란히 찍는다 — **`cited`/`ungrounded`**(답변에 등장한 조항 번호가 컨텍스트에
    실재하는가, 1랩부터 쓴 것)와 **`grounded`**(답변이 `[N]` 으로 지목한 근거가 골든셋 `must`
    를 가리키는가, RAG-029). 앞의 것은 지어냈는지만 보고 정답인지는 안 본다 — 그래서 물러선
    답변이 무관한 조항을 나열해도 '인용'으로 셌다. `grounded` 가 그것을 고친다.
    """
    key = args.model or config.settings.embedding_model_key
    if key not in embed.MODELS:
        print(f"모르는 모델: {key}   가능: {list(embed.MODELS)}")
        return 1
    if not args.questions and not args.query:
        print("질의를 주거나 --questions 를 쓸 것")
        return 1

    items = searcher.hand_questions() if args.questions else [("", " ".join(args.query), set(), set())]

    # 문항별 반려견 프로필 (RAG-056). **`hand_questions()` 의 튜플을 안 늘린다** — 그 모양을
    # 네 곳이 풀어 쓰고 있어서, 칸 하나 때문에 전부 고치면 이 카드가 건드릴 이유가 없는
    # 자리까지 diff 에 들어온다. 여기서 id 로 한 번 더 읽는 편이 싸다.
    profiles: dict[str, generator.DogProfile] = {}
    if args.questions:
        from .stages import goldenset as _gs
        profiles = {i.id: generator.DogProfile(breed=i.dog.breed, age_months=i.dog.age_months)
                    for i in _gs.load().items if i.dog is not None}
        if profiles:
            print(f"반려견 프로필이 붙은 문항 {len(profiles)}개: {', '.join(sorted(profiles))}")

    print(f"임베딩 {key}  ·  Gemini {config.settings.gemini_model}  ·  top-{args.k}")

    # 모델·커넥션·클라이언트를 **여기서 만들어 넘긴다** — RAG-028 ①의 수명 규약이다. 질의 7개마다
    # 6.5GB 를 올렸다 내릴 이유가 없고, 서버에서는 같은 자리에 lifespan 이 올린 것이 들어온다.
    try:
        client = generator._client()
    except RuntimeError as e:
        print(f"  {e}")
        return 1

    model = embed.MODELS[key]
    st = embed.load_model(model)
    answers: list[tuple[str, generator.Answer, set, set]] = []
    try:
        with loader.connect() as conn:
            for qid, q, must, nice in items:
                a = generator.ask(q, k=args.k, include_supplementary=args.supplementary,
                                  category=args.category, model_key=key,
                                  st=st, conn=conn, client=client, dog=profiles.get(qid))
                answers.append((qid or "-", a, must, nice))

                print()
                print("=" * 70)
                print(f"{('[' + qid + '] ') if qid else ''}{q}")
                print()
                print(a.text)
                print()
                print(f"  근거 top-{args.k}:")
                _print_hits(a.hits, must, nice, width=args.width)
                print()
                if not a.cited:
                    print("  인용한 조항: (없음)")
                else:
                    print(f"  인용한 조항: {', '.join(a.cited)}")
                    if a.ungrounded:
                        print(f"  ⚠️ 컨텍스트에 없음: {', '.join(a.ungrounded)}")
                    else:
                        print("  근거 안에 전부 있음")
                # RAG-029 — 조 번호가 아니라 **문서**를 본다. must 가 없는 자유 질의에서는
                # 늘 False 라 문항이 라벨을 가질 때만 찍는다 (골든셋 없인 잴 수 없다)
                if must:
                    grounded = scorer.grounds_the_answer(a.text, a.hits, must)
                    print(f"  근거로 정답을 인용함: {'예' if grounded else '아니오'} (RAG-029)")

            if not args.dry_run:
                header = generator.dump_header(args.lap, answers, args.k, conn=conn)
                path = io.write_answers(header, generator.dump_rows(answers), stem=args.lap)
                print()
                print(f"덤프 → {path.resolve()}")
                _warn_if_dump_lands_outside_checkout(path)
    finally:
        del st
        embed.release()

    if args.questions:
        # 검문소④는 **문항 수로 센다.** RAG-024 ③ 이 Hit 을 문항 균등으로 읽은 것과 같은 이유다 —
        # 조항을 많이 인용한 문항 하나가 점수를 지배하면 안 된다. 조항 총수는 참고로만 찍는다.
        bad = sum(1 for _, a, _, _ in answers if a.ungrounded)
        total = sum(len(a.ungrounded) for _, a, _, _ in answers)
        cited = sum(1 for _, a, _, _ in answers if a.cited)
        # RAG-029 — must 가 없는 자유 질의는 분모에서 뺀다. 그 문항은 애초에 잴 수 없다
        labeled = [(a, must) for _, a, must, _ in answers if must]
        grounded = sum(1 for a, must in labeled if scorer.grounds_the_answer(a.text, a.hits, must))
        print()
        print("=" * 70)
        print(f"검문소④  조항을 인용한 문항 {cited}/{len(answers)} (1랩부터 쓴 지표)  ·  "
              f"컨텍스트에 없는 조항을 든 문항 {bad}/{len(answers)} (조항 수 {total})")
        if labeled:
            print(f"  근거로 정답을 인용한 문항 {grounded}/{len(labeled)} (RAG-029 — 문서까지 대조한다)")
        print("  RAG-029 가 위 두 지표가 서는 자리와 새는 자리를 기록해 뒀다."
              " `python -m rag score-laps` 로 저장된 랩을 소급 비교할 수 있다.")
    return 0


def cmd_score_laps(args: argparse.Namespace) -> int:
    """저장된 모든 랩(`lap1`~)을 새 지표(`grounded`, RAG-029)로 소급 채점한다.

    **DB 도 임베딩도 필요 없다** — 총계 표는 `data/processed/answers/*.jsonl` 만 읽는다.
    `hits[].tier` 가 적재 시점에 이미 골든셋과 대조돼 저장돼 있어서다. 그래서 이 도구는
    2026-08-28 이전에 만들어진 `lap1`~`lap3`(7문항) · `lap4`~`lap6`(12문항)도 그대로 재본다.

    새 소스 카드(`#50` 펫보험 · `#51` 운송약관)가 새 랩을 뜨면 이 표에 한 줄이 늘어난다 —
    문항 수·`must` 라벨이 달라도 상관없다. 표가 랩마다 자기 문항 수로 나눈 비율이라서다.

    ⚠ **종류별 슬라이스(RAG-060)만은 `chunks/` 와 골든셋을 읽는다.** 그래서 없으면 그 표만
    건너뛰고 한 줄로 알린다 — 총계 표는 그대로 나온다. 위의 "코퍼스 없이 돈다"는 약속을 슬라이스
    하나 때문에 깨지 않기 위해서다.
    """
    paths = io.answer_files()
    if not paths:
        print("data/processed/answers 에 랩이 없다 — `rag generate --questions --lap <이름>` 로 먼저 만들 것")
        return 1

    # 경계 문항을 총계에서 빼려면 **골든셋만** 있으면 된다 (RAG-062) — `must` 가 있나 없나가
    # 판정의 전부라서다. 청크(`chunks/`)를 요구하는 종류별 슬라이스와 달리, 골든셋은 패키지에
    # 같이 실려 있으므로 "랩 파일만 있으면 돈다"는 이 표의 약속이 안 깨진다. 그래도 못 읽는
    # 경우가 있으면(옛 체크아웃 등) **표를 막지 않고 옛 셈으로 떨어진다.**
    try:
        gs = goldenset.load()
        ckinds = scorer.citable_kinds({i.id: i.must for i in gs.items})
    except Exception as exc:  # noqa: BLE001 - 표를 막는 것보다 옛 셈으로라도 내는 쪽이 낫다
        ckinds = None
        print(f"골든셋을 못 읽어 경계 문항을 못 가린다({exc}) — 옛 셈(경계 포함)으로 낸다\n")

    print(f"{'랩':6} {'문항':>4} {'경계':>4} {'채점':>4}   {'cited':>9}   {'grounded':>9}"
          f"   {'조 번호 있음':>14}   {'조 번호 없음':>14}   {'옛 표기':>13}")
    print("-" * 108)
    laps = []
    for path in paths:
        header, rows = io.read_answers(path)
        s = scorer.score_rows(rows, ckinds)
        n, k = s["n"], s["scored"]
        if not n:
            continue
        laps.append((path.stem, rows))
        old = scorer.score_rows(rows)  # 소급 대조 — 옛 기록이 인용하는 수를 그대로 다시 낸다
        cells = []
        for kind in (scorer.CITABLE, scorer.UNCITABLE):
            if f"{kind}_n" not in s:
                cells.append(f"{'—':>14}")
                continue
            cells.append(f"{s[f'{kind}_cited']}/{s[f'{kind}_grounded']}·{s[f'{kind}_n']:<2}".rjust(14))
        print(f"{path.stem:6} {n:>4} {s['boundary']:>4} {k:>4}   {s['cited']:>5}/{k:<3}"
              f"   {s['grounded']:>5}/{k:<3}   {cells[0]}   {cells[1]}"
              f"   {old['cited']:>4}·{old['grounded']}/{n:<4}")

    if ckinds is not None:
        print("\n  **경계 문항(`expect: abstain`·`refuse`)은 `cited`/`grounded` 에서 뺐다** (RAG-062) —"
              " `must` 가 없어 잴 근거가 없다.")
        print("  거절을 옳게 한 답이 거절문에 문 조 번호로 `cited` 에 잡히고, 놓친 기권이 성공으로 세지던 자리다.")
        print("  `옛 표기` 는 그 둘을 포함한 예전 수다 — RAG-029 이후 기록들이 인용하는 값이 이 열에 있다.")
        print("  칸 하나가 `cited/grounded·문항수` 다. **조 번호 축은 골든셋 `must` 앵커로 정한다** —"
              " 코퍼스를 안 보므로 랩마다 흔들리지 않는다.")
        print("  두 축의 문항수를 더한 것이 `채점` 보다 작으면, 그 차이는 **골든셋에서 빠진 옛 문항**이다"
              " (`lap7-age` 처럼). 총계에서는 빼지 않는다 — 뺄지 모르는 것과 빼야 하는 것은 다르다.")

    _print_kpi(laps, ckinds)
    _print_kind_table(laps, getattr(args, "by", "trust_level"), getattr(args, "laps", 6))
    _print_expect_table(laps)
    return 0


def kpi_cells(rows: list[dict], ckinds: dict[str, str]) -> dict[str, tuple[int, int, int]]:
    """KPI 를 **두 축으로 갈라** 낸다 — `{축: (cited, grounded, 문항수)}` (RAG-070 ③).

    KPI 문장은 「답변에 출처 링크 + **조항 번호** 인용」인데 총계 `cited` 는 두 축을 한 수에
    섞는다. 조 번호가 **문서에 아예 없는** 소스(보조금24 · knia 공시 · 항공사 안내 · SRT 약관 ·
    easylaw/nias 해설)는 `cited` 가 영영 0이고, 그것은 실패가 아니라 **그 소스의 성질**이다 —
    그 문항들도 근거는 옳게 잡는다. `lap29` 실측으로 그 칸이 **13문항 중 grounded 13** 이다.

    ⚠ **총계 칸은 안 건드린다.** `D10`(RAG-062)이 경계 문항을, `D12`(RAG-069)가 근거 표기를
    소급으로 두 번 바꿨다. 세 번째면 옛 기록이 인용하는 대조선이 또 끊긴다 — 여기서는
    **읽는 법을 더할 뿐** 수를 다시 쓰지 않는다. 축을 가르는 데 쓰는 값은 `D10` 이 이미
    표에 넣어 둔 `조 번호 있음`·`조 번호 없음` 그대로다.
    """
    s = scorer.score_rows(rows, ckinds)
    cells = {}
    for kind in (scorer.CITABLE, scorer.UNCITABLE):
        if f"{kind}_n" in s:
            cells[kind] = (s[f"{kind}_cited"], s[f"{kind}_grounded"], s[f"{kind}_n"])
    return cells


def _print_kpi(laps: list[tuple[str, list[dict]]], ckinds: dict[str, str] | None) -> None:
    """최신 랩 하나를 KPI 문장 그대로 읽어 준다. 발표 자료가 쓰는 수가 이것이다."""
    if ckinds is None or not laps:
        return
    stem, rows = max(laps, key=lambda lap: _lap_key(lap[0]))
    cells = kpi_cells(rows, ckinds)
    if not cells:
        return
    print(f"\n  KPI 「답변에 출처 링크 + 조항 번호 인용」 — 최신 랩 `{stem}`")
    labels = {scorer.CITABLE: "조 번호가 있는 소스", scorer.UNCITABLE: "조 번호가 없는 소스"}
    for kind, (cited, grounded, n) in cells.items():
        print(f"    {labels[kind]:22} 조 번호 인용 {cited:>3}/{n:<3}({cited * 100 // n:>3}%)"
              f"   출처 근거 {grounded:>3}/{n:<3}({grounded * 100 // n:>3}%)")
    print("    조 번호가 없는 소스는 **문서에 조 번호가 없어서** 인용 칸이 0이다 —"
          " 실패가 아니라 그 소스의 성질이고, 근거 칸으로 읽는다 (RAG-070 ③).")


def _print_kind_table(laps: list[tuple[str, list[dict]]], field: str = "trust_level",
                      shown: int = 6) -> int:
    """종류별 슬라이스 — 총계 하나로는 노이즈와 퇴보가 안 갈린다 (RAG-060).

    문항을 **골든셋 `must` 라벨이 어느 종류 문서에 있는가**로 귀속한다. 랩이 실제로 잡은 히트로
    귀속하지 않는 이유는 `scorer` 모듈 머리말에 있다 — 그러면 분모가 랩마다 흔들린다.

    **축은 고를 수 있다** (`--by`). 축을 코드에 박지 않는 이유는 이 카드가 실제로 겪은 것이다 —
    `trust_level` 로는 RAG-046 ⑦ 의 주장이 안 보이는데 `source_id` 로는 보인다. 어느 축이
    맞는지는 미리 알 수 없고, 알아보는 것이 이 표의 일이다.

    `chunks/` 가 없으면 종류를 알 길이 없다. 그때는 **총계 표를 막지 않고 이 표만 건너뛴다** —
    `score-laps` 는 랩 파일만 있으면 도는 도구이고, 그 약속이 옛 랩을 재보는 근거다.
    """
    chunk_paths = io.chunk_files()
    if not chunk_paths:
        print(f"\n종류별 슬라이스(RAG-060): `data/processed/chunks/` 가 비어 있어 건너뛴다 —"
              f" 종류는 청크 행의 `{field}` 에서 파생된다")
        return 0

    kinds = scorer.corpus_kinds(
        (row for path in chunk_paths for row in io.read_chunks(path)), field)
    gs = goldenset.load()
    qkinds = scorer.question_kinds({i.id: i.must for i in gs.items}, kinds)

    spread = collections.Counter(qkinds.values())
    print(f"\n종류별 슬라이스 (RAG-060) — 청크 {len(kinds):,}개의 `{field}` 에서 파생."
          f" 골든셋 {len(qkinds)}문항: "
          + " · ".join(f"{k} {n}" for k, n in sorted(spread.items(), key=lambda kv: scorer.kind_order(kv[0]))))
    print("  문항은 **정답이 있는 문서의 종류**로 앉는다 — 랩이 무엇을 찾았는지와 무관하게 고정이다")

    # **종류가 줄, 랩이 칸이다.** `--by source_id` 는 종류가 15개까지 가고, 그것을 가로로 늘어놓으면
    # 한 줄이 화면을 넘어가 아무도 안 읽는다. 종류 수는 축마다 다르지만 **읽는 방향은 늘 같다** —
    # "이 종류가 랩을 지나며 움직였나" 라서, 종류를 세로로 두는 쪽이 그 물음과 모양이 같다.
    recent = sorted(laps, key=lambda lap: _lap_key(lap[0]))[-shown:] if shown else \
        sorted(laps, key=lambda lap: _lap_key(lap[0]))
    sliced = {stem: scorer.slice_rows(rows, qkinds) for stem, rows in recent}
    rows_order = sorted({k for cells in sliced.values() for k in cells}, key=scorer.kind_order)

    # 칸 하나가 `cited/grounded·문항수` 다. 비율을 미리 나누지 않는 이유는 분모가 종류마다 다르고
    # (한 자리 수인 칸이 흔하다) 퍼센트로 적으면 1문항이 움직인 것이 크게 보이기 때문이다.
    width = max((len(k) for k in rows_order), default=8)
    print(f"\n  {'종류':{width}}" + "".join(f"   {stem:>16}" for stem, _ in recent))
    print(f"  {'':{width}}" + "".join(f"   {'cited/grounded·n':>16}" for _ in recent))
    print("  " + "-" * (width + 19 * len(recent)))
    for kind in rows_order:
        cells = []
        for stem, _ in recent:
            cell = sliced[stem].get(kind)
            text = "-" if cell is None else f"{cell['cited']}/{cell['grounded']}·{cell['n']}"
            cells.append(f"   {text:>16}")
        print(f"  {kind:{width}}" + "".join(cells))

    if field == "trust_level":
        # 기본 축은 짧아서 "뭔가 움직였나"를 훑기에 좋지만, **왜 움직였나는 여기서 안 보인다** —
        # `official` 이 조례(조 번호 있음)와 보조금24(없음)를 한 칸에 넣기 때문이다 (RAG-060 ②).
        print("  ↪ `--by source_id` 로 다시 보면 눌림(grounded > cited)과 부풀림(cited > grounded)이"
              " 갈린다 — 이 축은 그 둘을 한 칸에 넣는다 (RAG-060 ②·③)")
    return 0


def _lap_key(stem: str) -> tuple[int, str]:
    """`lap10` 이 `lap2` 뒤에 오게. 파일명 정렬로는 `lap10` < `lap2` 라 "최근 N개"가 엉킨다.

    ⚠ **위의 총계 표는 여전히 파일명 순이다** — `io.answer_files()` 가 그 순서를 돌려주고,
    RAG-029 이후의 기록들이 그 표를 그 순서로 인용한다. 여기서만 다시 세우고 저쪽은 안 건드린다.
    """
    head = stem.split("-", 1)[0]
    return (int(head[3:]), stem) if head[3:].isdigit() else (0, stem)


def _print_expect_table(laps: list[tuple[str, list[dict]]]) -> int:
    """`expect` 채점 — "답했나 말았나" (RAG-055).

    **정책을 하나 고르지 않고 나란히 찍는다.** 카드 #177 이 *"골든셋으로 잰 뒤 고른다 — 착수 전
    결정 금지"* 라고 못 박은 자리라, 이 표가 그 결정의 근거다. `none` 이 지금 서빙이 하는 것이고
    나머지가 후보다.

    두 방향을 갈라 찍는 이유는 한 수로 합치면 정반대의 정책이 같은 점수를 받기 때문이다 —
    아무것도 기권 안 하는 정책과 전부 기권하는 정책이 그렇다.
    """
    gs = goldenset.load()
    expects = {i.id: i.expect for i in gs.items}
    codes = {i.id: i.refusal_code for i in gs.items if i.refusal_code}
    boundary = {i.id for i in gs.items if i.expect != "answer"}
    scored = [(stem, rows) for stem, rows in laps
              if boundary & {r.get("id") for r in rows}]
    if not scored:
        print("\n기대 채점(RAG-055): 경계 문항을 가진 랩이 없다 —"
              " `rag generate --questions --lap lapN` 으로 새 랩을 떠야 잰다")
        return 0

    for stem, rows in scored:
        base = scorer.grade_expect(rows, expects, "none", codes)
        head = f"\n기대 채점 — {stem}  (채점 가능 {base['gradable']}문항"
        if base["unmeasurable"]:
            head += (f" · refuse {base['unmeasurable']}문항은 이 랩에 `boundary` 칸이 없어 못 잰다"
                     " — 덤프 VERSION 2 부터 있다")
        print(head + ")")

        if base["refuse_n"]:
            # **거절은 정책과 무관하다** — 생성이 질문을 보고 낸 값이라 기권 문턱을 바꿔도
            # 안 변한다. 정책 표에 섞으면 같은 수가 줄마다 되풀이돼 읽는 사람을 헷갈리게 한다
            print(f"  거절(생성이 낸 boundary) — 맞음 "
                  f"{base['refuse_n'] - base['missed_refuse'] - base['wrong_code']}"
                  f"/{base['refuse_n']}  ·  놓침 {base['missed_refuse']}"
                  f"  ·  코드 다름 {base['wrong_code']}"
                  f"  ·  경계 아닌데 거절 {base['false_refuse']}")

        print(f"  {'정책':24} {'통과':>7}   {'오기권':>18}   {'놓친 기권':>16}")
        print("  " + "-" * 72)
        for name in scorer.ABSTAIN_POLICIES:
            g = scorer.grade_expect(rows, expects, name, codes)
            mark = " ←현행" if name == "none" else ""
            print(f"  {name:24} {g['passed']:>3}/{g['gradable']:<3}"
                  f"   {g['false_abstain']:>8}/{g['answer_n']:<8}"
                  f"   {g['missed_abstain']:>7}/{g['abstain_n']:<7}{mark}")
        print("  오기권 = 답해야 하는데 기권(신호가 과하게 켜졌다) ·"
              " 놓친 기권 = 기권해야 하는데 답함(신호가 안 켜졐다)".replace("켜졐", "켜졌"))
        print("  통과 수에는 거절 채점도 들어간다 — 정책이 같아도 랩이 다르면 이 칸이 움직인다")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m rag")
    sub = p.add_subparsers(dest="cmd", required=True)

    lst = sub.add_parser("list", help="원본 목록과 파서 구현 여부")
    lst.add_argument("--source", help="meta 의 source_id 로 거르기")
    lst.set_defaults(fn=cmd_list)

    par = sub.add_parser("parse", help="raw/ → processed/parsed/")
    par.add_argument("--source", help="meta 의 source_id 로 거르기")
    par.add_argument("--limit", type=int, default=0, help="앞에서 N개만")
    par.add_argument("--force", action="store_true", help="원본이 그대로여도 다시 파싱")
    par.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 결과만 출력")
    par.add_argument("-v", "--verbose", action="store_true")
    par.set_defaults(fn=cmd_parse)

    chk = sub.add_parser("chunk", help="processed/parsed → processed/chunks")
    chk.add_argument("--source", help="parsed 헤더의 source_id 로 거르기")
    chk.add_argument("--force", action="store_true", help="parsed 가 그대로여도 다시 청킹")
    chk.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 결과만 출력")
    chk.add_argument("-v", "--verbose", action="store_true")
    chk.set_defaults(fn=cmd_chunk)

    shw = sub.add_parser("show", help="청크를 눈으로 본다 (검문소①)")
    shw.add_argument("pattern", help="chunk_id 의 일부")
    shw.add_argument("--limit", type=int, default=5)
    shw.add_argument("--full", action="store_true", help="본문을 자르지 않고 전부")
    shw.set_defaults(fn=cmd_show)

    emb = sub.add_parser("embed", help="processed/chunks → processed/embeddings (모델 3종)")
    emb.add_argument("--model", help=f"하나만: {list(embed.MODELS)}")
    emb.add_argument("--all", dest="all_models", action="store_true",
                     help="3종 전부 (6단계 3파전용). **기본은 서빙 모델 하나다** — RAG-064 ⑦")
    emb.add_argument("--batch", type=int, default=8)
    emb.add_argument("--force", action="store_true", help="청크가 그대로여도 다시")
    emb.add_argument("--guard-only", action="store_true",
                     help="토큰 가드만 돌리고 인코딩은 하지 않는다 (가중치 로드 없음)")
    emb.add_argument("--dry-run", action="store_true",
                     help="인코딩은 **하고** 쓰지는 않는다. 가드까지만 보려면 --guard-only")
    emb.add_argument("--quiet", action="store_true", help="진행 막대를 끈다")
    emb.add_argument("--full", action="store_true",
                     help="증분을 쓰지 않고 전량 인코딩 (RAG-064 — 증분과 대조할 때)")
    emb.add_argument("--backfill-hashes", action="store_true",
                     help="옛 parquet(v1)에 content_sha256 을 채운다. 벡터는 안 건드린다 (RAG-064)")
    emb.add_argument("--restamp", action="store_true",
                     help="벡터는 두고 지문만 다시 찍는다 (RAG-025 ⑤ 일회성. 행이 일치할 때만)")
    emb.set_defaults(fn=cmd_embed)

    gld = sub.add_parser("goldenset", help="골든셋 라벨이 실재하는 청크인지 검사 (RAG-022)")
    gld.add_argument("-v", "--verbose", action="store_true", help="문항별 라벨을 전부 출력")
    gld.set_defaults(fn=cmd_goldenset)

    ev = sub.add_parser("evaluate", help="6단계 3파전 — 채점하고 승자를 고른다 (RAG-024)")
    ev.add_argument("--model", help=f"하나만: {list(embed.MODELS)}")
    ev.add_argument("--all", dest="all_models", action="store_true",
                    help="3종 전부. **판정(승자 고르기)은 셋이 다 있어야 하므로 이것이 필요하다**")
    ev.add_argument("--force", action="store_true",
                    help="parquet 이 지금 청크와 어긋나도 채점한다")
    ev.add_argument("--dry-run", action="store_true", help="덤프를 쓰지 않는다")
    ev.add_argument("-v", "--verbose", action="store_true", help="문항별 결과를 전부 출력")
    ev.set_defaults(fn=cmd_evaluate)

    ld = sub.add_parser("load", help="7단계 — chunks+embeddings → documents (RAG-025)")
    ld.add_argument("--model", help=f"기본 {config.settings.embedding_model_key}"
                                    " (RAG-024 판정 승자). 교체는 이 인자 하나")
    ld.add_argument("--dry-run", action="store_true", help="DB 를 열지 않고 만들 행만 확인")
    ld.add_argument("--show", type=int, default=5, help="dry-run·stale 에서 보여 줄 행 수")
    ld.add_argument("--prune", action="store_true",
                    help="이번 적재에 없는 행(사라진 청크)을 지운다. 기본은 세어서 경고만 (RAG-045)")
    ld.add_argument("--allow-metadata-loss", action="store_true",
                    help="DB 에 있는 메타 키가 이번 적재로 통째로 사라져도 진행한다."
                         " 기본은 멈춘다 — `org` 이 그렇게 지워진 적이 있다 (RAG-066)")
    ld.set_defaults(fn=cmd_load)

    sr = sub.add_parser("search", help="8단계 — dense 검색 (검문소③, RAG-026)")
    sr.add_argument("query", nargs="*", help="질의. --questions 를 쓰면 생략")
    sr.add_argument("--questions", action="store_true",
                    help="검증질문 1~7 전부 (goldenset.yaml 의 origin=hand)")
    sr.add_argument("-k", type=int, default=searcher.DEFAULT_K, help="top-k (기본 5)")
    sr.add_argument("--model", help=f"기본 {list(embed.MODELS)[0]} (RAG-024 판정 이후)")
    sr.add_argument("--category", help="policy/travel/food 로 사전 필터")
    sr.add_argument("--width", type=int, default=150, help="본문 발췌 길이")
    sr.add_argument("--no-supplementary", dest="supplementary", action="store_false",
                    help="부칙(시행일·경과조치)을 뺀다. **기본은 포함**이다 — 검문소③은"
                         " 걸러지지 않은 것을 봐야 한다 (RAG-026 ①)")

    gen = sub.add_parser("generate", help="9단계 — 검색 + Gemini 답변 (검문소④, RAG-028)")
    gen.add_argument("query", nargs="*", help="질의. --questions 를 쓰면 생략")
    gen.add_argument("--questions", action="store_true",
                     help="검증질문 1~7 전부 = 검문소④ (goldenset.yaml 의 origin=hand)")
    gen.add_argument("-k", type=int, default=searcher.DEFAULT_K, help="컨텍스트에 넣을 top-k (기본 5)")
    gen.add_argument("--model", help=f"임베딩 모델. 기본 {list(embed.MODELS)[0]} (RAG-024 판정 이후)")
    gen.add_argument("--category", help="policy/travel/food 로 사전 필터")
    gen.add_argument("--width", type=int, default=150, help="근거 발췌 길이 (답변 본문은 안 자른다)")
    gen.add_argument("--lap", default="lap1", help="덤프 파일명. 2랩은 lap2 (RAG-028 ⑥)")
    gen.add_argument("--no-supplementary", dest="supplementary", action="store_false",
                     help="부칙을 뺀다. 기본은 포함 — 서빙 기본값은 app/services 가 갖는다 (RAG-026 ①)")
    gen.add_argument("--dry-run", action="store_true", help="덤프를 쓰지 않는다")
    gen.set_defaults(fn=cmd_generate)
    sr.set_defaults(fn=cmd_search, supplementary=True)

    scl = sub.add_parser("score-laps", help="저장된 모든 랩을 새 지표로 소급 채점 (RAG-029)")
    scl.add_argument("--by", default="trust_level",
                     choices=["trust_level", "source_id", "subcategory", "category"],
                     help="종류별 슬라이스의 축 (RAG-060). 청크 행의 그 칸에서 파생한다")
    scl.add_argument("--laps", type=int, default=6, metavar="N",
                     help="슬라이스 표에 보일 최근 랩 수 (0=전부). 기본 6")
    scl.set_defaults(fn=cmd_score_laps)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
