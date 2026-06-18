# memanto 격차 분석 & 따라잡기 로드맵

> 대상: **only-my-mem0ry v0.4.0** (commit `f8f5ada`) ↔ **moorcheh-ai/memanto v0.2.1**
> 작성: 2026-06-18 · 상태: 제안(draft)
>
> 목적 — memanto의 기능을 1:1로 꼼꼼히 대조하고, **무엇을 / 어떤 순서로 / 어떻게**
> 가져올지 결정한다. 핵심 전제: *only-my-mem0ry의 정체성을 깨지 않으면서* 격차를 좁힌다.
> "memanto를 흉내 내는 것"이 목표가 아니라, memanto가 지적한 "수동적 인프라의 6가지
> 한계"를 **이 프로젝트의 철학(클라이언트가 두뇌·100% 로컬·결정적)에 맞는 방식으로**
> 해소하는 것이 목표다.

---

## 0. 한눈 요약 (TL;DR)

- **이미 따라잡음**: 타입형 메모리(13범주, v0.4.0), 쓰기 시점 LLM 추출 없음(zero-overhead
  ingestion = `infer=False`), "주입이 아니라 질의 가능"(search + 상시 core), 중복 클러스터.
- **채택(ADOPT)할 격차**: provenance·confidence 메타데이터, temporal/`changed-since` 필터,
  버전 히스토리(no silent overwrite), 충돌 *후보* 탐지(LLM 없이), 파일 인제스트, 배치 추가,
  전체 export.
- **적응(ADAPT)할 격차**: `answer`(RAG) → 서버 LLM이 아니라 **클라이언트가 답하게 하는
  프롬프트**로. `daily-summary` → 큐레이션류 프롬프트로.
- **거부(DECLINE)**: Moorcheh 엔진 교체, 클라우드 백엔드, Ollama/on-prem LLM, 풀
  인터랙티브 Web UI, REST API.  **보류(DEFER)**: 크로스플랫폼(Linux/Windows), 스케줄러.
- **순서**: v0.5.0(메타데이터+answer) → v0.6.0(버전 히스토리) → v0.7.0(충돌 후보) →
  v0.8.0(파일 인제스트+배치).

---

## 0.5. 진행 로그 (Progress log)

