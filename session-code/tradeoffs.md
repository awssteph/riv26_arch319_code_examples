# Cost-optimisation trade-offs

Talking points for the trade-off section of the presentation. Each example is the
same shape: the naive baseline, the optimisation, the trade-off we are accepting,
how to validate it is safe, and how to keep an eye on it afterwards (observability).

Each maps to a concrete change between the two templates in `application/`:

- `ai_app_unoptimized.yaml` - the "before" baseline
- `ai_app_optimized.yaml` - the "after", where every change carries an inline
  `# OPTIMISED:` (what/why) and `# VALIDATE:` (how to confirm) comment

The theme across all of them: **start with a safe, reversible saving, measure the
real access/usage pattern, then go more granular.** Don't optimise blind.

---

## 1. S3 - start with Intelligent-Tiering, not cold storage

**The conversation.** The FinOps person says "put this in a colder class or add
lifecycle policies to move objects to Glacier." That's a valid end state. But the
developer pushes back: we don't yet know the access pattern for these objects
(SOP documents, uploaded images) - some may be read constantly, some never again.
Committing to a lifecycle rule (e.g. "everything to Glacier after 30 days") too
early risks retrieval fees and latency on objects that turn out to be hot.

**The trade-off / optimisation.** Enable **S3 Intelligent-Tiering** on the data
bucket. It automatically moves each object between access tiers based on that
object's own usage, with **no retrieval fees** and no lifecycle rule to guess at.
It is the right default precisely *when the access pattern is unknown*. It is a
small, reversible first step - not the final answer.

**The progression (this is the story).**
1. Turn on Intelligent-Tiering now for an immediate, low-risk saving.
2. Turn on **S3 Storage Lens** to learn the real access patterns per bucket,
   prefix, and object age.
3. Once the data shows a clear pattern, layer in more intricate, per-prefix
   lifecycle rules (e.g. "images older than 90 days -> Archive tier") for deeper
   savings. Now it is data-driven, not a guess.

**Validate before adopting.**
- Intelligent-Tiering has a small per-object monitoring charge; it only pays off
  for objects larger than 128 KB kept longer than 30 days. Confirm the bucket
  holds larger, longer-lived objects (it does: SOP docs + images).
- Very small objects (< 128 KB) are not monitored/moved, so no downside there.

**Observability.** S3 Storage Lens dashboards (free tier gives account-level
metrics; advanced tier adds prefix-level and 15-month history). Use it to justify
the next lifecycle step.

**In the templates.** `EnableS3IntelligentTiering` parameter: `false` -> `true`,
plus the `IntelligentTieringConfigurations` block on `DataBucket`.

---

## 2. DynamoDB - Infrequent Access class + the right capacity mode

**The conversation.** The team has heard of DynamoDB "Infrequent Access" but isn't
sure what it is. Explain it: **DynamoDB Standard-IA** is a table class that cuts
**storage cost ~60%** in exchange for **~25% higher read/write request cost**. So
it is a straight win *only* for tables that are storage-heavy and read/written
infrequently - and a loss for hot tables.

**The trade-off / optimisation.**
- Move cold, storage-heavy tables to **STANDARD_INFREQUENT_ACCESS**. Good
  candidates here: `SESSION-HISTORY`, `CUSTOMER_TRANSACTIONS`, `FEEDBACK`,
  `IMAGES` (they accumulate data but are rarely re-read).
- Keep hot tables on **STANDARD**. `USERROLE` and `TASKLIST` are read on nearly
  every request (role lookups gate everything; task lists load constantly), so IA
  would cost *more*.
- Separately, on **capacity mode**: the baseline is `PAY_PER_REQUEST` (on-demand),
  which is great for spiky/unknown traffic. For tables with steady, predictable
  throughput, switching to **provisioned capacity** and then buying into a
  **savings/capacity plan** (compute savings plans cover provisioned throughput)
  reduces the per-request rate further. This is a second, separate decision from
  table class.

**The key teaching moment: don't apply IA fleet-wide.** The optimised template
demonstrates this - it flips the default to IA for the fleet but *pins the two
hottest tables back to STANDARD* with a per-table override. That contrast is the
point: the saving is per-table, driven by each table's own metrics.

**Validate before adopting (per table).**
- `ConsumedReadCapacityUnits` / `ConsumedWriteCapacityUnits` - sustained high =>
  keep STANDARD; low and spiky => IA is a good fit.
- Table size vs. request volume - large and rarely read => IA wins.
- Note: you can switch table class, but only a limited number of times per day -
  decide deliberately.

**Observability.** CloudWatch DynamoDB metrics (consumed vs. provisioned capacity,
throttled requests) and AWS Cost Explorer grouped by table to see the actual
per-table storage vs. request split before and after.

**In the templates.** `DynamoTableClass` parameter default `STANDARD` ->
`STANDARD_INFREQUENT_ACCESS`, with `USERROLE` and `TASKLIST` overriding back to
`STANDARD` inline.

---

## 3. CloudWatch Logs - own the log group so you control retention and class

**The conversation.** "You haven't specified anything about logging for your
data." The easy reply is: *Amazon will just create the log group for us - we don't
need to set it up.* True, Lambda/EKS auto-create log groups. But if we **define
the log group ourselves**, we get granular control over the things that actually
cost money: **retention** and **log class**.

**The trade-off / optimisation.** Three levers on an explicit log group:
1. **Retention.** The auto-created default is *never expire* - logs pile up
   forever and you pay storage indefinitely. Set a finite retention (e.g. 30
   days). Simplest CloudWatch saving there is.
