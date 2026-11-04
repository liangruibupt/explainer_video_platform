#!/bin/bash
# Admin-run (CloudShell or an admin profile), NOT by the agent: create a least-privilege
# evp-video-worker role + instance profile for the EC2 GPU worker, and grant the agent's
# role PassRole on it. Then set "instance_profile": "evp-video-worker" in
# infra/gpu_ec2/modules/video.json (recommended; the first test used evp-video-builder,
# which can also push to ECR and write anywhere in the bucket).
#
#   bash grant_worker_role.sh [AGENT_ROLE_NAME]
set -euo pipefail
ACCT=$(aws sts get-caller-identity --query Account --output text)
R=ap-south-1
BUCKET=evp-models-$ACCT-$R
ROLE=evp-video-worker
AGENT_ROLE=${1:-$(aws iam list-roles --query "Roles[?starts_with(RoleName,'openclaw-bedrock-OpenClawInstanceRole')].RoleName | [0]" --output text)}
echo "account $ACCT  worker role $ROLE  agent role $AGENT_ROLE"

TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam get-role --role-name $ROLE >/dev/null 2>&1 || \
  aws iam create-role --role-name $ROLE --assume-role-policy-document "$TRUST" \
    --tags Key=project,Value=explainer-video Key=module,Value=video >/dev/null

POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadWeights","Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::$BUCKET/weights/*"},
 {"Sid":"Jobs","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::$BUCKET/jobs/video/*"},
 {"Sid":"List","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$BUCKET",
  "Condition":{"StringLike":{"s3:prefix":["weights/*","jobs/video/*"]}}},
 {"Sid":"EcrAuth","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"EcrPull","Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],
  "Resource":"arn:aws:ecr:$R:$ACCT:repository/evp/minimax-h3"}]}
JSON
)
aws iam put-role-policy --role-name $ROLE --policy-name evp-video-worker --policy-document "$POLICY"
aws iam attach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam get-instance-profile --instance-profile-name $ROLE >/dev/null 2>&1 || {
  aws iam create-instance-profile --instance-profile-name $ROLE >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name $ROLE --role-name $ROLE; }

PASS=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"iam:PassRole",
 "Resource":"arn:aws:iam::$ACCT:role/$ROLE","Condition":{"StringEquals":{"iam:PassedToService":"ec2.amazonaws.com"}}}]}
JSON
)
aws iam put-role-policy --role-name "$AGENT_ROLE" --policy-name EvpVideoWorkerPassRole --policy-document "$PASS"

echo "verify:"
aws iam get-instance-profile --instance-profile-name $ROLE --query "InstanceProfile.Roles[].RoleName" --output text
aws iam get-role-policy --role-name "$AGENT_ROLE" --policy-name EvpVideoWorkerPassRole --query PolicyName --output text
echo "done. Set \"instance_profile\": \"$ROLE\" in infra/gpu_ec2/modules/video.json"
