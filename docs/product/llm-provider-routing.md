# LLM Provider Routing & Hybrid Model Strategy

Updated: 2026-11-06

## 운영 OpenAI 연결 (2026-11-06 사용자 승인)

사용자가 제공한 새 OpenAI 비밀 키로 공식 모델 조회와 비민감 합성 연결 문장의 실제
응답을 확인했다. 운영 API의 `OPENAI_API_KEY`는 기존 Google Secret Manager의
전용 `realytics-openai-key` 버전 1을 참조한다. 도쿄 한 지역에 저장하며 API 서비스
계정에만 이 항목의 Secret Accessor를 연결했다. 키 값은 코드·문서·브라우저·로그에
넣지 않는다. 전용 시크릿 1개의 생성·연결과 무료 할당량 초과 비용 가능성은 사용자가
별도로 승인했다. 새 서버나 DB 스키마는 만들지 않는다.

운영 환경은 `REALYTICS_AI_PROVIDER=openai`, `OPENAI_MODEL=gpt-4.1-mini`로
선택한다. 코드 자체의 기본값과 사용자별 명시적 제공자·모델 설정은 유지한다.
기존 LLM 호출이 연결된 문서 해석·조정·설명 단계에서 이 모델을 사용하며, 수치 계산과
교차검사·입력 근거·사람 승인 절차는 기존 결정론적 경로를 따른다. 키 연결만으로
미구현 데이터 연결이나 미지원 금융 조건을 완료로 취급하지 않는다.

기존 제공자·사용자 프로필 회귀 29개를 통과했고, 같은 API 이미지의
`realytics-api-00053-soz`에 비밀 참조·제공자·모델 환경을 반영했다. readiness와
기동 점검을 통과한 리비전에 운영 트래픽 100%를 연결했다. 합성 인증 시험에는
실제 사업자료를 보내지 않았다. 사용자별 개인 LLM 설정은 계정 화면에서 따로 확인한다.

REALYTICS handles sensitive PF data (loan amounts, owner names, accounts, PNU, registry) and Korean-language documents under finance/public-sector compliance. LLM use must therefore be **routed by sensitivity, difficulty, and residency**, not fixed to one vendor.

> Note: model version numbers and prices change frequently. Confirm current versions/limits/pricing on each vendor's page before production rollout.

## 1. Multi-provider adapter (implemented)

`services/api-server/agents/ai_connection.py` calls LLMs through the **OpenAI Chat Completions-compatible** interface, so one code path reaches many providers by overriding `base_url`. Anthropic uses its native SDK; Claude Desktop stays host-LLM only.

| Provider (`REALYTICS_AI_PROVIDER`) | Runtime | API key env | Default model | base_url |
|---|---|---|---|---|
| `openai` (aliases: gpt, chatgpt) | native OpenAI | `OPENAI_API_KEY` | `gpt-4o` | default |
| `anthropic` (claude) | native Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | default |
| `claude_desktop` (mcp_host) | MCP host LLM | — | host | — |
| `gemini` (google) | OpenAI-compat | `GEMINI_API_KEY` | `gemini-2.5-flash` | Google AI Studio OpenAI endpoint |
| `groq` | OpenAI-compat | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | api.groq.com/openai/v1 |
| `openrouter` | OpenAI-compat | `OPENROUTER_API_KEY` | llama-3.3-70b-instruct | openrouter.ai/api/v1 |
| `deepseek` | OpenAI-compat | `DEEPSEEK_API_KEY` | `deepseek-chat` | api.deepseek.com |
| `together` | OpenAI-compat | `TOGETHER_API_KEY` | Llama-3.3-70B | api.together.xyz/v1 |
| `local` (ollama, vllm, self_hosted) | OpenAI-compat | `REALYTICS_LLM_API_KEY` (optional) | `qwen2.5` | `REALYTICS_LLM_BASE_URL` |

