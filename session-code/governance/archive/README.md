# Service control policies (org altitude)

Preventive guardrails for the ARC319 governance segment. A control belongs here only if
the answer is the same for every workload in the org. Anything that varies per workload
belongs at resource altitude, in the template.

JSON carries no comments, so the narrative role, the condition key, and the known gap for
each policy are documented here.

| File | Role in the talk |
|------|------------------|
| `scp-require-cost-allocation-tags.json` | The attribution foundation. Nothing downstream works without it. |
| `scp-deny-cost-tag-removal.json` | Attribution has to survive after create. |
| `scp-deny-regions-outside-approved.json` | Bound the footprint you can monitor and commit against. |
| `scp-deny-accelerated-and-oversized-compute.json` | Encode an architectural decision as a constraint. |
| `scp-deny-cost-guardrail-tampering.json` | Protect the controls themselves. |

---

## 1. Require cost allocation tags

Denies create actions when any of `cost-center`, `application`, or `environment` is absent
from the request, and denies `environment` values outside a controlled vocabulary. One
statement per required key, because multiple keys inside a single `Null` block are ANDed,
which would only deny when every tag is missing at once.

`owner` is deliberately not enforced here. Ownership goes stale as people change teams, so
a preventive deny on it mostly generates break-glass requests. If the org wants owner
recorded, it belongs in a detective control (an AWS Config `required-tags` rule) that
reports drift rather than blocking deploys.

Condition keys: `aws:RequestTag/<key>` with `Null`, plus `StringNotEquals` on the value.

Why a fourth statement on values: tag presence alone produces attribution data you cannot
group by. A free-text `environment` tag gives you `prod`, `Prod`, `production`, and
`prd` as four separate cost lines.

Known gaps, worth stating on stage:

- **`s3:CreateBucket` does not support `aws:RequestTag`.** Bucket tags are set by a
  separate `PutBucketTagging` call, so you cannot enforce bucket tags at creation with an
  SCP. It is deliberately absent from the action list. Adding it would deny all bucket
  creation, because the key is never present.
- **`cloudfront:CreateDistribution` and `cognito-idp:CreateUserPool`** are also excluded
  for the same class of reason. Tagging support on create varies by API.
- Coverage is therefore best effort. It needs a detective backstop (an AWS Config rule
  such as `required-tags`) for everything the preventive layer cannot reach. That
  prevent-plus-detect pairing is the honest version of the pattern.

## 2. Deny cost tag removal

Statement 1 denies untag calls that target the protected keys. Statement 2 additionally
denies `ec2:CreateTags` on those keys, because `CreateTags` overwrites values, so
write access is as dangerous as delete access. It carries a second exclusion for the
provisioning role that legitimately sets tags.

Condition key: `aws:TagKeys` with `ForAnyValue:StringEquals`.

Known gap: S3 is excluded. `s3:PutBucketTagging` replaces the whole tag set, and
`aws:TagKeys` there reflects the keys being written rather than removed, so the condition
reads backwards. S3 tag protection belongs in a bucket policy or an RCP.

## 3. Deny regions outside the approved footprint

Condition key: `aws:RequestedRegion`.

The cost argument is not data residency. It is that resources in regions nobody monitors
become orphaned spend, and a scattered footprint cannot be covered by commitments.

Notes:

- **`us-east-1` cannot be removed from the allow list.** Cost Explorer anomaly monitors
  are us-east-1 only, as are several global service control planes.
- The `NotAction` list is a maintenance treadmill. Say that out loud. It is the clearest
  example in the talk of a guardrail that dates, and it motivates the patterns-over-products
  point.

## 4. Deny accelerated and oversized compute

Condition key: `ec2:InstanceType` with `ForAnyValue:StringLike`, on `ec2:RunInstances`.

Two statements on purpose. The first encodes an architectural decision: inference is
managed through Bedrock, so no team needs a GPU instance, and the design space shrinks
accordingly. The second is a blast-radius cap on general-purpose sizing.

Known gap, and the most useful one in the talk:

- **This does not cover EKS managed node groups.** Those instances are launched through
  Auto Scaling using a service-linked role, and SCPs do not apply to service-linked roles.
  RCPs do not either. Your own workload runs EKS, so the guardrail misses the compute in
  your own architecture.
- The fix is a different altitude: a CloudFormation Hook or a Config rule evaluating
  `AWS::EKS::Nodegroup` instance types. This is the moment to show that the mechanism
  sometimes chooses the altitude for you.

## 5. Deny cost guardrail tampering

Protects budgets, anomaly monitors, CUR definitions, cost allocation tag activation, and
cost categories. Nobody can quietly switch off the control that would have caught them.

Two deliberate design choices to call out:

- **A different break-glass role.** Policies 1 to 4 exempt `OrgBreakGlassRole`. This one
  exempts `FinOpsAdminRole` instead. Not every guardrail should share one escape hatch,
  and the one protecting cost controls should not be openable by whoever can launch
  instances.
- **The third statement has no exclusion at all.** Leaving the organization and detaching
  org policies are escapes with no legitimate emergency path from a member account. Some
  guardrails are deliberately unopenable.

Known gap: SCPs never apply to the management account, where org policies are actually
managed, so the third statement mainly constrains delegated administrator accounts.

---

## Applies to all five

- SCPs do not apply to the management account or to service-linked roles.
- SCPs are prohibitive only. None of these can require that a budget *exists*. That needs
  a CloudFormation Hook, a Service Catalog product, or a Control Tower proactive control.
- Every break-glass exclusion must be logged and alerted. An exception nobody watches is
  just a hole.
- Attach to a test account first, then an OU, then the root. Review CloudTrail
  `AccessDenied` entries before widening.

## Superseded samples

`scp-deny-regions-outside-usa.json` is superseded by policy 3, and
`scp-deny-large-and-metal-ec2.json` by policy 4. Left in place for now rather than
deleted. `scp-deny-expensive-bedrock-models.json` still needs the inference profile fix
before it is usable.
