# AnyCompanyAssist — CloudFormation (architecture / cost-optimisation demo)

Single-file CloudFormation conversion of the CDK guidance in
`../example-ai-application`, built for an **architecture and cost trade-off talk**.
It is an architecture artefact — **not intended to be deployed live**.

This is the **un-optimised baseline**. The optimisation levers from
[`../tradeoffs.md`](../tradeoffs.md) are deliberately left naive so an optimised
version can show a clear before/after delta.

## Files

| File | Purpose | Deploy order |
|------|---------|--------------|
| `networking.yaml` | VPC, 3 public + 3 private subnets, 1 NAT gateway, route tables. Exports values via `Fn::ImportValue`. | **1st** |
| `ai_app_unoptimized.yaml` | The application, un-optimised baseline ("before"). Imports networking exports. | **2nd** |
| `ai_app_optimized.yaml` | Same application with the tradeoffs.md levers applied ("after"). Every change carries a `# OPTIMISED:` and `# VALIDATE:` comment. Diff it against the baseline. | **2nd (alt)** |
| `ai_app.yaml` | Pre-existing unrelated workshop template (EC2/Lambda/Aurora/Bedrock). Left as-is. | — |

`networking.yaml` is split out on purpose: it is essential, not edited during the
talk, and deployed once ahead of time. That keeps the main file focused on the
resources you actually tune.

## Deploy order (reference only — we do not deploy this)

```bash
# 1. Networking first (StackName must match the app stack)
aws cloudformation deploy \
  --template-file networking.yaml \
  --stack-name retail-networking \
  --parameter-overrides StackName=retail

# 2. Application (imports the networking exports)
aws cloudformation deploy \
  --template-file ai_app_unoptimized.yaml \
  --stack-name retail-app \
  --parameter-overrides StackName=retail Environment=DEV EmailAddress=you@example.com \
  --capabilities CAPABILITY_NAMED_IAM
```

Validate without deploying:

```bash
cfn-lint networking.yaml ai_app_unoptimized.yaml
```

## Structure of `ai_app_unoptimized.yaml`

The **five focus resources** are hoisted to the **top** of the `Resources`
section under a `FOCUS RESOURCES` banner, in this order:

1. **S3** — access-logs, website, and data buckets (defined first because the
   others depend on the data bucket)
2. **DynamoDB** — the 11 application tables
3. **Containers** — ECR repo, EKS cluster, managed node group, Pod Identity add-on
4. **OpenSearch Serverless** — encryption/network/data-access policies, collection, index
5. **Bedrock** — Knowledge Base role, Knowledge Base, data source, Guardrail

Everything below the `ESSENTIAL GLUE` banner (Cognito, backend workload IAM role,
CloudFront, synthetic-data seeder, demo Lambda) is not part of the edit surface.

Their tunable knobs are exposed as **Parameters** at the top of the file and
grouped in the console via `Metadata → AWS::CloudFormation::Interface`.

## Parameters → trade-off mapping

| Focus | Key parameters | Un-optimised baseline | Optimisation lever (tradeoffs.md) |
|-------|----------------|-----------------------|-----------------------------------|
| Containers | `NodeInstanceType`, `NodeAmiType`, `NodeCapacityType` | `t3.medium`, x86, `ON_DEMAND` | Graviton (`t4g`/`m7g` + ARM AMI), right-size after checking multithreading, track in CloudWatch |
| DynamoDB | `DynamoBillingMode`, `DynamoTableClass` | `PAY_PER_REQUEST`, `STANDARD` | Infrequent-access table class; provisioned + capacity/savings plans for steady tables |
| S3 | `EnableS3IntelligentTiering` | `false` (S3 Standard, no lifecycle) | Intelligent-Tiering, then Storage Lens to learn access patterns before prefix rules |
| Bedrock | `BedrockModelId` | naive prompting | Prompt optimisation to cut input/output tokens |
| OpenSearch | `OpenSearchStandbyReplicas` | `ENABLED` (redundant OCUs, ~$350/mo) | Disable standby replicas for non-prod to ~halve OCUs |
| CloudWatch logs | `DemoLogRetentionDays`, `LogLevel` | `0` = never expire, `DEBUG` | Finite retention; IA log class for DEV (`IsProd` seam); lower to INFO/WARN |
| Demo Lambda | `DemoFunction` `Architectures`, `MemorySize` | x86_64 (implicit), 128 MB | Graviton `arm64`; right-size memory via Lambda Power Tuning |

## Optimised version and how to read it

`ai_app_optimized.yaml` is a copy of the baseline with the levers applied. To
drive the talk, diff the two files:

```bash
diff ai_app_unoptimized.yaml ai_app_optimized.yaml
```

Every optimisation is annotated in place:

- `# OPTIMISED:` — what changed and why (the saving / benefit).
- `# VALIDATE:` — how to confirm the change is safe **before** adopting it (the
  metric to check, the compatibility caveat, or the SLA to confirm).

Changes applied in the optimised file:

| Area | Change | Key thing to validate |
|------|--------|-----------------------|
| Containers | `t3.medium`/x86 → `t4g.medium`/ARM AMI | arm64 image builds; CPU/mem headroom in Container Insights; t4g in all AZs |
| DynamoDB | fleet → IA class, **but** USERROLE + TASKLIST pinned STANDARD | per-table read volume in CloudWatch (hot tables stay STANDARD) |
| S3 | Intelligent-Tiering ON | object size > 128 KB & lifetime > 30d; review with Storage Lens |
| OpenSearch | standby replicas `DISABLED` | availability SLA (esp. PROD); changing it replaces the collection |
| CloudWatch logs | retention 30d, DEV log class IA, level INFO | IA class has no Live Tail/metric filters; audit retention needs |
| Demo Lambda | `arm64` + memory 128→256 MB | arm64 dep compatibility; right-size with Lambda Power Tuning |

Note the DynamoDB **per-table override**: applying IA fleet-wide is wrong, so the
optimised file demonstrates keeping the two hottest tables on STANDARD while the
rest move to IA — the concrete "validate per-table" payoff.

## CloudWatch logging talking point

`tradeoffs.md` notes that AWS auto-creates a log group if you specify nothing —
convenient, but you lose control of retention and class. The demo makes the
opposite choice: `DemoFunctionLogGroup` is an **explicit** `AWS::Logs::LogGroup`
attached to a small `DemoFunction` Lambda, so retention (`RetentionInDays`) and
class (`LogGroupClass`) are yours to tune. Baseline = never-expire + STANDARD;
the `IsProd` condition is the seam where DEV switches to `INFREQUENT_ACCESS`.

## Known simplifications (architecture artefact, not a live deploy)

- **EKS in-cluster workload** is out of scope for raw CloudFormation, exactly as
  in the CDK: the namespace, `aws-auth`, ALB controller (Helm), and the backend
  `Deployment`/`Service`/`Ingress` are applied post-stack by `deploy.sh` with
  `kubectl`/`helm`. The template provisions the cluster, node group, ECR, and
  Pod Identity association.
- **Synthetic-data seeder** (`SyntheticDataFunction`) is a wiring **stub**. The
  full ~700-line seeding logic lives in the CDK source
  (`deployment/cdk/lib/synthetic-data-stack.ts`).
- The **Docker image** referenced by the backend is built and pushed to ECR by
  `deploy.sh`, not by CloudFormation.