Per-provider model override env: `<PROVIDER>_MODEL` (e.g. `GEMINI_MODEL`, `GROQ_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`); local uses `REALYTICS_LLM_MODEL`. All calls run at `temperature=0`; JSON mode is used where the provider supports it.

## 2. Hybrid routing policy

| Tier | When | Recommended target |
|---|---|---|
| **Sensitive** (PF amounts, owner/account/PNU/registry, internal review docs) | Any data that must not leave the network | **Self-hosted open model** (`local`: Ollama/vLLM running DeepSeek/Qwen/Llama) or **Korean domestic LLM** (HyperCLOVA X / EXAONE) for residency |
| **Non-sensitive / high-volume** (generic extraction, classification, routing) | Bulk, low-risk text | Cloud **cheap tier**: Gemini Flash / GPT mini / Groq (free tier ok) |
| **High-stakes judgment** (credit opinion, legal/permit interpretation, hard feasibility) | Decisions affecting approval, with human gate | **Top tier**: Claude Opus 4.8 / GPT o-series / Gemini 2.5 Pro / DeepSeek-R1 |

Rules: every LLM judgment must (1) run at `temperature=0`, (2) cite evidence and link into the Evidence Graph, and (3) pass a human review gate before it affects approval. Deterministic calculators (feasibility, cash flow, PF metrics, scenario, stress, credit enhancement) stay rule-based for auditability — LLMs interpret and narrate, they do not replace the math.

## 3. Model recommendations by use case (cross-provider)

| Use case | Claude | OpenAI | Gemini | xAI | Open / free |
|---|---|---|---|---|---|
| Document parsing/extraction (current LLM use) | Sonnet 4.6 | GPT-4.1 / GPT-5 | **Gemini 2.5 Flash** (cheap) / Pro (long docs) | Grok-3 | **DeepSeek-V3**, **Qwen3** |
| Hard/ambiguous docs, complex feasibility | Opus 4.8 | o-series (o3/o4) | Gemini 2.5 Pro | Grok-4 | **DeepSeek-R1** |
| Credit/legal judgment & memo (high-stakes) | Opus 4.8 | GPT-5 / o3 | Gemini 2.5 Pro | Grok-4 | DeepSeek-R1 + human gate |
| Planner intent classification / routing | Haiku 4.5 | GPT mini | Gemini Flash-Lite | Grok-3 mini | Groq+Llama 3.3, small Qwen |

## 4. Free / low-cost options

- **Free API tiers**: Google AI Studio (Gemini), Groq, OpenRouter free slots, Mistral/Cohere trials. Fastest to prototype.
- **Self-hosted open weights (compute-only cost)**: Llama 3.3/4, DeepSeek V3/R1, Qwen3, Gemma 3, Mistral — via Ollama/vLLM. **Best fit for the deck's "Private AI / 회사 노하우 학습"** because data never leaves the network.
- **Korean-specialised**: HyperCLOVA X (Naver), EXAONE (LG AI) — strong Korean + domestic data residency for finance/public-sector PoCs.

## 5. Configuration examples

```bash
# Free Gemini (prototype)
REALYTICS_AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash

# Free/fast Groq with an open model
REALYTICS_AI_PROVIDER=groq
GROQ_API_KEY=...

# Sensitive / on-prem (no data leaves the network)
REALYTICS_AI_PROVIDER=local
REALYTICS_LLM_BASE_URL=http://localhost:11434/v1   # Ollama
REALYTICS_LLM_MODEL=qwen2.5:14b
```

## 6. Open follow-ups

- Per-step tiered routing: `resolve_ai_context` currently resolves one provider per request. A routing layer that picks tier (sensitive/cheap/high-stakes) per orchestration step is future work.
- Native adapters for Korean LLMs (HyperCLOVA X / EXAONE) if their APIs are not OpenAI-compatible.
- LLM judgment layer (Planner intent, credit/legal narrative) with evidence + human gate — see `docs/product/ir-deck-implementation-gap.md`.
