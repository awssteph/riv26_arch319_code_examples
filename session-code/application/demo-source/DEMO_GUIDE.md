# Cost-optimisation demo guide

A walkthrough for the trade-off section of the talk. For each lever: **what to
change**, **what to say**, and **why that specific resource is a safe/sensible
one to change**. The recurring theme: *enable a safe saving, turn on the
matching observability, learn the real pattern, then go more granular. Don't
optimise blind, and don't apply a lever fleet-wide.*

## How to use these files

The `demo/` folder has one small before/after snippet per lever, numbered in
presentation order:

| # | Snippet file | Lever |
|---|--------------|-------|
| 1 | `01_s3_intelligent_tiering.yaml` | S3 Intelligent-Tiering (two-bucket contrast) |
| 2 | `02_containers_graviton.yaml` | Graviton (ARM) EKS nodes + Lambda |
| 3 | `03_dynamodb_table_class.yaml` | DynamoDB IA vs STANDARD + capacity mode |
| 4 | `04_opensearch_standby_replicas.yaml` | OpenSearch Serverless standby replicas |
| 5 | `05_cloudwatch_logs.yaml` | CloudWatch Logs retention / class / level |
| 6 | `06_bedrock_connection.py` | Where EKS connects to Bedrock: model selection + prompt (cost levers) |

Each snippet is a **teaching artefact**, trimmed to just the resource(s) that
make the point. They are **not deployed on their own** — the deployable source
of truth is `../ai_app_unoptimized.yaml` (before) and `../ai_app_optimized.yaml`
(after). See the sync caveat at the bottom.

Full narrative background for every lever is in `../../tradeoffs.md`.

---

## 1. S3 Intelligent-Tiering — and why two buckets, not one

**What to change.** On `DataBucket`, add an `IntelligentTieringConfigurations`
block (see snippet 1 `DataBucket_AFTER`). Leave `WebsiteBucket` untouched.

**What to say.** Intelligent-Tiering auto-moves each object between access tiers
based on that object's own usage, with **no retrieval fees** and no lifecycle
rule to guess at. It's the right default *when the access pattern is unknown* —
a small, reversible first step, not the final answer. The progression is: turn
it on now → turn on **S3 Storage Lens** to learn real access patterns → later
layer in per-prefix lifecycle rules (e.g. images > 90 days → Archive) once the
data justifies it.

**Why these buckets — the contrast is the lesson.**

- **`DataBucket` → turn IT ON.** Holds SOP documents and uploaded images. Access
  is mixed and unknown, objects are large-ish and long-lived, and many are read
  once or never again. That's the textbook Intelligent-Tiering case.
- **`WebsiteBucket` → leave it OFF.** Holds the static frontend (HTML/JS/CSS)
  served through CloudFront. Objects are small and hot. Two reasons IT is the
  wrong call here: objects **< 128 KB aren't monitored or moved** at all, and
  hot objects **never leave the frequent tier** — so you'd pay the per-object
  monitoring charge for zero tiering benefit.

The point to land: **the saving is per-bucket and access-pattern-driven.** "Turn
on Intelligent-Tiering" is not a blanket switch — you enable it where the data is
cold/unknown and skip it where the data is hot and small.

**Validate before adopting.** Confirm the bucket holds objects > 128 KB kept
> 30 days (DataBucket does). Very small objects aren't monitored, so no downside
there either.

**Observability.** S3 Storage Lens (access patterns, tier distribution).

---

## 2. Graviton — cheaper ARM compute, after two checks

**What to change.** EKS node group: `t3.large` → `t4g.medium`, and switch the
node AMI to ARM (`AL2023_ARM_64_STANDARD`) **in lockstep** (snippet 2). The demo
Lambda in the same snippet gains `Architectures: [arm64]` and a right-sized
`MemorySize` — the same lever applied to both compute types.

**What to say.** Graviton (ARM) is roughly **20% cheaper** for equal or better
performance. Before flipping it, check two things:
1. **Does the software run on arm64?** Every base image, pip wheel, and native
   lib must build for aarch64. Our backend is pure Python + boto3 (arm64-native),
   so it's safe. Compiled/native deps are where this bites.
2. **Right-sizing headroom.** The move to ARM is a natural moment to right-size.
   The backend is largely single-threaded per request, so a smaller shape may
   suffice — but confirm with utilisation data first.

**Why this resource is safe to change.** The EKS worker nodes run a stateless
Python container; nothing about it is x86-specific. The AMI-arch/instance-arch
lockstep is the one hard rule — mismatch and the nodes won't join the cluster.
The Lambda is pure Python + boto3, so arm64 is a drop-in; it has no AMI, you just
set `Architectures: [arm64]`.

