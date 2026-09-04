# Training RAG Bathing-Aversion Investigation Report

**Query**: `목욕을 싫어해요` ("My dog hates bathing")  
**Status**: UNCERTAIN (model_reported_insufficient_evidence)  
**Investigation Date**: 2026-09-04  
**Scope**: Phases 1-4 complete (diagnostic analysis); Phase 5 not applicable; Phase 6 complete for this investigation (documentation only); Phase 7 proposal only; Phase 8 deferred

---

## Phase 1: Baseline Reproduction

**Setup**:
- Server: DESKTOP-02CFSOD
- Serving Corpus: 14 NIAS companion documents
- Query Model: E5 multilingual embeddings
- Top-K: 4

**Query Result**:
```
{
  "decision": "PASS",           # Gate passed (retrieval confident)
  "top_score": 0.8210,
  "reason": "retrieval_confident"
}
```

**Top 4 Retrieved Chunks**:

| Rank | Score | Document ID | Heading | Topic | In Serving |
|------|-------|-------------|---------|-------|------------|
| 1 | 0.8210 | nias_companion-64753005... | FAQ | Biting when touching body parts | YES |
| 2 | 0.8124 | nias_companion-86bf30... | FAQ | **Navigation artifact (pagination)** | YES |
| 3 | 0.8100 | nias_companion-a6aeb... | Manners Education | Toilet training (대소변가리기) | YES |
| 4 | 0.8100 | nias_companion-64753... | FAQ | **Navigation artifact (pagination)** | YES |

**Analysis**:
- **All 4 are in the serving allow-list**
- Ranks 2 & 4 are extraction artifacts (pagination links only)
- Rank 1: About handling sensitivity/biting, NOT bathing aversion
- Rank 3: About toilet training, NOT bathing aversion
- **None directly address bathing-aversion training**
- Despite gate PASS, model correctly reported insufficient evidence

---

## Phase 2: Retrieved Chunk Analysis

### Rank 1 (0.8210) - Biting Sensitivity
**Heading**: FAQ / Biting behavior  
**Content**: "When a dog shows sensitivity to a specific body part, it means painful memories remain strongly..."  
**Classification**: Adjacent handling/grooming evidence (partially relevant to body handling)  
**Why Model Abstained**: Content is about fearful biting, not aversion to water/bathing process

### Rank 2 (0.8124) - Navigation Artifact
**Content**: Pure pagination links `[처음 페이지로] [이전 페이지] [다음 페이지]`  
**Classification**: Extraction/navigation artifact, ZERO behavioral content  
**Why Model Abstained**: No substantive text to work with

### Rank 3 (0.8100) - Toilet Training
**Heading**: Manners Education / Toilet training (대소변가리기)  
**Content**: Table about toilet training methods and behavioral problems  
**Classification**: Unrelated training (different problem domain)  
**Why Model Abstained**: Wrong training topic entirely

### Rank 4 (0.8100) - Navigation Artifact
**Content**: Pagination links, page numbers  
**Classification**: Extraction artifact  
**Why Model Abstained**: No behavioral content

---

## Phase 3: Full-Corpus Search for Bathing Content

**Search Terms Tested**:
```
Korean:
  - 목욕 (bathing)
  - 씻 (wash)
  - 욕실 (bathroom)
  - 욕조 (bathtub)
  - 드라이기 (hair dryer)

English:
  - bath, shower, wash, groom, desensitize, water
```

**Result**: **ZERO matches across entire database**

No documents in the collected corpus contain any bathing-related vocabulary.

---

## Phase 4: Ranking Comparison Across Diagnostic Queries

| Query | Top Rank Score | Document | Topic | In Serving |
|-------|------------------|----------|-------|------------|
| `목욕을 싫어해요` | 0.8210 | nias-64753 | Biting behavior | YES |
| `강아지가 목욕을 무서워하고 도망가요` | 0.8260 | nias-a6aeb | Toilet training | YES |
| `강아지를 목욕에 천천히 적응시키는 방법을 알려줘` | 0.8564 | nias-64753 | FAQ | YES |
| `물을 묻히면 강아지가 도망가요` | 0.8312 | nias-64753 | FAQ | YES |
| `드라이기 소리를 무서워해요` | 0.8223 | nias-64753 | FAQ | YES |
| `목욕할 때 물려고 해요` | 0.8271 | nias-a6aeb | FAQ | YES |

**Pattern Observed**: All bathing-related queries return non-bathing content.  Semantic embeddings find no direct matches, so similar behavioral topics rank highest (toilet training, handling sensitivity).

---

## Root Cause Determination

