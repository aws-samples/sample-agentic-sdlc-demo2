#!/bin/bash
#
# Packages all deployment assets (agent source, UI build, Lambda zips, project
# context) and uploads them to a dedicated S3 bucket. Prints the bucket name
# on stdout so `yarn deploy-infrastructure` can pass it to the CDK.
#
# Bucket naming: aidlc-assets-<account>-<region>
# Override via the ASSETS_BUCKET_NAME env var if needed.
#

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

set -e

# ──────────────────────────────────────────────────────────────
# Resolve account + region
# ──────────────────────────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region)}}

# Project default region when none is otherwise configured.
AWS_REGION=${AWS_REGION:-us-east-1}

if [[ -z "$AWS_REGION" ]]; then
  echo "ERROR: no AWS region set. Run 'aws configure' or export AWS_REGION." >&2
  exit 1
fi

BUCKET_NAME=${ASSETS_BUCKET_NAME:-"aidlc-assets-${ACCOUNT_ID}-${AWS_REGION}"}

echo "Account:     $ACCOUNT_ID" >&2
echo "Region:      $AWS_REGION" >&2
echo "Bucket:      $BUCKET_NAME" >&2

# ──────────────────────────────────────────────────────────────
# Ensure the bucket exists (create it with sane defaults if not)
# ──────────────────────────────────────────────────────────────
if ! aws s3api head-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
  echo "Creating bucket $BUCKET_NAME ..." >&2
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" >&2
  else
    aws s3api create-bucket \
      --bucket "$BUCKET_NAME" \
      --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >&2
  fi

  aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >&2

  aws s3api put-bucket-encryption \
    --bucket "$BUCKET_NAME" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >&2
else
  echo "Bucket $BUCKET_NAME already exists — reusing." >&2
fi

# ──────────────────────────────────────────────────────────────
# Stage assets locally
# ──────────────────────────────────────────────────────────────
rm -f "$SCRIPT_DIR"/../*.zip

assets_dir="$SCRIPT_DIR"/../assets
mkdir -p "$assets_dir"
rm -fr "$assets_dir"/*

# Copy assetsSrc contents (isv-agents.json, labs, etc.)
cp -r "$SCRIPT_DIR"/../assetsSrc/. "$assets_dir"/

# Package code assets (Kiro-native: no UI build, no agent container, no gateway
# Lambda zips — the Project API / webhook Lambdas bundle from cdk/custom-resources
# via CDK Code.fromAsset, so package-code.sh is now a no-op kept for wiring).
cd "$SCRIPT_DIR"
bash package-code.sh >&2

# ──────────────────────────────────────────────────────────────
# Upload to the assets bucket
# ──────────────────────────────────────────────────────────────
echo "Uploading assets to s3://$BUCKET_NAME/assets/ ..." >&2
aws s3 sync "$assets_dir" "s3://$BUCKET_NAME/assets/" \
  --delete \
  --region "$AWS_REGION" >&2

# Emit the bucket name on stdout (last line) so callers can capture it
echo "$BUCKET_NAME"