**Validate before adopting.** Rebuild with `docker build --platform=linux/arm64`
and confirm it runs. Load-test, then check Container Insights
`node_cpu_utilization` / `node_memory_utilization` (p90 CPU comfortably below
~60%) before downsizing. Confirm t4g is available in all target AZs. For the
Lambda, don't assume more memory is cheaper — use AWS Lambda Power Tuning. SPOT
is a *further* saving but a separate, riskier step — don't bundle it in.

**Observability.** CloudWatch Container Insights for nodes; AWS Lambda Power
Tuning for the Lambda memory/architecture sweet spot.

---

## 3. DynamoDB — IA for cold tables, STANDARD for hot, and don't do it fleet-wide

**What to change.** Set the fleet default table class to
`STANDARD_INFREQUENT_ACCESS`, then **pin the hot tables back to `STANDARD`** with
a per-table override (see snippet 3: `UserRoleTable_AFTER` stays STANDARD,
`SessionHistoryTable_AFTER` moves to IA).

**What to say.** DynamoDB **Standard-IA** is a table class that cuts **storage
cost ~60%** in exchange for **~25% higher read/write request cost**. So it's a
straight win *only* for tables that are storage-heavy and read/written
infrequently — and a *loss* for hot tables. The key teaching moment: **don't
apply IA fleet-wide.** The saving is per-table, driven by each table's own
metrics.

**Why each table suits its class.**

| Table | Class | Why |
|---|---|---|
| `USERROLE` | **STANDARD** (hot) | Read on nearly every request — role lookups gate task/schedule access. High request volume, little storage → IA would cost *more*. |
| `TASKLIST` | **STANDARD** (hot) | Task lists load constantly on the main screens. Same reasoning as USERROLE. |
| `SESSION-HISTORY` | **IA** (cold) | Accumulates conversation rows that are almost never re-read. Storage-heavy, low reads → IA wins. |
| `CUSTOMER_TRANSACTIONS` | **IA** (cold) | Append-heavy log of transactions, rarely queried back. |
| `FEEDBACK` | **IA** (cold) | Write-once feedback records, rarely read. |
| `IMAGES` | **IA** (cold) | Image metadata that piles up and is seldom re-fetched. |
| `CUSTOMER`, `PRODUCTS`, `SCHEDULE`, `TIMEOFF`, `DAILY_TASKS_BY_DAY` | **IA** by default | Low/moderate access; fine as IA unless CloudWatch metrics show sustained reads. |

**The separate capacity-mode decision.** Independent of table class, the baseline
uses `PAY_PER_REQUEST` (on-demand) — great for spiky/unknown traffic. For a table
with **steady, predictable** throughput (e.g. `PRODUCTS`), switching to
**PROVISIONED** capacity and buying a **capacity/savings plan** lowers the
per-request rate further (see snippet 3 `ProductsTable_CAPACITY_EXAMPLE`). Keep
this distinct from the class choice — they're two separate levers on the same
table.

**Validate before adopting (per table).** Check CloudWatch
`ConsumedRead/WriteCapacityUnits`: sustained/high → keep STANDARD; low and spiky
→ IA fits. Weigh table size vs request volume (large + rarely read → IA wins).
Note you can only switch table class a limited number of times per day, so decide
deliberately.

**Observability.** CloudWatch DynamoDB metrics + Cost Explorer grouped by table
to see the per-table storage-vs-request split before and after.

---

## 4. OpenSearch Serverless — disable standby replicas (biggest single saving)

**What to change.** Collection `StandbyReplicas: ENABLED` → `DISABLED` (snippet 4).

**What to say.** AOSS bills in **OCUs**. Standby replicas keep a redundant set of
OCUs warm in a second AZ for faster failover; disabling them roughly **halves**
the OCU count. OCUs are the **single biggest line item** in this stack
(~$350/mo), so this is the **largest single saving** in the demo.

**Why it's OK here (and the caveat).** We're deploying a DEV/non-prod demo, so
trading AZ-level redundancy for cost is acceptable. For **PROD**, confirm the
availability SLA tolerates it first. **Gotcha to call out:** `StandbyReplicas` is
set at collection creation — changing it later **replaces the collection** (data
loss + KB re-index), so decide before the first deploy.

**Observability.** OCU usage in CloudWatch; Cost Explorer for the AOSS line.

---

## 5. CloudWatch Logs — own the log group to control retention, class, and volume

**What to change.** Keep the **explicit** `DemoFunctionLogGroup` (instead of
letting Lambda auto-create one), then set finite `RetentionInDays: 30`, use the
`INFREQUENT_ACCESS` class for DEV (STANDARD for PROD via the `IsProd` condition),
and default the app `LOG_LEVEL` to `INFO` (snippet 5).

**What to say.** "You didn't set up logging — Amazon just makes the log group for
us." True, but if you **define it yourself** you control the three things that
cost money: **retention** (auto-default is *never expire* — logs pile up
forever), **log class** (IA is cheaper for logs you rarely query live), and
**volume** (DEBUG is high-volume; you pay per GB ingested).