2. **Log class.** Use the **Infrequent Access** log class for logs you rarely
   query live - it's cheaper on ingestion and storage. This is the
   *environment-dependent* call: **IA is fine for development, but maybe not for a
   production workload**, because the IA class drops features (no Live Tail, no
   metric filters, no subscription filters). So: IA for DEV, STANDARD for PROD.
3. **Log level / volume.** Emitting `DEBUG` in normal operation is high-volume and
   you pay per GB ingested. Default to `INFO` and raise to `DEBUG` only while
   actively debugging.

**Validate before adopting.**
- Retention: confirm the chosen value meets any audit/compliance requirement. If
  longer retention is mandated, keep it finite but higher, and/or export to S3 +
  lifecycle for cheap long-term storage.
- IA log class: confirm the environment doesn't rely on Live Tail, metric filters,
  or subscription filters. Production usually does - keep it STANDARD there.
- Log level: confirm `INFO` still gives on-call enough to triage.

**Observability.** This *is* the observability lever - but manage its own cost via
the CloudWatch Logs `IncomingBytes` metric per log group to see ingestion volume,
and Cost Explorer for CloudWatch spend trend after the change.

**In the templates.** `DemoFunction` + explicit `DemoFunctionLogGroup`:
`RetentionInDays` never-expire -> 30, `LogGroupClass` STANDARD -> IA for DEV
(STANDARD for PROD via the `IsProd` condition), `LogLevel` DEBUG -> INFO.

---

## 4. Graviton - cheaper ARM compute, once you've checked two things

**The conversation.** Move compute (EKS worker nodes, and the demo Lambda) from
x86 to **AWS Graviton (ARM)**. Graviton is roughly **20% cheaper** for equal or
better performance. But before we do it we check **two things**:

1. **Is the software/engine version high enough to support ARM?** The container
   image and all its dependencies must build and run on `arm64` (base image, every
   pip wheel, any native libs). For the demo backend this is pure Python + boto3,
   which is arm64-native, so it's safe. Compiled/native deps are where this bites.
2. **Multithreading / right-sizing headroom.** Because the workload's threading
   profile lets us, we can often **reduce the instance size** at the same time -
   the move to Graviton is a natural moment to right-size, not just re-architect.
   The backend is largely single-threaded per request, so a smaller shape may
   suffice.

**The trade-off / optimisation.**
- EKS nodes: `t3.medium` (x86) -> `t4g.medium` (Graviton), and the node **AMI must
  switch to ARM** in lockstep (`AL2023_ARM_64_STANDARD`) or nodes won't join.
- Lambda: add `Architectures: [arm64]`.
- SPOT capacity is a *further* saving on nodes but a separate, riskier step (needs
  interruption tolerance) - keep it deliberate, not bundled in.

**Validate before adopting.**
- Rebuild the image with `--platform=linux/arm64` and confirm it runs.
- Load-test, then check utilisation before downsizing (see observability).
- The "validate later on SageMaker" note: if any ML/inference sizing is in play,
  validate the right-sized shape against real inference load before committing.
- Confirm the Graviton instance type is available in all target AZs.

**Observability.** CloudWatch **Container Insights** for the EKS nodes
(`node_cpu_utilization`, `node_memory_utilization`) - confirm p90 CPU is
comfortably below ~60% before downsizing further. For Lambda, use **AWS Lambda
Power Tuning** to find the memory/architecture cost-performance sweet spot from
real invocation data (more memory is *not* automatically cheaper).

**In the templates.** `NodeInstanceType` `t3.medium` -> `t4g.medium`,
`NodeAmiType` -> `AL2023_ARM_64_STANDARD`; `DemoFunction` gains
`Architectures: [arm64]` and a right-sized `MemorySize`.

---

## 5. Bedrock - prompt optimisation to cut token spend

**The conversation.** Bedrock cost is driven by **input + output tokens**. The
lever is **prompt optimisation**: tightening system prompts and context so we send
fewer input tokens and generate fewer output tokens for the same result.

**The trade-off / optimisation.**
- Use Bedrock's **prompt optimisation** capability to rewrite/condense prompts.
  Check whether there's a CLI path to kick it off (vs. console-only) and compare
  results before/after.
- Trade-off to watch: over-trimming a prompt can reduce answer quality or
  grounding. Optimise, then re-check output quality against the guardrails and the
  RAG grounding, not just the token count.

**Validate before adopting.**
- A/B the optimised prompt vs. the original on a fixed set of representative
  questions; compare token counts *and* answer quality/grounding.
- Confirm the guardrail contextual-grounding and relevance thresholds still pass.

**Observability.** Track token usage in **CloudWatch** (Bedrock invocation
metrics / model invocation logging) and Cost Explorer for Bedrock spend, so the
before/after token delta is visible. This is the usage-tracking hook for Bedrock.

**In the templates.** Represented by the `BedrockModelId` parameter and the KB /
Guardrail wiring; prompt content lives in the backend, so this lever is largely a
runtime/process change rather than a CloudFormation diff.

---

## Cross-cutting: observability so each saving is manageable

Every optimisation above pairs with a way to *watch* it, so the team has something
to manage rather than a one-off change:

| Optimisation | Watch it with |
|--------------|---------------|
| S3 Intelligent-Tiering | S3 Storage Lens (access patterns, tier distribution) |
| DynamoDB IA / capacity | CloudWatch consumed-capacity metrics + Cost Explorer per table |
| CloudWatch log tuning | Log group `IncomingBytes` + CloudWatch spend trend |
| Graviton nodes | CloudWatch Container Insights (CPU/mem utilisation) |
| Graviton / Lambda sizing | AWS Lambda Power Tuning |
| Bedrock prompts | Bedrock invocation metrics / model invocation logging + Cost Explorer |

The pattern to leave the audience with: **enable a safe saving, turn on the
matching observability, learn the real pattern, then go more granular.**
