# local-mem0-mcp

[English](README.md) | **한국어**

[![CI](https://github.com/ost527/local-mem0-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ost527/local-mem0-mcp/actions/workflows/ci.yml)

**macOS의 MCP 클라이언트를 위한 완전 로컬·무설정 [Mem0](https://github.com/mem0ai/mem0) 메모리 서버.**
LLM도, API 키도, 클라우드도 — 그리고 켜고 끄는 스위치도 없습니다. IDE/CLI를 열면
시작되고, 다 쓰면 스스로 꺼져(RAM 반환) 줍니다.

> 비공식 커뮤니티 도구 — mem0ai와는 무관합니다.

---

## 핵심 특징

- 🧠 **루프 안에 LLM이 없습니다.** 여러분의 MCP 클라이언트가 *이미* 유능한 LLM이므로,
  "스마트 메모리" 추론(사실 추출, 중복 제거, 병합, 충돌 해소)은 그쪽이 수행하고 서버는
  단순한 기본 동작만 호출받습니다. 두 번째 모델도, API 키도, 비용도 없습니다.
- 💾 **100% 로컬.** 임베딩은 온디바이스(`intfloat/multilingual-e5-small`)로 동작하고, 메모리는
  `~/.mem0-mcp/chroma`의 로컬 **Chroma** 저장소에 보관됩니다. 오프라인에서도 동작합니다.
- ⚡ **자동 관리 라이프사이클.** 클라이언트를 실행하면 백엔드가 온디맨드로 시작되고,
  마지막 클라이언트를 닫으면 idle-exit하며 ~200MB를 반환합니다. 수동 토글이 없습니다.
- 🤝 **다중 클라이언트 안전.** Kiro, Claude Desktop, Cursor … 모두 **하나의** 백엔드
  프로세스를 공유합니다 — 단일 Chroma writer, 중복 서버 없음, 좀비 없음.
- 📌 **상시(always-on) 코어 메모리.** 절대 잊으면 안 되는 핵심 몇 개를 고정(pin)하면
  파일로 미러링되어 룰 파일이 매 세션 로드합니다 — 검색 없이 항상 컨텍스트에 있습니다.

---

## 구성 방식

```
  ┌────────────┐  stdio   ┌───────────────┐   HTTP 127.0.0.1:8765   ┌─────────────────────┐
  │ MCP client │─spawns──▶│  mem0_proxy   │────────────────────────▶│  mem0 backend (one) │
  │ (Kiro/IDE) │◀─tools───│ (per client)  │  forwards + keepalive   │  embed + Chroma     │
  └────────────┘          └───────────────┘                         └─────────────────────┘
        │ close ─▶ proxy dies ─▶ backend idle-exits (frees RAM)              ▲ single writer
   more clients ── each spawns its own lightweight proxy ────────────────────┘ (shared backend)
```

클라이언트는 작은 **stdio proxy**를 실행합니다. proxy는 **공유 HTTP 백엔드**를
온디맨드로 시작해 모든 툴 호출을 전달하고, 작업하는 동안 백엔드를 warm하게 유지합니다.
마지막 클라이언트가 닫히면 백엔드는 스스로 idle-exit합니다.

---

## 요구사항

- **macOS 12+**
- **Python 3.10+** (`python3`)

이게 전부입니다 — Xcode도, API 키도, 외부 서비스도 필요 없습니다. 임베딩 모델은
첫 사용 시 한 번만 다운로드(기본 다국어 모델 ~470MB)되고, 이후로는 완전히 오프라인으로 동작합니다.

---

## 설치

```bash
git clone https://github.com/ost527/local-mem0-mcp.git
cd local-mem0-mcp
./install.sh
```

`install.sh`는 가상환경을 만들고 의존성(mem0ai, fastmcp, chromadb,
sentence-transformers)을 설치하며, 백엔드용 **온디맨드** `launchd` 에이전트 하나를
등록합니다. 복사해서 붙여넣을 MCP config 스니펫을 그대로 출력해 줍니다. 기본값은
환경변수로 조정할 수 있습니다:

```bash
MEM0_MCP_PORT=8800 MEM0_IDLE_TIMEOUT=900 ./install.sh
```

---

## MCP 클라이언트 연결

클라이언트의 MCP config(예: `~/.kiro/settings/mcp.json`, Claude Desktop, Cursor)에
아래를 추가하세요 — **stdio proxy**를 가리키도록 합니다(`install.sh`가 출력한 절대경로 사용):

```json
{
  "mcpServers": {
    "local-mem0-mcp": {
      "command": "/ABS/PATH/local-mem0-mcp/.venv/bin/python3",
      "args": ["/ABS/PATH/local-mem0-mcp/server/mem0_proxy.py"]
    }
  }
}
```

클라이언트를 재시작하세요. 첫 메모리 호출은 몇 초 걸립니다(백엔드가 콜드스타트하며
임베더를 로드). 그 뒤로는 즉시 동작합니다.

---

## 툴(Tools)

| 툴 | 하는 일 |
|------|--------------|
| `add_memory(text, user_id?, tags?)` | 사실을 그대로 저장. 선택적 `tags`(예: 프로젝트명)로 이후 검색 범위를 좁힘. 조정(reconcile)용으로 가장 가까운 기존 메모리를 함께 반환. |
| `update_memory(id, text)` | 기존 메모리를 교체/병합(중복 방지). |
| `delete_memory(id)` | 오래되었거나 모순되는 메모리 제거. |
| `search_memories(query, user_id?, tags?)` | 시맨틱 검색; 선택적 `tags`로 그 태그 중 **하나라도** 가진 메모리로 범위를 좁힘. 메모리를 **ID와 함께** 반환(📌 고정, `#tags` 표시). |
| `tag_memory(id, tags)` | 메모리의 태그를 설정/교체(빈 문자열이면 제거). 태그는 사이드카에 살아 `update_memory` 후에도 유지. |
| `list_memories(user_id?)` | 저장된 모든 것을 나열(ID 포함; 📌 고정, `#tags` 표시). |
| `pin_memory(id)` | 메모리를 상시 **코어**로 고정(룰 파일이 매 세션 로드하는 파일로 미러링). `MEM0_CORE_BUDGET`로 제한. |
| `unpin_memory(id)` | 코어에서 해제; 메모리는 그대로 저장·검색됨. |

**프롬프트 & 리소스** (지원하는 클라이언트에서 노출) — 에이전트가 검색을 *기억해서*
호출할 필요 없이 회상을 저마찰로 만들어 줍니다:

| 종류 | 이름 | 하는 일 |
|------|------|--------------|
| 프롬프트 | `load_context(query?)` | 관련 메모리를 대화에 컨텍스트로 끌어옴 — 작업 시작 시 호출하면 에이전트가 다시 묻지 않고 회상. query 없으면 전체 나열. |
| 프롬프트 | `curate_memories()` | 유지보수 패스: 전체 인벤토리 + 사용 통계와 함께, 중복 병합·오래된 사실 삭제·재작성·코어 재조정을 에이전트에게 지시. |
| 리소스 | `memory://all` | 저장된 모든 메모리(ID 포함). |
| 리소스 | `memory://core` | 고정된 상시 **코어** 집합. |
| 리소스 | `memory://search/{query}` | `query`에 대한 하이브리드 랭킹 메모리. |

---

## 에이전트가 메모리를 *알아서* 쓰게 만들기

저장은 문제의 절반일 뿐입니다. 나머지 절반은 에이전트가 *묻기 전에 회상*하고
*시키지 않아도 저장*하게 만드는 것입니다 — 그래야 같은 설명을 반복하지 않고
토큰도 아낄 수 있습니다. 세 개의 층이 이를 밀어붙입니다:

1. **서버 instructions** (내장). MCP initialize 응답으로 모든 클라이언트에
   전달되며, 대부분의 클라이언트가 에이전트의 시스템 프롬프트에 주입합니다:
   작업 시작 시와 사용자에게 묻기 전에 메모리를 먼저 검색하고, 영속적 사실은
   알게 된 즉시 저장하며, 중복 대신 조정(reconcile)하고, 비밀값은 절대 저장하지
   않는다. 백엔드와 proxy 둘 다 선언합니다(FastMCP proxy는 initialize에 스스로
   응답하므로). `server/mem0_instructions.py` 참고.
2. **호출 시점이 담긴 툴 설명** (내장). `search_memories`와 `add_memory`에
   명시적 트리거가 들어 있어, 툴 스키마만 읽는 에이전트도 *언제* 호출해야 하는지
   알 수 있습니다.
3. **룰 파일 스니펫** (권장). 서버 instructions를 노출하는 방식은 클라이언트마다
   다르므로, 최대한 확실하게 하려면 아래를 에이전트의 상시 룰(`AGENTS.md`,
   `CLAUDE.md`, `.cursorrules`, Kiro steering 등)에도 붙여넣으세요:

   ```markdown
   ## Long-term memory (local-mem0-mcp)
   You have persistent memory shared with the user's other LLM clients/agents. Use it without being asked:
   - Task start: call search_memories with the task's key terms.
   - Before asking the user anything: search_memories first — the answer may already be stored.
   - On learning a durable fact (decision, preference, config, path, environment quirk): call add_memory immediately, one atomic fact per call.
   - Reconcile, don't duplicate: update_memory to refine/merge; delete_memory when a memory becomes wrong.
   - Never store secrets (passwords, API keys, tokens).
   ```

---

## 코어 메모리 (상시·always-on)

검색 기반 메모리에는 구조적 약점이 하나 있습니다: 에이전트가 검색하기로 *결정*해야
한다는 것. **코어 메모리**가 그 틈을 메웁니다. 절대 잊으면 안 되는 핵심 몇 개(프로젝트
정체성, 핵심 경로, 환경, 핵심 선호)를 고정하면 평범한 파일 `~/.mem0-mcp/CORE_MEMORY.md`로
미러링되고, 이 파일을 여러분의 상시 룰이 **매 세션** 로드합니다. 그 사실들은 툴 호출도,
검색 운(運)도 없이 에이전트에게 도달합니다.

- **고정/해제.** `pin_memory(id)`로 코어에 추가, `unpin_memory(id)`로 해제합니다.
  어느 쪽이든 메모리 자체는 그대로 저장·검색되며, 고정된 항목은 `search_memories` /
  `list_memories`에서 📌로 표시됩니다.
- **설계상 제한됨.** 코어는 `MEM0_CORE_BUDGET`자(기본 4000)로 상한이 있습니다. *매*
  세션에 로드되므로 이 상한이 상시 블록을 작게 유지합니다 — 초과해서 고정하려 하면
  해제하거나 줄이기 전까지 거부됩니다.
- **한 번만 활성화.** 상시 룰 파일에 한 줄을 추가해 에이전트가 매 세션 시작 시 미러를
  읽게 하세요:

  ```markdown
  ## Core memory (always-on)
  At the START of every session, read ~/.mem0-mcp/CORE_MEMORY.md — the user's
  pinned, always-on core memory. (Claude Code: import it with `@~/.mem0-mcp/CORE_MEMORY.md`.)
  ```

미러 파일은 자동 생성됩니다(고정/해제마다, 그리고 백엔드 시작 시 재동기화) — 손으로
편집하지 마세요. 코어는 `memory://core` 리소스로도 노출되고 `load_context` 상단에도
표시됩니다.

---

## 태그 (가벼운 범위 지정)

메모리에 **태그**(짧은 라벨 — 보통 프로젝트명 `32min`이나 영역 `infra`)를 달아 회상을
한 맥락으로 좁힐 수 있습니다:

- **저장 시 태그 지정**: `add_memory(text, tags="32min, infra")`, 또는 기존 메모리에
  `tag_memory(id, "32min")`로 라벨링(빈 문자열이면 제거).
- **검색 범위 좁히기**: `search_memories(query, tags="32min")`는 그 태그 중 **하나라도**
  가진 메모리만 반환합니다. `tags` 없이 검색하면 전체를 대상으로 하므로, 공통 사실은 모든
  프로젝트에서 계속 보입니다.
- 태그는 `search_memories` / `list_memories`에서 `#tag`로 표시되고, HTML 메모리 뷰어에
  태그 필터가 생깁니다.

태그는 벡터 스토어가 아니라 사이드카(`memory_meta.json`)에 저장되므로 `update_memory`
후에도 유지되며 임베딩이나 랭킹에 전혀 영향을 주지 않습니다. 하이브리드 검색 위에 얹는
하드 후처리 필터로, `user_id`(완전 분리)나 상시 **코어** 고정과 상호 보완적입니다.

---

## 메모리 정리(큐레이션)

검색할 때마다 메모리별 가벼운 사용 통계(검색된 횟수 + 마지막 사용일)가 조용히
기록됩니다. `curate_memories` 프롬프트는 이를 유지보수 패스로 바꿉니다: 전체 인벤토리
(📌 고정, 생성일, 사용량)를 펼쳐 놓고, 중복 병합·오래된 사실 삭제·문구 다듬기·상시
코어 슬롯 재조정을 에이전트가 한 번에 하나씩 수행하도록 합니다. 주기적으로, 또는
메모리가 어수선하다 싶을 때 실행하세요. (사용량이 적다는 것만으로는 삭제 이유가 되지
않습니다: 여전히 참인 영속적 사실은 유지합니다.)

---

## 메모리 동작 방식 (클라이언트가 두뇌)

Mem0의 가치는 "스마트 메모리"입니다: 오래 남길 사실을 뽑아낸 뒤 add / update /
delete 하여 메모리를 중복 없이 일관되게 유지하는 것. 보통은 LLM이 필요하지만 —
**여러분의 MCP 클라이언트가 바로 그 LLM**이므로, 그쪽이 추론을 수행하며 다음 툴들을
구동합니다:

1. 대화에서 보존할 가치가 있는 원자적 사실을 **추출**합니다.
2. 관련/중복/모순되는 항목을 **`search_memories`**로 찾습니다.
3. **조정(Reconcile)**: `add_memory`(신규) · `update_memory`(정제/병합) ·
   `delete_memory`(폐기).

3단계를 쉽게 하도록 `add_memory`는 가장 가까운 기존 메모리도 함께 반환합니다.
내부적으로 서버는 mem0의 `infer=False` 경로를 사용합니다 — 임베딩 후 그대로 저장 —
따라서 쓰기는 즉각적이고 결정적이며 모델 호출이 없습니다.

---

## 검색 & 튜닝

검색은 **기본이 하이브리드**입니다: 밀집(dense) 벡터 유사도(시맨틱)에 로컬 BM25 렉시컬
신호를 융합해, 패러프레이즈 *와* 정확 식별자(파일 경로, env 변수명, IP, 함수명)가 모두
표면화됩니다. 융합 기본값은 **`rescue`** — dense 랭킹을 유지하고 벡터 모델이 놓친 정확
일치만 *추가*하므로, 좋은 dense 결과를 절대 재정렬하지 않습니다(증명 가능하게 비후퇴;
스토어가 커질수록 이득이 커짐). 더 공격적인 Reciprocal Rank Fusion은 `MEM0_FUSION=rrf`로
사용할 수 있습니다(dense 결과를 재정렬할 수 있으니 먼저 측정하세요). 하이브리드를 끄려면
`MEM0_HYBRID_SEARCH=0`. 추가 의존성 없이 전부 로컬·결정적입니다.

**튜닝 전에 측정하세요.** `server/eval_recall.py`는 *일회용* 스토어에 라벨링된 코퍼스를
만들어 dense vs 하이브리드의 hit@k / MRR을 보고합니다(실제 스토어나 백엔드를 절대 건드리지
않음):

```bash
.venv/bin/python server/eval_recall.py
EVAL_VERBOSE=1 .venv/bin/python server/eval_recall.py   # 쿼리별 첫 적중 순위
```

**기본 임베더는 `intfloat/multilingual-e5-small`** (384차원, ~470MB)입니다. 이곳의
메모리가 한/영 혼용이라 영어 전용 모델은 한국어·교차언어 회상을 놓치기 때문입니다.
*데이터가 있는* 스토어에서 `MEM0_EMBEDDER_MODEL`만 바꾸면 랭킹이 깨지므로(기존 벡터는
옛 모델로 생성), 대신 재임베딩하세요(먼저 백업; 백엔드는 중지한 상태):

```bash
# 예: 더 가벼운 영어 전용 모델로 전환
MEM0_EMBEDDER_MODEL=sentence-transformers/all-MiniLM-L6-v2 MEM0_EMBEDDER_DIMS=384 \
    .venv/bin/python server/migrate_reembed.py
```

> **0.2.0 이전 버전에서 올리는 경우?** 옛 기본값은 `all-MiniLM-L6-v2`였습니다. 업데이트
> 후에는 위 명령으로 (새 기본값 `intfloat/multilingual-e5-small`로) 재임베딩하거나, 백엔드에
> `MEM0_EMBEDDER_MODEL=sentence-transformers/all-MiniLM-L6-v2`를 설정해 옛 모델을 유지하세요.
> 그렇지 않으면 새 쿼리 벡터가 저장된 벡터와 맞지 않아 회상이 무너집니다.

**측정 결과** — 이중 언어 코퍼스(메모리 31개; 한/영 + 교차언어 쿼리 22개;
`server/eval_recall.py`):

| 임베더 (384차원) | 다운로드 | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| `intfloat/multilingual-e5-small` (**기본**) | ~470MB | **0.86** | **1.00** | **1.00** | **0.92** |
| `all-MiniLM-L6-v2` (영어 전용, 더 가벼움) | ~90MB | 0.73 | 0.82 | 0.91 | 0.79 |
| `paraphrase-multilingual-MiniLM-L12-v2` | ~470MB | 0.77 | 0.86 | 0.86 | 0.81 |

셋 다 384차원이라 `MEM0_EMBEDDER_DIMS`는 `384` 그대로입니다. 스토어가 **영어 전용**이고
다운로드를 최소화하려면 위 방식으로 `all-MiniLM-L6-v2`로 재임베딩하세요.

---

## 라이프사이클 (자동 시작/종료)

1. IDE/CLI 실행 → 자식 프로세스로 `server/mem0_proxy.py`(stdio)를 spawn합니다.
2. proxy는 공유 백엔드가 떠 있지 않으면 `launchctl kickstart`로 시작하고, 이후 툴
   호출을 전달하며 주기적으로 keepalive를 보냅니다.
3. 클라이언트를 닫으면 → proxy가 죽고 → warm하게 유지할 것이 없으므로 백엔드는
   `MEM0_IDLE_TIMEOUT`초 뒤 **idle-exit**하며 RAM을 반환합니다. (먼저 진행 중인
   메모리 작업이 끝나기를 기다리므로, 쓰기가 도중에 끊기는 일이 없습니다.)
4. 아무 클라이언트나 다시 열면 → proxy가 백엔드를 다시 시작합니다.

모든 proxy는 **같은** 백엔드로 전달하므로, 여러 클라이언트가 동시에 열려 있어도
Chroma writer는 정확히 하나뿐입니다.

---

## 설정(Configuration)

**백엔드** (`server/mem0_mcp_server.py`; `launchd/com.mem0mcp.server.plist.template`에
설정한 뒤 `install.sh` 재실행, 또는 `install.sh`에 전달):

| 변수 | 기본값 | 설명 |
|-----|---------|-------|
| `MEM0_IDLE_TIMEOUT` | `600` | 백엔드가 종료되기까지의 무활동 시간(초); `0`이면 비활성화 |
| `MEM0_EMBEDDER_MODEL` | `intfloat/multilingual-e5-small` | 로컬 임베더 |
| `MEM0_EMBEDDER_DIMS` | `384` | 모델과 일치해야 함 |
| `MEM0_CHROMA_PATH` | `~/.mem0-mcp/chroma` | 벡터 저장소 위치 |
| `MEM0_COLLECTION` | `mem0` | Chroma 컬렉션 이름 |
| `MEM0_DEFAULT_USER` | `developer_workspace` | 기본 `user_id` |
| `MEM0_RELATED_TOPK` | `3` | `add_memory`가 함께 보여주는 인접 메모리 개수 |
| `MEM0_SEARCH_TOPK` | `10` | `search_memories`가 반환하는 결과 개수 |
| `MEM0_CORE_BUDGET` | `4000` | 고정(코어) 메모리의 총 글자 수 상한; 초과 고정은 거부 |
| `MEM0_CORE_FILE` | `~/.mem0-mcp/CORE_MEMORY.md` | 상시 코어 미러 파일(룰 파일이 읽음) |
| `MEM0_META_FILE` | `~/.mem0-mcp/memory_meta.json` | 사이드카: 고정 상태 + 메모리별 사용 통계 |
| `MEM0_HYBRID_SEARCH` | `1` | 하이브리드 dense+렉시컬 검색; `0`이면 dense 전용 |
| `MEM0_FUSION` | `rescue` | `rescue`(비후퇴) 또는 `rrf`(공격적) |
| `MEM0_RRF_K` | `60` | RRF 상수(`MEM0_FUSION=rrf`일 때만 사용) |
| `MEM0_BM25_MAX_DOCS` | `5000` | 매우 큰 스토어에서 렉시컬 스캔 크기 상한 |
| `MEM0_MCP_PORT` | `8765` | 백엔드 HTTP 포트(proxy와 일치해야 함) |

**프록시** (`server/mem0_proxy.py`; MCP config의 `env` 블록으로 설정):

| 변수 | 기본값 | 설명 |
|-----|---------|-------|
| `MEM0_MCP_PORT` | `8765` | 접속/kickstart할 백엔드 포트 |
| `MEM0_SERVER_LABEL` | `com.mem0mcp.server` | 온디맨드로 시작할 launchd 라벨 |
| `MEM0_PROXY_KEEPALIVE` | `clamp(IDLE/3, 5, 120)` | keepalive 핑 간격(초) |
| `MEM0_BACKEND_READY_TIMEOUT` | `40` | 백엔드가 뜨기를 기다리는 시간(초) |

---

## 설계 이유

- **클라이언트가 곧 지능이다.** 사실을 재추출하려고 *두 번째* 로컬 LLM을 돌리는 것이
  가장 큰 마찰 요인이었습니다(항상 떠 있어야 하고, 비추론 instruct 모델이어야 하며,
  느림). 호출하는 에이전트가 이미 LLM이므로 그것을 완전히 버리고 mem0의 그대로-저장
  경로를 씁니다. (mem0는 내부적으로 여전히 LLM 클라이언트를 만들지만, **절대 호출되지
  않도록** 배선되어 있습니다.)
- **하나의 공유 HTTP 백엔드.** 일반적인 MCP stdio는 클라이언트마다 *별도* 서버를
  spawn합니다 — 여러 클라이언트가 같은 Chroma 저장소를 여러 writer로 열게 되어(락/손상
  위험) 좀비 프로세스로 남을 수 있습니다. 단일 공유 백엔드는 writer가 하나이고 중복이
  없습니다. 그 백엔드 안에서는 단일 전역 락이 **모든** 메모리 작업(읽기와 쓰기)을
  직렬화하므로, 여러 클라이언트의 동시 호출이 서로 끼어들거나 저장소를 손상시킬 수
  없습니다 — 한 번에 하나씩 줄을 서서 실행됩니다. 또한 저장소 디렉터리에 건 OS 수준
  파일락이 단일 writer를 강제합니다: 같은 저장소를 가리키는 두 번째 백엔드는 손상을
  감수하느니 시작을 거부합니다. (여기서는 처리량보다 데이터 손실 안전을 우선합니다;
  메모리 작업은 빠르고 드물어서 이 직렬화는 체감되지 않습니다.)
- **라이프사이클을 위한 per-client stdio proxy.** proxy는 가볍고(임베더/Chroma 없음)
  그 수명이 클라이언트를 따라가므로, 백엔드를 실행 시 시작하고 종료 시 멈출 수 있습니다 —
  맨 HTTP URL로는 제공할 수 없는 온디맨드 동작입니다.
- **Idle 자동 종료로 RAM 반환.** 백엔드는 ~200MB를 점유합니다; 마지막 클라이언트가
  끊기고 잠시 뒤 종료되며 다음 실행 때 다시 시작됩니다.

---

## 개발 (테스트·린트·CI)

서버는 핵심 로직을 따로 테스트하기 쉽도록 작은 모듈로 나뉘어 있습니다:

- `server/mem0_retrieval.py` — 순수 검색 프리미티브(토크나이저, BM25, 랭크 융합).
  표준 라이브러리만 사용 — 임베더/Chroma가 없어 즉시 import됩니다.
- `server/mem0_store.py` — 공용 store/meta/마이그레이션 헬퍼(경로, 원자적 쓰기,
  고정/사용 사이드카, 코어 파일 미러, 백엔드 생존 확인, Chroma 백업/재생성).
  서버가 import하고 마이그레이션 스크립트·뷰어가 재사용합니다.
- `server/mem0_mcp_server.py` — MCP 툴/프롬프트/리소스, 라이프사이클, 그리고 모듈을
  엮는 dense + 하이브리드 검색.

테스트와 린터 실행(개발 도구일 뿐 — 런타임 의존성이 **아니므로** `requirements.txt`에는
넣지 않습니다):

```bash
.venv/bin/python -m pip install pytest ruff
.venv/bin/python -m pytest           # 순수 단위 테스트 + 통합 테스트
.venv/bin/ruff check server tests    # 린트(pyflakes + 정확성 규칙)
```

단위 테스트(`tests/test_retrieval.py`, `test_store.py`, `test_viewer.py`)는 모델이
필요 없어 수 밀리초 만에 끝나고, 통합 테스트(`test_integration.py`)는 일회용 스토어에서
실제 서버를 돌리며 런타임 의존성이 없으면 **자동으로 건너뜁니다**. GitHub Actions
(`.github/workflows/ci.yml`)가 Python 3.10–3.13에서 ruff + pytest를 실행합니다.

**의존성.** `mem0ai`는 서버가 mem0 2.0.4 내부 동작에 의존하므로 정확히 고정
(`==2.0.4`)하고, 나머지(`fastmcp`, `chromadb`, `sentence-transformers`)는 다음 메이저
미만으로 상한을 둔 호환 범위를 씁니다. 의존성을 올릴 때는 먼저 테스트 스위트와
`server/eval_recall.py`를 다시 실행하세요.

---

## FAQ

**메뉴바 토글(과 옛 이름)은 어떻게 됐나요?**
초기 버전에는 메뉴바 on/off 스위치가 있었고 이름은 `mem0-mcp-toggle`이었습니다.
토글은 위의 자동 라이프사이클로 대체되었고, 프로젝트는 `local-mem0-mcp`로 이름이
바뀌었습니다.

**LLM이나 API 키가 필요한가요?** 아니요. 로컬 임베더만 필요하며, 한 번 다운로드된 뒤
오프라인으로 동작합니다.

**"코어 메모리"가 뭔가요?** 일반 메모리는 검색할 때만 표면화되지만, 고정된 *코어*
메모리는 `~/.mem0-mcp/CORE_MEMORY.md`를 통해 **매** 세션 로드됩니다(아래 "코어 메모리"
섹션 참고). 항상 컨텍스트에 두고 싶은 핵심 몇 개에는 `pin_memory`를 쓰세요.

**제 데이터는 어디에 있나요?** `~/.mem0-mcp/chroma`(벡터)와 더불어
`~/.mem0-mcp/CORE_MEMORY.md`(코어 미러), `~/.mem0-mcp/memory_meta.json`(고정 상태 +
사용 통계). 제거(uninstall)해도 유지됩니다.

**여러 클라이언트를 동시에 실행할 수 있나요?** 네 — 모두 하나의 백엔드를 공유합니다
(단일 Chroma writer).

---

## 문제 해결(Troubleshooting)

- **툴이 안 보임 / 클라이언트가 연결 안 됨** → MCP config의 `command`/`args` 경로가
  이 repo의 `.venv/bin/python3`와 `server/mem0_proxy.py`를 가리키는지 확인하세요.
  proxy는 stderr로 로그를 남깁니다(클라이언트의 MCP 로그에서 확인 가능).
- **백엔드가 시작 안 됨** → 에이전트 등록 확인:
  `launchctl print gui/$(id -u)/com.mem0mcp.server`. `~/Library/Logs/mem0-mcp.log`를
  확인하세요. 수동 시작: `launchctl kickstart gui/$(id -u)/com.mem0mcp.server`.
- **로그에 "refusing to start a second Chroma writer"가 보임** → 버그가 아니라 정상입니다:
  다른 백엔드가 이미 저장소의 단일 writer 락(`~/.mem0-mcp/chroma/.writer.lock`)을
  쥐고 있습니다. 한 번에 하나의 백엔드만 쓸 수 있습니다. 이미 떠 있는 것을 쓰거나,
  다른 것을 시작하기 전에 먼저 멈추세요
  (`launchctl kill TERM gui/$(id -u)/com.mem0mcp.server`). (정상 재시작 중에는 새
  백엔드가 옛 것이 종료되는 동안 잠깐 재시도하므로, 백엔드가 실제로 아직 떠 있을 때만
  이 메시지가 지속됩니다.)
- **첫 쓰기가 느림 / 인터넷 필요** → 임베더가 한 번 다운로드된 뒤 오프라인으로 동작합니다.
- **오래된 저장소에서 검색이 이상함** → 코사인 업그레이드 이전에 만들어진 저장소는
  Chroma 기본 L2 거리를 씁니다; 백엔드를 멈춘 상태에서
  `.venv/bin/python server/migrate_cosine.py`를 실행해 코사인으로 전환하세요(임베딩을
  재사용하고, 먼저 백업합니다). 새 설치는 이미 코사인을 사용합니다.
- **지금 당장 RAM 확보** → 클라이언트를 닫거나(idle-exit됨)
  `launchctl kill TERM gui/$(id -u)/com.mem0mcp.server`.
- **로그인한 동안에만 동작** — 부팅 데몬이 아니라 LaunchAgent(사용자별 GUI 세션)입니다.
- **로그:** `~/Library/Logs/mem0-mcp.log`.

---

## 제거(Uninstall)

```bash
./uninstall.sh
```

launchd 백엔드 에이전트(및 레거시 메뉴바 토글)를 제거합니다. 저장된 메모리
(`~/.mem0-mcp/chroma`)와 venv는 유지합니다.

---

## 라이선스

MIT — [LICENSE](LICENSE) 참고. 다음을 기반으로 만들어졌습니다:
[mem0ai/mem0](https://github.com/mem0ai/mem0),
[FastMCP](https://github.com/jlowin/fastmcp),
[Chroma](https://github.com/chroma-core/chroma),
[sentence-transformers](https://github.com/UKPLab/sentence-transformers); 각
프로젝트는 자체 라이선스를 따릅니다.
