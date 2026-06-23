# LLM Provider Routing & Hybrid Model Strategy

Updated: 2026-06-20

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
