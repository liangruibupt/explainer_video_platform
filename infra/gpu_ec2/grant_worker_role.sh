#!/bin/bash
# Admin-run (CloudShell or an admin profile), NOT by the agent: create one least-privilege
# role + instance profile `evp-gpu-worker` for the EC2 GPU workers of all three modules
# (video in ap-south-1, image and music in us-east-1), and grant the agent's role PassRole
# on it. Then set "instance_profile": "evp-gpu-worker" in infra/gpu_ec2/modules/*.json.
#
# The first tests used the role evp-video-builder (plus an inline policy evp-gpu-us-east-1
# for the us-east-1 bucket and repos), which can also push to the video ECR repo and write
# anywhere in the ap-south-1 bucket. This role can only read weights, use its own job
# prefixes, pull the three images and register with SSM.
#
#   bash grant_worker_role.sh [AGENT_ROLE_NAME]
set -euo pipefail
ACCT=$(aws sts get-caller-identity --query Account --output text)
B_AP=evp-models-$ACCT-ap-south-1     # video
B_US=evp-models-$ACCT-us-east-1      # image, music
ROLE=evp-gpu-worker
AGENT_ROLE=${1:-openclaw-bedrock-OpenClawInstanceRole-4GeeSV4z0qo7}
[[ "$AGENT_ROLE" =~ ^[A-Za-z0-9+=,.@_-]+$ ]] || { echo "bad agent role name: '$AGENT_ROLE'"; exit 1; }
aws iam get-role --role-name "$AGENT_ROLE" --query Role.RoleName --output text >/dev/null
echo "account $ACCT  worker role $ROLE  agent role $AGENT_ROLE"

TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam get-role --role-name $ROLE >/dev/null 2>&1 || \
  aws iam create-role --role-name $ROLE --assume-role-policy-document "$TRUST" \
    --tags Key=project,Value=explainer-video >/dev/null

POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadWeights","Effect":"Allow","Action":"s3:GetObject",
  "Resource":["arn:aws:s3:::$B_AP/weights/*","arn:aws:s3:::$B_US/weights/*"]},
 {"Sid":"Jobs","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
  "Resource":["arn:aws:s3:::$B_AP/jobs/video/*","arn:aws:s3:::$B_AP/jobs/video-ec2/*",
              "arn:aws:s3:::$B_US/jobs/image-ec2/*","arn:aws:s3:::$B_US/jobs/music-ec2/*"]},
 {"Sid":"ListAp","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$B_AP",
  "Condition":{"StringLike":{"s3:prefix":["weights/*","jobs/video/*","jobs/video-ec2/*"]}}},
 {"Sid":"ListUs","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$B_US",
  "Condition":{"StringLike":{"s3:prefix":["weights/*","jobs/image-ec2/*","jobs/music-ec2/*"]}}},
 {"Sid":"EcrAuth","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"EcrPull","Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],
  "Resource":["arn:aws:ecr:ap-south-1:$ACCT:repository/evp/minimax-h3",
              "arn:aws:ecr:us-east-1:$ACCT:repository/evp/qwen-image",
              "arn:aws:ecr:us-east-1:$ACCT:repository/evp/yue2"]}]}
JSON
)
aws iam put-role-policy --role-name $ROLE --policy-name evp-gpu-worker --policy-document "$POLICY"
aws iam attach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam get-instance-profile --instance-profile-name $ROLE >/dev/null 2>&1 || {
  aws iam create-instance-profile --instance-profile-name $ROLE >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name $ROLE --role-name $ROLE; }

PASS=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"iam:PassRole",
 "Resource":"arn:aws:iam::$ACCT:role/$ROLE","Condition":{"StringEquals":{"iam:PassedToService":"ec2.amazonaws.com"}}}]}
JSON
)
aws iam put-role-policy --role-name "$AGENT_ROLE" --policy-name EvpGpuWorkerPassRole --policy-document "$PASS"

echo "verify:"
aws iam get-instance-profile --instance-profile-name $ROLE --query "InstanceProfile.Roles[].RoleName" --output text
aws iam get-role-policy --role-name $ROLE --policy-name evp-gpu-worker --query PolicyName --output text
aws iam get-role-policy --role-name "$AGENT_ROLE" --policy-name EvpGpuWorkerPassRole --query PolicyName --output text
echo "done. Set \"instance_profile\": \"$ROLE\" in infra/gpu_ec2/modules/{video,image,music}.json"
