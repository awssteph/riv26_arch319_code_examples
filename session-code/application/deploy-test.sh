#!/bin/bash
# ---------------------------------------------------------------------------
# Deploy-test harness for the AnyCompanyAssist CloudFormation templates.
#
# Purpose: prove the templates deploy as valid, live infrastructure, then tear
# it down. This creates REAL, BILLABLE resources (EKS, OpenSearch Serverless,
# NAT gateway, CloudFront, Bedrock KB). Run `destroy` when done.
#
# Usage:
#   ./deploy-test.sh deploy    # deploy networking then the app (unoptimized)
#   ./deploy-test.sh status    # show stack statuses
#   ./deploy-test.sh destroy   # delete app then networking (reverse order)
#
# The AWS CLI must be the ARM build at /opt/homebrew/bin on this machine.
# ---------------------------------------------------------------------------
set -euo pipefail

export PATH="/opt/homebrew/bin:$PATH"
export AWS_PROFILE="${AWS_PROFILE:-awssteph+reinvent-app-dev}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PAGER=""

STACK_PREFIX="retail"
NET_STACK="${STACK_PREFIX}-networking"
APP_STACK="${STACK_PREFIX}-app"
APP_TEMPLATE="ai_app_unoptimized.yaml"   # switch to ai_app_optimized.yaml to test the other

ACCT="$(aws sts get-caller-identity --query Account --output text)"
BKT="cfn-templates-${ACCT}-${AWS_REGION}"

# Deploy parameters. EMAIL/PASSWORD are demo-only; override via env if desired.
EMAIL="${EMAIL:-demo@example.com}"
PASSWORD="${COGNITO_PASSWORD:-TempPassword123!}"

upload() {
  aws s3api head-bucket --bucket "$BKT" 2>/dev/null || \
    aws s3api create-bucket --bucket "$BKT" --region "$AWS_REGION" >/dev/null
  for f in networking.yaml "$APP_TEMPLATE"; do
    aws s3 cp "$f" "s3://$BKT/$f" >/dev/null
  done
}

deploy() {
  upload
  echo ">>> Deploying $NET_STACK (VPC/subnets/NAT) ..."
  aws cloudformation deploy \
    --stack-name "$NET_STACK" \
    --template-file networking.yaml \
    --parameter-overrides "StackName=${STACK_PREFIX}" \
    --no-fail-on-empty-changeset

  echo ">>> Deploying $APP_STACK ($APP_TEMPLATE) ..."
  aws cloudformation deploy \
    --stack-name "$APP_STACK" \
    --template-file "$APP_TEMPLATE" \
    --parameter-overrides \
      "StackName=${STACK_PREFIX}" \
      "Environment=DEV" \
      "EmailAddress=${EMAIL}" \
      "InitialPassword=${PASSWORD}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset

  echo ">>> Done. Outputs:"
  aws cloudformation describe-stacks --stack-name "$APP_STACK" \
    --query "Stacks[0].Outputs" --output table
}

status() {
  for s in "$NET_STACK" "$APP_STACK"; do
    st="$(aws cloudformation describe-stacks --stack-name "$s" \
        --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NOT_FOUND)"
    echo "$s: $st"
  done
}

destroy() {
  echo ">>> Deleting $APP_STACK ..."
  aws cloudformation delete-stack --stack-name "$APP_STACK" || true
  aws cloudformation wait stack-delete-complete --stack-name "$APP_STACK" || true
  echo ">>> Deleting $NET_STACK ..."
  aws cloudformation delete-stack --stack-name "$NET_STACK" || true
  aws cloudformation wait stack-delete-complete --stack-name "$NET_STACK" || true
  echo ">>> Removing staging bucket $BKT ..."
  aws s3 rb "s3://$BKT" --force || true
  echo ">>> Teardown complete."
}

case "${1:-}" in
  deploy)  deploy ;;
  status)  status ;;
  destroy) destroy ;;
  *) echo "Usage: $0 {deploy|status|destroy}"; exit 1 ;;
esac
