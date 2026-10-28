# Cost versus implementation quality

These values are decision estimates, not benchmark measurements. Quality is a relative 0-100 expected suitability score when the model is used for the correct verified-diamond role, receives a complete task packet, and deterministic verification exists. Bands: 90-100 excellent, 80-89 strong, 70-79 usable with verification, under 70 avoid for that role. Latency and usage are relative. Subscription billing is not token-metered per task; API list prices are shown only as a cost/scarcity proxy.

## Current lineup proxy prices (USD per million input/output tokens)
Fable 5.1 $10/$50; Opus 5.5 $4/$20; Sonnet 5.5 $2/$10; Haiku 5.5 from $0.10/$0.50 for prompts up to 100k tokens, with higher long-prompt prices.

## Role-quality matrix by effort

| Model | Effort | Orchestrator | Builder | Mechanical | Reviewer | Relative latency | Relative usage |
|---|---:|---:|---:|---:|---:|---|---|
| Fable 5.1 | low | 88 | 88 | 90 | 88 | high | very high |
| Fable 5.1 | medium | 94 | 93 | 92 | 94 | high | very high |
| Fable 5.1 | high | 97 | 96 | 92 | 97 | very high | extreme |
| Fable 5.1 | xhigh | 98 | 97 | 91 | 98 | very high | extreme |
| Fable 5.1 | max | 98 | 97 | 89 | 99 | highest | extreme |
| Opus 5.5 | low | 86 | 88 | 91 | 87 | medium | high |
| Opus 5.5 | medium | 94 | 94 | 93 | 94 | medium | high |
| Opus 5.5 | high | 97 | 96 | 92 | 97 | medium-high | very high |
| Opus 5.5 | xhigh | 98 | 97 | 91 | 98 | high | very high |
| Opus 5.5 | max | 98 | 97 | 89 | 99 | highest | extreme |
| Sonnet 5.5 | low | 78 | 84 | 92 | 81 | low | medium |
| Sonnet 5.5 | medium | 87 | 92 | 94 | 89 | low | medium |
| Sonnet 5.5 | high | 91 | 95 | 93 | 95 | medium | medium-high |
| Sonnet 5.5 | xhigh | 93 | 96 | 91 | 97 | medium-high | high |
| Sonnet 5.5 | max | 94 | 96 | 89 | 98 | high | very high |
| Haiku 5.5 | low | 58 | 70 | 91 | 67 | lowest | lowest |
| Haiku 5.5 | medium | 65 | 77 | 94 | 74 | lowest | low |
| Haiku 5.5 | high | 70 | 81 | 94 | 80 | low | low-medium |
| Haiku 5.5 | xhigh | 73 | 83 | 93 | 83 | medium | medium |
| Haiku 5.5 | max | 74 | 83 | 91 | 85 | medium-high | medium-high |

## Recommended balance
- Orchestrator: Opus 5.5 medium. Use high for R3 planning or after one failed decomposition. Avoid Fable unless Opus high fails your own evaluations.
- Builder: Sonnet 5.5 medium. Use high for concurrency, migrations, authorization, or complex integration.
- Mechanical: Haiku 5.5 low. Use medium for classification/extraction involving repository nuance; use deterministic code when possible.
- Reviewer: Sonnet 5.5 high. Escalate blocking, uncertain findings to Opus 5.5 high.

## Representative task routing
| Task | Route |
|---|---|
| Small mechanical repository change | program, else Haiku low |
| Nice-to-have cleanup batch (follow-up fast lane) | pre-built packets, no re-planning; deterministic command or Haiku-low mechanical, Sonnet-low builder if judgment needed; affected-scope checks per item, full gates once per batch |
| Bounded backend implementation | Sonnet medium |
| Bounded frontend implementation | Sonnet medium |
| Cross-layer full-stack feature | Opus-medium orchestrator + Sonnet-medium builders + Sonnet-high reviewer |
| Database/schema migration | Opus-high orchestrator + Sonnet-high builder/reviewer |
| Security-sensitive implementation | Opus-high plan + Sonnet-high builder + Opus-high final review |
| Complex architecture | Opus high; Fable high only after eval-based escalation |
| Integration and repair | Opus medium, high after repeated failure |
| Independent code review | Sonnet high; Opus high for blocking ambiguity |

## Interpretation
Increasing effort usually improves edge-case search and self-verification but has diminishing returns and can overthink mechanical tasks. The same effort label is calibrated per model, so scores are not derived from a universal token multiplier. Calibrate these estimates using the included evaluation rubric and your repository telemetry.