**Why the DEV/PROD split matters.** The IA log class **drops features** — no Live
Tail, no metric filters, no subscription filters. That's fine for DEV but often
not for PROD, which is exactly why the class is gated on `IsProd`. This is the
"IA is fine for development, maybe not for production" nuance.

**Validate before adopting.** Confirm 30-day retention meets audit/compliance
needs (if longer is mandated, keep it finite-but-higher and/or export to S3 +
lifecycle). Confirm DEV doesn't rely on Live Tail / metric filters before using
IA. Confirm INFO still gives on-call enough to triage.

**Observability.** This *is* the observability lever — manage its own cost via the
log group `IncomingBytes` metric and the CloudWatch spend trend.

---

## 6. Bedrock — where EKS connects, and the real token levers

Unlike the others, this snippet is **backend code** (`06_bedrock_connection.py`),
not a CloudFormation diff — because the Bedrock cost levers for this app live in
the code the EKS pods run, not in the template.

**How EKS reaches Bedrock.** The backend pod gets credentials via **EKS Pod
Identity** — `BackendPodIdentityAssociation` binds the `${StackName}-backend-sa`
service account to `BackendServiceAccountRole`, whose `BackendBedrockAccess`
policy grants `bedrock:InvokeModel` / `InvokeModelWithResponseStream` /
`ApplyGuardrail` / `Retrieve`. No keys in code: the pod → Pod Identity → IAM
role → `bedrock-runtime` / `bedrock-agent-runtime`.

**How the app actually uses Bedrock (three ways, three levers).**

1. **Nova Sonic speech-to-speech** (`s2s_session_manager.py`) — the primary use.
   A bidirectional audio stream to `amazon.nova-2-sonic-v1:0`. Billed on audio
   in/out tokens — the dominant cost. The big lever is the **system prompt**: a
   ~70-line block shipped on *every* session. Trimming it cuts input tokens on
   every conversation.
2. **Claude text chat** (`restapi.py`) — reads `BEDROCK_MODEL_ID` (the one knob
   the template exposes via the `BedrockModelId` parameter) and attaches a
   **Guardrail** on each call. Lever: right-size the model. The Guardrail is a
   safety/quality control, *not* a cost knob — keep it on.
3. **Knowledge Base retrieval / RAG** (`restapi.py :: kb_search`) — pulls
   `numberOfResults = 3` SOP chunks into the prompt per call. Lever: tune
   `numberOfResults` so you don't over-stuff the context.

**What to say.** Bedrock cost is driven by **input + output tokens**. Show the
three call sites in the snippet and point out that the biggest wins here — the
system prompt size and `numberOfResults` — are **runtime/code changes, not
CloudFormation**. Good teaching point: *not every optimisation is an
infrastructure change.* Only the Claude text model is wired to a template
parameter.

**The trade-off.** Over-trimming the prompt or dropping `numberOfResults` too
low can hurt answer quality or grounding. Optimise, then re-check against the
guardrail grounding/relevance thresholds, not just the token count.

**Validate before adopting.** A/B the trimmed prompt (and lower `numberOfResults`)
vs the original on a fixed question set; compare token counts *and* answer
quality / tool-calling accuracy / grounding.

**Observability.** Bedrock invocation metrics / model invocation logging +
Cost Explorer for the token delta.

---

## Cross-cutting: pair every saving with observability

| Lever | Watch it with |
|---|---|
| S3 Intelligent-Tiering | S3 Storage Lens |
| DynamoDB IA / capacity | CloudWatch consumed-capacity + Cost Explorer per table |
| CloudWatch log tuning | Log group `IncomingBytes` + CloudWatch spend trend |
| Graviton nodes | CloudWatch Container Insights (CPU/mem) |
| Graviton / Lambda sizing | AWS Lambda Power Tuning |
| Bedrock prompts | Bedrock invocation metrics + Cost Explorer |

Leave the audience with the pattern: **enable a safe saving, turn on the matching
observability, learn the real pattern, then go more granular.**

---

## Sync caveat (revisit later)

> These `demo/` snippets are **hand-trimmed copies** of resources that also live
> in `../ai_app_unoptimized.yaml` and `../ai_app_optimized.yaml`. They are **not**
> the deployable source of truth and are **not** wired into `deploy-test.sh`.
>
> That means: **if you change a resource in the main templates, the matching
> snippet here will drift** unless you update it too. For now, treat the main
> templates as authoritative and update the snippet by hand when a lever changes.
> If a commit touches a focus resource, mention the matching `demo/` snippet in
> the commit message so the drift is easy to catch in review.
>
> We deliberately parked the bigger decision (splitting the app into independent,
> testable nested stacks vs. keeping the monolith + snippets) — see the chat
> thread. Come back to this once the live-session format is locked.