**Confirmed Root Cause: #1 - No directly relevant bathing-aversion evidence exists in collected corpus**

Evidence:
1. Full-database lexical search returns zero results for all bathing terms
2. Top-4 semantic results do not contain bathing content
3. Diagnostic queries across 6 variants consistently retrieve unrelated topics
4. Model's `model_reported_insufficient_evidence` response is **correct** — there IS no evidence to provide

**Not causes #2-5**:
- #2 (outside serving list): N/A — bathing content doesn't exist anywhere in database
- #3 (parsing damage): N/A — no source text to damage
- #4 (ranking below top 4): N/A — no bathing content exists to rank
- #5 (generation issue): N/A — model correctly reports what it found (nothing)

---

## Current Serving Corpus Coverage

The 14-document serving corpus covers:

**Training Topics Present**:
- Toilet training (대소변)
- Barking/excessive noise
- Separation anxiety & crate training
- Biting & mouthing behavior
- Handling sensitivity
- Leash training & manners
- Social interaction with other dogs
- Feeding/food-related behavior
- Clinginess & attachment issues
- Sound sensitivity (fireworks, etc.)

**Training Topics Absent**:
- ✗ Bathing aversion
- ✗ Water/shower adaptation  
- ✗ Grooming (professional handling)
- ✗ Desensitization to grooming tools
- ✗ Anxiety during veterinary exams

---

## Minimum Justified Fix Options

### Option A: Document as Out-of-Scope (Recommended for Phase 6)

**Action**: Update documentation to explicitly state that bathing-aversion training is not covered in the current serving corpus.

**Why**: 
- Honest and transparent
- No false positives
- Sets user expectations correctly
- No code changes required

**This investigation's action**: This report documents the coverage gap. It does not modify the serving corpus manifest, the evaluation dataset, or any other runtime artifact — those remain a separate future decision (see `bathing_coverage_gap_proposal_0904.md`).

### Option B: Source & Review Bathing Content (Requires Business Decision)

**Action**: Identify authoritative Korean dog training sources on bathing aversion, evaluate for addition to corpus.

**Why**:
- Addresses actual user need
- Aligns with "comprehensive training coverage" goal
- Potential high-demand topic

**Requirements**:
- Source identification
- Review & vetting process
- Parsing/chunking/embedding pipeline
- Addition to serving allow-list
- Re-evaluation of frozen test set

**Blockers**: 
- No approved bathing-aversion source identified yet
- Requires upstream review process
- Cannot be a workaround (no unreviewed sources)

### Option C: Adjust Semantics (NOT Recommended)

**Explicitly Forbidden** per task constraints:
- Semantic router modification
- New Care capability
- Hard-coded bathing → document mapping
- Threshold-based workarounds
- Unreviewed source injection

---

## Recommendations

### Phase 5 (Parsing & Chunking): Not applicable
No relevant ingested source was found, so there is no parsing or chunking behavior to inspect.

### Phase 6 (Minimum Fix): Complete for this investigation
The minimum justified action is to document the coverage gap and stop pending source review (Option A). This report is that action. Option B (sourcing new bathing content) is a separate future decision requiring human source approval — see "Remaining Human Decision" below.

### Phase 7 (Evaluation): Proposal only, not implemented
Candidate bathing-related queries are recorded as future evaluation cases in `bathing_coverage_gap_proposal_0904.md`. They are not integrated into any permanent or frozen evaluation dataset in this PR, because doing so would freeze a temporary coverage gap as if it were expected long-term behavior.

### Phase 8 (Verification): Deferred
Deferred until an approved bathing-aversion source and its ingestion are implemented. There is nothing to verify yet, since no retrieval, generation, or corpus change was made.

---

## Data Artifacts

- `docs/training/reports/generated/bathing_search_results_0904.json` — Phase 4 ranking comparison
  - Six diagnostic queries, top 20 results per query, 120 ranked results total
  - Zero directly relevant bathing-aversion chunks found across all 120 ranked results
  - Format: JSON with chunk IDs, document IDs, scores, heading paths, serving eligibility
- Database queries executed on 2026-09-04 against production vectordb (read-only, no mutations)

---

## Timeline

- **Phase 1-4**: Complete — baseline reproduction, chunk analysis, full-corpus search, ranking comparison
- **Phase 5**: Not applicable — no relevant ingested source was found
- **Phase 6**: Complete for this investigation — coverage gap documented, work stopped pending source review
- **Phase 7**: Proposal only — not implemented, not integrated into any permanent evaluation dataset
- **Phase 8**: Deferred — until an approved source and implementation exist