| 날짜 | 작업 | 상태 | 비고 |
|---|---|---|---|
| 2026-06-18 | 문서 작성(이 gap-analysis) | ✅ | v0.4.0(commit f8f5ada) 기준 |
| 2026-06-18 | **Phase 1 / `answer(query)` 프롬프트** (#13) | ✅ 완료 | 서버 LLM 없이 회수+프레이밍 → 클라이언트가 `[id]` 인용해 답변. `_answer_context` 헬퍼(단위 테스트 가능) + `answer()` 프롬프트. 통합 테스트 2개 추가. CHANGELOG `[Unreleased]`, README(+ko) 프롬프트 표 갱신. 랭킹 경로 불변(=eval 비회귀). |
| 2026-06-18 | **Phase 1 / provenance·confidence·temporal·export** (#9·#10·#7·#16) | ✅ 완료 (v0.5.0) | provenance(origin/source, `set_provenance`, search `origin=`)는 이미 작업트리에 있던 것 확정·문서화; confidence(`CONFIDENCE_LEVELS`/`normalize_confidence`, `add_memory(confidence=)`, `set_confidence`, `search(min_confidence=)` — 미평가 메모리는 게이트 시 제외) 추가; temporal(`parse_date`/`date_of`, search `since`/`until`/`changed_since` + list `since`/`until`, 일 단위 포함) 추가; 전체 export CLI `server/export_memory.py`(Markdown/JSON, 스토어+사이드카 직접 읽기). 뷰어에 신뢰도 필터/칩. 사이드카에 `confidence`·`history` 키(무마이그레이션). |
| 2026-06-18 | **Phase 2 / 버전 히스토리** (#12) | ✅ 완료 (v0.6.0) | 사이드카 `history` 맵 + `_archive_version`(user_id 캡처); `update_memory`·`delete_memory`가 직전 본문 보관(삭제는 히스토리 유지); `memory_history`·`restore_memory` 툴; `MEM0_HISTORY_DEPTH`(기본 5). |
| 2026-06-18 | **Phase 3 / 충돌 후보 + recency tie-break** (#11·#8) | ✅ 완료 (v0.7.0) | `mem0_retrieval.is_conflict_pair`(순수: 숫자·요일·반의어·부정) + 서버 `_conflict_candidates`(코사인 대역 `[MEM0_CONFLICT_LOW=0.80, DUP_THRESHOLD)`), `curate_memories`에 ⚔️ 섹션. opt-in tie-break `rerank_with_bias` + `MEM0_RECENCY_BIAS=0`/`MEM0_CONFIDENCE_BIAS=0`(기본 off=no-op, eval 비회귀 증명). |
| 2026-06-18 | **Phase 4 / 파일 인제스트 + 배치 추가** (#14·#15) | ✅ 완료 (v0.8.0) | `server/ingest_file.py`(stdlib txt/md/csv/json/log + 선택적 pdf/docx/xlsx, 결정적 `chunk_text`, `--dry-run`, 백엔드 떠 있으면 거부); `add_memories(items_json)` 툴 + 공용 `_add_many`(락 1회). `requirements-ingest.txt` 격리. |

**로드맵 완료** — Phase 1~4 모두 구현·테스트·문서화 완료. 게이트: ruff clean · pytest
152 pass(baseline ~89) · eval_recall 비회귀(dense==hybrid hit@1 0.864 / hit@3·5 1.000 /
MRR 0.917). CHANGELOG에 **0.5.0–0.8.0** 섹션으로 정리(태깅은 커밋 시점에).

---

## 1. 절대 원칙 (NON-NEGOTIABLES)

신규 기능은 **전부** 아래를 만족해야 한다. 하나라도 깨면 그 기능은 "거부" 대상이다.

1. **두 번째 LLM 금지.** 호출 에이전트(클라이언트)가 유일한 두뇌다. 서버·스크립트는 어떤
   추론 모델도 호출하지 않는다. (memanto on-prem이 Ollama를 띄우는 것과 정반대)
2. **100% 로컬·오프라인.** 클라우드 전송 옵션을 만들지 않는다. 외부 네트워크 호출 없음
   (임베더 최초 1회 다운로드만 예외).
3. **결정적(deterministic).** 동일 입력 → 동일 출력. 난수·모델추론 기반 동작 금지.
4. **메타데이터는 사이드카(`memory_meta.json`)에.** Chroma payload는 건드리지 않는다 —
   mem0 2.0.4의 `update()`가 payload metadata를 재구성하므로 커스텀 필드는 사라진다.
   (tags·types가 이미 이 패턴; 모든 신규 메타도 동일)
5. **랭킹 비회귀(non-regressing).** dense + BM25 `rescue` 융합 경로는 손대지 않는다. 새
   신호는 **후처리 필터** 또는 **동점 처리(tie-break)** 로만 쓰고, 그조차 **opt-in(기본
   off)** 으로 둔다. 모든 변경은 `server/eval_recall.py`로 hit@k/MRR 비회귀를 증명한다.
6. **단일 writer 안전성.** 모든 store 연산은 `_store_lock`으로 직렬화하고, OS 파일락
   (`.writer.lock`)을 유지한다.
7. **데이터 무손실.** 잘못된 입력은 거부보다 "저장 + 경고"를 우선한다(add 경로). 파괴적
   변경(update/delete)은 **사이드카에 백업**한 뒤 수행한다.
8. **의존성 최소.** 런타임 deps(`requirements.txt`)는 그대로 둔다. 새 무거운 의존(PDF/docx
   파서 등)은 **optional extra**로 격리해, 그 기능을 쓰는 사람만 설치한다.
9. **macOS launchd 라이프사이클 유지.** idle-exit RAM 회수·프록시 구조를 깨지 않는다.

---

## 2. 전체 기능 대조 매트릭스

판정: **DONE**(이미 보유) · **ADOPT**(그대로 채택) · **ADAPT**(철학에 맞게 변형 채택) ·
**DEFER**(나중) · **DECLINE**(의도적 거부).

| # | memanto 기능 / 지적한 격차 | memanto 방식 | only-my-mem0ry 현재 | 판정 |
|---|---|---|---|---|
| 1 | Flat memory → 타입형(13범주) | `--type`, 13 categories | `mem_type`, 동일 13범주 (v0.4.0) | **DONE** |
| 2 | Indexing delay → zero-overhead ingestion | 쓰기 즉시 검색, LLM 추출세 없음 | `infer=False` verbatim 저장 | **DONE** |
| 3 | Static injection → queryable | 주입 대신 질의 | `search_memories` + 상시 **core**(주입까지) | **DONE(+초과)** |
| 4 | `remember` (단건 저장) | CLI/REST | `add_memory` | **DONE** |
| 5 | `forget` (단건 삭제) | `--force` | `delete_memory` | **DONE** |
| 6 | `recall` (검색) | 단일쿼리 시맨틱 | 하이브리드(dense+BM25 rescue) | **DONE** |
| 7 | `recall` **시간질의** (`--as-of`,`--changed-since`) | 시점/변경분 조회 | created_at·updated_at는 보유, 필터 없음 | **ADOPT** P1 |
| 8 | No temporal decay → **recency 신호** | 최근성 가중 | usage(count/last)+뷰어 날짜정렬 | **ADAPT** P3 (opt-in tie-break) |
| 9 | No provenance → **provenance 메타** | 출처/유래 기록 | 없음 | **ADOPT** P1 |
| 10 | No provenance → **confidence 메타** | 신뢰도 점수 | 없음 | **ADOPT** P1 |
| 11 | No writeback → **conflict detection** | 모순 감지·해소 | 중복 클러스터(redundancy)만 | **ADOPT** P3 (LLM-free 후보) |
| 12 | No writeback → **versioning / no silent overwrite** | 명시적 버전 | `update`가 제자리 덮어씀(이전본 소실) | **ADOPT** P2 |
| 13 | `answer` (RAG grounded QA) | 메모리 기반 LLM 답변 | `answer(query)` 프롬프트 — 회수+프레이밍, 클라이언트가 생성 | **✅ DONE** (Unreleased) |
| 14 | `upload` (파일→메모리) | PDF/docx/xlsx/csv/md/json | 없음 | **ADOPT** P4 |
| 15 | 배치 `remember` (≤100 JSON) | 일괄 인입 | 단건만 | **ADOPT** P4 |
| 16 | `memory export` / `MEMORY.md` sync | 마크다운 export·동기화 | `CORE_MEMORY.md` 미러(core만) | **ADOPT-lite** P1 (전체 export) |
| 17 | `daily-summary` | 일일 요약 | 없음 | **ADAPT/DEFER** (프롬프트) |
| 18 | `agent bootstrap` (지능 스냅샷) | 세션 초기 컨텍스트 | `load_context` 프롬프트 | **DONE(≈)** |
| 19 | `session` / `schedule` | 세션·스케줄 관리 | launchd가 라이프사이클 담당 | **DEFER** |
| 20 | `status` 대시보드 | 환경/서버 상태 | 로그+`launchctl` | **DEFER** |
| 21 | REST API (`serve`) | HTTP API | MCP 자체가 API | **DECLINE** |
| 22 | Web UI (`ui`, 인터랙티브) | 브라우저 편집 | 읽기전용 HTML 뷰어 | **DECLINE**(인터랙티브) |
| 23 | `connect` (14+ 에이전트) | 원클릭 통합 | MCP = 모든 MCP 클라이언트 호환 | **N/A(무료로 보유)** |
| 24 | Moorcheh 엔진(벡터DB 미사용) | 정보이론 검색 | Chroma + BM25(측정됨) | **DECLINE** |
| 25 | 클라우드 백엔드(scale-to-zero) | 관리형 클라우드 | launchd idle-exit | **DECLINE** |
| 26 | on-prem Docker + Ollama | 로컬 LLM 스택 | 두 번째 LLM 없음 | **DECLINE** |
| 27 | 크로스플랫폼(mac/linux/win) | 전 플랫폼 | macOS launchd 전용 | **DEFER** |
| 28 | 벤치마크(LongMemEval/LoCoMo) | 파이프라인 성능 | `eval_recall`(검색 recall) | **DEFER**(다른 계층) |

> **핵심 인식**: #21~#26은 "격차"가 아니라 **다른 설계 선택**이다. memanto는 "메모리를
> 똑똑하게(LLM 내장)", only-my-mem0ry는 "메모리는 단순하게, 클라이언트가 두뇌". #13
> `answer`처럼 보이는 격차도 사실 **이미 가능**하다 — 클라이언트가 LLM이므로
> `search → 답변`을 하면 된다. 우리는 그걸 *프롬프트로 1급 시민화*만 하면 된다.

---

## 3. 우선순위 로드맵 (단계별)

각 항목: **가치(V)/노력(E)/위험(R)** = High·Med·Low.

### Phase 1 — v0.5.0 「출처·신뢰·시간·답변」
memanto 격차 3·(2 일부)·13을 한 번에 메운다. 전부 사이드카/후처리/프롬프트라 **랭킹 위험 0**.

- **provenance**(#9) — V:H E:M R:L
- **confidence**(#10) — V:M E:L R:L
- **temporal 필터**(`since`/`until`/`changed_since`, #7) — V:M E:M R:L
- **`answer` 프롬프트**(#13) — V:H E:L R:L
- **전체 export**(#16) — V:M E:L R:L

### Phase 2 — v0.6.0 「버전 히스토리」
- **versioning / no silent overwrite**(#12) — V:H E:M R:L
  update·delete가 이전 본문을 사이드카 history에 보존. `memory_history`·`restore_memory` 추가.

### Phase 3 — v0.7.0 「충돌 후보 + 최근성」
- **conflict candidates**(#11) — V:H E:H R:M (LLM 없이 휴리스틱, 클라이언트가 확정)
- **recency tie-break**(#8) — V:M E:M R:M (기본 off, 측정 후에만)

### Phase 4 — v0.8.0 「파일 인제스트 + 배치」
- **file ingest**(#14) — V:H E:M R:L (별도 CLI, optional 파서 deps)
- **batch add**(#15) — V:M E:L R:L

> 권장 착수 순서 = Phase 번호 순. Phase 1은 위험이 거의 없고 가치가 커서 즉시 시작 가능.

---

## 4. 기능별 상세 설계

모든 신규 메타는 `memory_meta.json` 사이드카에 키를 추가하고, `load_meta`에 `setdefault`만
넣으면 **하위호환·무마이그레이션**이다(타입 추가 때 검증된 패턴). 모든 도구는 `_store_lock`
안에서 동작하고, 삭제 시 해당 메타도 함께 정리한다.

### 4.1 Provenance (출처/유래) — P1
- **데이터 모델**: `provenance: { <id>: { "origin": "explicit|inferred|imported", "source": "<free text>" } }`
  - `origin`은 통제 어휘(3종) — `normalize_origin()` 헬퍼(type와 동일한 3-way 계약).
  - `source`는 자유 문자열(예: `"user chat"`, `"file:report.pdf#p3"`, `"git:repoA"`).
- **도구**:
  - `add_memory(..., origin="", source="")` — 관대(unknown origin이면 경고 후 무출처 저장).
  - `set_provenance(id, origin="", source="")` — 엄격(unknown origin 거부, 빈 문자열 정리).
  - `search_memories(..., origin=)` — 후처리 필터(tags·type와 AND 결합).
- **렌더**: search/list/curate에 `«explicit»` 또는 `«imported:report.pdf»` 라벨.
- **뷰어**: origin 필터 드롭다운 + 칩. **테스트**: normalize_origin 단위 + 통합(set/clear/filter).
- **비회귀**: 순수 후처리. 랭킹 불변.

### 4.2 Confidence (신뢰도) — P1
- **데이터 모델**: `confidence: { <id>: "high|medium|low" }` (정수/실수 대신 **coarse enum** —
  결정적이고 가짜 정밀도를 피함; 클라이언트가 판단해 부여).
- **도구**: `add_memory(..., confidence="")`, `set_confidence(id, value)`. `search_memories(..., min_confidence=)` 후처리 필터(`low<medium<high` 순서비교).
- **활용**: (1) 렌더 라벨, (2) `curate_memories` 힌트(low + 오래됨 + 미사용 = 재검토 후보),
  (3) **선택적** 동점 tie-break(`MEM0_CONFIDENCE_BIAS=0` 기본; Phase 3의 recency와 함께 측정).
- **비회귀**: 기본 off이면 랭킹 불변.

### 4.3 Temporal 필터 (`since`/`until`/`changed_since`) — P1
- **데이터원**: created_at·updated_at는 **이미 Chroma payload에 존재**(`_get_all`이 created_at
  반환, 뷰어가 updated_at 사용). 추가 저장 불필요.
- **설계**: `search_memories(..., since="YYYY-MM-DD", until=..., changed_since=...)` —
  검색 풀을 받은 뒤 `{id: created_at/updated_at}` 맵(하이브리드 경로가 이미 부르는
  `_get_all` 재사용)으로 **후처리 필터**. `changed_since`는 updated_at 기준.
- **부가**: `list_memories(since=, until=)`도 동일 필터.
- **비회귀**: 후처리만. 랭킹 불변. **테스트**: 경계값(포함/제외), updated vs created.

### 4.4 `answer` 프롬프트 (RAG, 서버 LLM 없이) — P1
- **설계**: 새 MCP 프롬프트 `answer(question)` → 내부에서 `_semantic_search`로 top-k 회수 →
  결과 + 지시문을 반환:
  > "다음 메모리만 근거로 질문에 답하라. 각 주장에 `[id]`를 인용하라. 근거가 부족하면
  > '메모리에 없음'이라고 말하라. 추측 금지."
  실제 생성은 **클라이언트 LLM**이 한다(서버는 회수+프레이밍만). memanto의 3번째 primitive를
  철학을 깨지 않고 제공.
- **위험 0**(프롬프트일 뿐). **테스트**: 회수 결과가 프롬프트에 포함되는지, 빈 결과 처리.

### 4.5 전체 export — P1
- **설계**: `server/export_memory.py` CLI → 전체 메모리를 `MEMORY.md`(또는 JSON)로 덤프
  (id·본문·type·tags·provenance·confidence·생성/수정일). 뷰어처럼 Chroma+사이드카 직접 읽기,
  실행 중 백엔드 불필요. memanto의 `memory export`/`MEMORY.md sync` 대응.

### 4.6 Versioning / no silent overwrite — P2
- **데이터 모델**: `history: { <id>: [ { "text": "<이전 본문>", "ts": "<ISO>", "op": "update|delete" }, ... ] }`
  — **최신 N개 한정**(`MEM0_HISTORY_DEPTH`, 기본 5)로 사이드카 비대화 방지.
- **흐름**: `update_memory`·`delete_memory`가 **변경 직전 본문**을 history에 append한 뒤 수행
  (원칙 7: 파괴 전 백업).
- **도구**: `memory_history(id)`(이전본 나열), `restore_memory(id, n)`(n번째 이전본으로 update).
  - delete는 벡터가 사라지므로 "복원"은 새 id로 재추가(주의서 명시).
- **비회귀**: 검색/랭킹과 무관. **테스트**: update 누적·depth 상한·restore 라운드트립·delete 아카이브.

### 4.7 Conflict candidates (LLM 없이) — P3
- **솔직한 한계**: 진짜 "의미적 모순"은 LLM 없이 단정 불가. 대신 **충돌 후보**를 띄우고
  **클라이언트가 확정**한다(중복 클러스터와 동일 철학).
- **휴리스틱**(순수·결정적, `server/mem0_retrieval.py`에 `conflict_candidates()` 신설):
  같은 사용자 메모리 쌍 중 (a) 임베딩 코사인이 "같은 주제" 대역(예 `0.80 ≤ sim < DUP_THRESHOLD`)
  이면서 (b) 토큰의 대부분을 공유하지만 (c) **숫자·날짜·불리언·부정어** 토큰에서 어긋나는
  쌍을 표시. 예: `"port 5432"`↔`"port 5433"`, `"deploy on Friday"`↔`"deploy on Monday"`,
  `"X is enabled"`↔`"X is disabled"`.
- **표면화**: `curate_memories`에 `⚔️ 충돌 의심` 섹션 추가(+ 필요 시 `detect_conflicts` 프롬프트).
- **위험**: 오탐 → 임계·규칙 튜닝 + "클라이언트가 확인" 문구 필수. **테스트**: 합성 충돌/비충돌 쌍.

### 4.8 File ingest — P4
- **설계**: `server/ingest_file.py` CLI. 텍스트 추출 → **결정적 청킹**(빈 줄 단락 기준, 목표
  ~N자, 약간의 overlap) → 각 청크를 `add`(origin=`imported`, source=`file:<경로>#<청크>`, 태그=파일명).
- **포맷/의존성**: `.txt/.md/.csv/.json`은 stdlib. `.pdf`(pypdf)·`.docx`(python-docx)·`.xlsx`
  (openpyxl)는 **optional extra**(`requirements-ingest.txt`)로 격리 — 원칙 8.
- **LLM 없음**: 청킹은 규칙 기반. 임베딩만 로컬.

### 4.9 Batch add — P4
- **설계**: `add_memories(items_json)` — `[{text, tags?, mem_type?, origin?, ...}]` 배열을 받아
  **락 1회**로 순회 add, id 배열 반환. 인제스트와 짝. 근접중복 경고는 배치 요약으로.

---

## 5. 횡단 관심사 (cross-cutting)

- **사이드카 스키마 진화**: 최종 형태
  `{ pinned, access, tags, types, provenance, confidence, history }`.
  `load_meta`에 각 키 `setdefault`만 추가 → **기존 스토어 무손상·무마이그레이션**(types로 검증됨).
  `delete_memory`는 모든 맵에서 해당 id 제거(이미 tags/types 그렇게 함 → 동일 확장).
- **검증 게이트(모든 단계 공통, 머지 전 필수)**:
  1. `ruff check server tests` 통과,
  2. `pytest` 전부 통과(신규 기능마다 단위+통합 테스트 추가),
  3. `server/eval_recall.py`로 dense/hybrid hit@k·MRR **비회귀** 증명(현 baseline:
     hit@1 0.86 / hit@3·5 1.00 / MRR 0.92, dense==hybrid).
- **뷰어 동반**: 메타가 늘 때마다 `build_memory_viewer.py`에 필터/칩 추가(tags·types 패턴).
- **문서/버전**: 단계마다 README(+ko) 섹션 + `CHANGELOG` 항목 + SemVer 태그.
- **env 추가 예정**: `MEM0_HISTORY_DEPTH`(5), `MEM0_CONFLICT_LOW`(0.80),
  `MEM0_RECENCY_BIAS`(0=off), `MEM0_CONFIDENCE_BIAS`(0=off).

---

## 6. 명시적 비목표 (왜 안 하는가)

- **서버 내 RAG/LLM** — 거부. 클라이언트가 LLM이므로 `answer`는 프롬프트로 충분(§4.4).
  서버에 모델을 넣으면 프로젝트의 존재 이유가 사라진다.
- **Moorcheh 엔진 교체** — 거부. Chroma+BM25 rescue는 측정된 비회귀 자산이고, 엔진 교체는
  거대한 리스크 대비 이득 불명확.
- **클라우드 / Ollama / on-prem Docker 스택** — 거부. 원칙 1·2 정면 위반.
- **인터랙티브 Web UI · REST API** — 거부. 네트워크 노출·인증 부담을 만든다. 읽기전용 뷰어 +
  MCP 도구로 충분.
- **크로스플랫폼** — 보류. 라이프사이클이 launchd 종속. 수요가 크면 별도 `systemd`/Windows
  서비스 어댑터로 분리 검토(코어 로직은 이미 OS 독립적).

---

## 7. 실행 체크리스트 (단계 진입 시)

- [x] Phase 1 — provenance·confidence·temporal 필터·`answer` 프롬프트·export (v0.5.0)
- [x] Phase 2 — 버전 히스토리(`memory_history`/`restore_memory`) (v0.6.0)
- [x] Phase 3 — 충돌 후보 탐지 + (측정 후) recency tie-break (v0.7.0)
- [x] Phase 4 — 파일 인제스트 + 배치 추가 (v0.8.0)

각 PR은 §5의 3단 게이트(ruff·pytest·eval 비회귀)를 통과해야 머지한다. **로드맵 4단계
전부 완료**(2026-06-18): ruff clean · pytest 152 pass · eval_recall 비회귀
(dense==hybrid hit@1 0.864 / hit@3·5 1.000 / MRR 0.917, baseline과 동일; recency/confidence
bias 기본 off라 랭킹 불변). 자세한 내용은 CHANGELOG의 0.5.0–0.8.0 참고.

---

## 부록 A — memanto의 "6가지 격차" 대응 현황

| memanto가 말한 격차 | only-my-mem0ry 대응 | 상태 |
|---|---|---|
| ① Static injection (주입만) | search + 상시 core 주입 | ✅ 보유 |
| ② No temporal decay | temporal 필터(P1) + recency tie-break(P3) | ✅ 완료 |
| ③ No provenance | provenance + confidence(P1) | ✅ 완료 |
| ④ Flat memory | 타입형 13범주 | ✅ v0.4.0 |
| ⑤ No writeback (모순 방치) | 버전 히스토리(P2) + 충돌 후보(P3) | ✅ 완료 |
| ⑥ Indexing delay | `infer=False` 즉시 검색 | ✅ 보유 |

→ 이 로드맵을 완주해, memanto가 지적한 6가지 격차를 **전부**, 그러나 "클라이언트가 두뇌·
100% 로컬·결정적"이라는 우리 방식으로 메웠다(2026-06-18, v0.5.0–0.8.0).
