# 🚀 Deployment Guide: Speech Redaction Framework (SRF)

**Version:** 1.0 (Dataflow v15)
- **Redaction Pipeline**: A Dataflow (Apache Beam) pipeline that processes audio files.
- **Triggers**: Two Cloud Run services to launch the pipeline:
  - **GCS Trigger**: Fires immediately when a file is uploaded to the raw audio bucket.
  - **Insights Trigger**: Fires when Conversational Insights publishes a conversation notification.

## ⚠️ Important: Choose One Trigger Workflow
To avoid double-redaction and race conditions, you should generally **use only one** of the following workflows for a given set of files:

1.  **Direct Upload Workflow (GCS Trigger)**: Use this if you want files redacted *immediately* upon upload, before or independent of Conversational Insights.
2.  **Insights Workflow (Insights Trigger)**: Use this if you are using CCaaS/Insights as the source of truth and want redaction to happen *after* a conversation is created.

**If you upload a file to the GCS bucket AND ingest it into Insights, both triggers will fire.**
To prevent this, deploy only the trigger relevant to your use case (comment out the other in `terraform/main.tf`).

## Architecture
Deploy a serverless pipeline that detects new audio in a bucket, archives it, and then redacts PII in-place using Dataflow.

## 1. Prerequisites

Before starting, ensure you have the **Google Cloud SDK** installed and authenticated.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
Enable Required APIs
Run this to turn on all necessary services:

Bash
gcloud services enable \
  dataflow.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  dlp.googleapis.com \
  speech.googleapis.com \
  run.googleapis.com \
  eventarc.googleapis.com \
  logging.googleapis.com
2. Configuration
Copy and paste this block into your terminal to set common variables. Update the values to match your specific environment.

Bash
# --- CONFIGURATION ---
export PROJECT_ID=$(gcloud config get-value project)
export REGION="us-central1"

# Buckets (Must be unique globally)
export RAW_BUCKET="${PROJECT_ID}-raw-audio"
export ARCHIVE_BUCKET="${PROJECT_ID}-audio-archive"
export DATAFLOW_BUCKET="${PROJECT_ID}-dataflow-system"

# Networking (For Private Dataflow Workers)
export VPC_NAME="ccai-vpc"
export SUBNET_NAME="ccai-subnet"

# Service Account for Dataflow
export WORKER_SA="dataflow-worker-sa"
3. Infrastructure Setup
Step 3.1: Create Storage Buckets
Bash
# Create buckets
gcloud storage buckets create gs://$RAW_BUCKET --location=$REGION
gcloud storage buckets create gs://$ARCHIVE_BUCKET --location=$REGION
gcloud storage buckets create gs://$DATAFLOW_BUCKET --location=$REGION

# Create folder structure for Dataflow
touch empty
gcloud storage cp empty gs://$DATAFLOW_BUCKET/temp/
gcloud storage cp empty gs://$DATAFLOW_BUCKET/staging/
rm empty
Step 3.2: Create Private Network (VPC)
Dataflow workers need a Private VPC to run securely without public IPs.

Bash
# Create VPC
gcloud compute networks create $VPC_NAME --subnet-mode=custom

# Create Subnet (Enable Private Google Access is CRITICAL)
gcloud compute networks subnets create $SUBNET_NAME \
  --network=$VPC_NAME \
  --region=$REGION \
  --range=10.0.0.0/24 \
  --enable-private-ip-google-access

# Create Firewall Rule (Allow internal communication for Dataflow workers)
gcloud compute firewall-rules create allow-dataflow-internal \
  --network=$VPC_NAME \
  --action=allow \
  --direction=INGRESS \
  --source-ranges=10.0.0.0/24 \
  --rules=tcp:12345-12346
Step 3.3: Create Service Account
This identity runs the Dataflow job.

Bash
gcloud iam service-accounts create $WORKER_SA \
  --display-name="Dataflow Worker Service Account"

# Grant Permissions (Storage, Dataflow, DLP, Speech)
export SA_EMAIL="${WORKER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/dataflow.worker"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/dataflow.developer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/storage.objectAdmin"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/dlp.user"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/speech.client"
4. DLP Template Configuration
Note: You must do this in the Console or import a JSON file. Alternatively, use the included terraform files for automated setup.

## 5. Deployment & Switching Triggers (Terraform)
The recommended way to deploy the pipeline and switch between triggers is using Terraform. **Terraform enforces mutual exclusivity**, ensuring you only run the trigger you need.

```bash
cd terraform
# Initialize Terraform
terraform init

# Plan deployment (Choose GCS or INSIGHTS)
terraform plan -var="project_id=$PROJECT_ID" -var="dlp_template_id=$DLP_TEMPLATE" -var="deploy_trigger=GCS"

# Apply
terraform apply -var="project_id=$PROJECT_ID" -var="dlp_template_id=$DLP_TEMPLATE" -var="deploy_trigger=GCS"
```
**How to Switch Triggers (`deploy_trigger` variable):**
- To use **Option A (Direct GCS)**: Set `-var="deploy_trigger=GCS"`
- To use **Option B (Insights)**: Set `-var="deploy_trigger=INSIGHTS"`
- To **Pause Pipeline** (Deploys only base infrastructure without triggers): Set `-var="deploy_trigger=NONE"`

## 6. Manual Deployment (Alternative)

Go to Security > Sensitive Data Protection (DLP) > Create Template.

Type: Inspect Template.

Detectors: Select CREDIT_CARD_NUMBER, EMAIL_ADDRESS, US_SOCIAL_SECURITY_NUMBER.

Custom InfoTypes (Important for Speech):

Name: SPOKEN_DIGITS

Regex: \b(?:\d[ -]*?){13,19}\b

Likelihood: Possible

Save and copy the Template ID (e.g., projects/YOUR_PROJECT/locations/global/inspectTemplates/TEMPLATE_ID).

Export this ID as a variable:

Bash
export DLP_TEMPLATE="projects/$PROJECT_ID/locations/global/inspectTemplates/YOUR_TEMPLATE_ID"
export DLP_TEMPLATE="projects/$PROJECT_ID/locations/global/inspectTemplates/YOUR_TEMPLATE_ID"
### 6.1 Build & Deploy Pipeline (Manual)
Step 5.1: Build Docker Image (v15)
Navigate to the worker folder to build the unified image.

Bash
cd dataflow-worker

# Build the image using the generic Cloud Build config
gcloud builds submit . --config cloudbuild.yaml
Step 5.2: Register Dataflow Flex Template
This creates the "Blueprint" file in GCS that the Cloud Run starter will call.

Bash
gcloud dataflow flex-template build gs://$DATAFLOW_BUCKET/templates/redactor.json \
  --image "gcr.io/$PROJECT_ID/redactor-worker:v15" \
  --sdk-language "PYTHON" \
  --metadata-file "metadata.json"

cd ..
6. Deploy Triggers (Cloud Run)

### ⚠️ CRITICAL DECISION: Choose Your Trigger
**Do NOT deploy both triggers** unless you have a specific advanced use case.
*   **Option A (Direct GCS)**: Redacts files *immediately* when uploaded to the bucket. Best for simple pipelines.
*   **Option B (Insights)**: Redacts files *only* when Conversational Insights processes them. Best for Contact Center integration.

### Option A: Direct GCS Trigger (The "Simple" Path)
Deploy the service that listens to Google Cloud Storage events.

```bash
cd dataflow-worker/cloud-run-gcs-trigger

# 1. Deploy Cloud Run Service
gcloud run deploy redactor-gcs-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --service-account $SA_EMAIL \
  --set-env-vars PROJECT_ID=$PROJECT_ID,TEMPLATE_BUCKET=$DATAFLOW_BUCKET,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION,SUBNET=$SUBNET_NAME

cd ../..

# 2. Connect GCS Event (Eventarc)
gcloud eventarc triggers create start-redaction-trigger \
  --location=$REGION \
  --destination-run-service=redactor-gcs-trigger \
  --destination-run-region=$REGION \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=$RAW_BUCKET" \
  --service-account=$SA_EMAIL
```

### Option B: Insights Trigger (The "Enterprise" Path)
Deploy the service that listens to Conversational Insights conversation events (via Pub/Sub).

```bash
cd dataflow-worker/cloud-run-insights-trigger

# 1. Deploy Cloud Run Service
# Note: This will use the "Default Compute Service Account" for the service identity to resolve permission issues.
export DEFAULT_SA=$(gcloud iam service-accounts list --filter="displayName:'Compute Engine default service account'" --format="value(email)" --project=$PROJECT_ID)

gcloud run deploy redactor-insights-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --service-account $DEFAULT_SA \
  --set-env-vars PROJECT_ID=$PROJECT_ID,TEMPLATE_BUCKET=$DATAFLOW_BUCKET,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION,SUBNET=$SUBNET_NAME

cd ../..

# 2. Grant Permission (Allow Default SA to generic Redaction SA)
# This is required because the Cloud Run service (Default SA) launches Dataflow as the Worker SA.
export WORKER_SA="redaction-sa@${PROJECT_ID}.iam.gserviceaccount.com" # Verify your actual SA email!

gcloud iam service-accounts add-iam-policy-binding $WORKER_SA \
    --member="serviceAccount:$DEFAULT_SA" \
    --role="roles/iam.serviceAccountUser" \
    --project=$PROJECT_ID

# 3. Create Pub/Sub Subscription (If not created by Terraform)
# Ensure your Insights topic is triggering this Cloud Run service.
```
7. Validation Test
Clean State: Ensure $RAW_BUCKET is empty.

Upload: Upload a test MP3 file (longer than 1 minute recommended to test Async Speech).

Bash
gcloud storage cp test-call.mp3 gs://$RAW_BUCKET/
Monitor:

Cloud Run Logs: Should see "Archiving file..." then "Triggering Dataflow job...".

Dataflow Console: A job should appear.

Verify Result: Wait ~5-6 minutes, then check the file metadata:

Bash
gcloud storage objects describe gs://$RAW_BUCKET/test-call.mp3
Success Criteria: Metadata includes redacted: true.

Archive Check: Check gs://$ARCHIVE_BUCKET to confirm the original unredacted copy exists.

8. License & Legal Notices
License: This project is licensed under the Apache License, Version 2.0. See LICENSE for the full text.

3rd Party Components:

FFmpeg: This software uses code of FFmpeg licensed under the LGPLv2.1 and its source can be downloaded here. This solution installs FFmpeg binaries during the Docker build process. Users are responsible for ensuring their use complies with the FFmpeg licensing terms.

Pydub: Distributed under the MIT License. Copyright (c) 2011 James Robert.

## 9. Security & Vulnerability Testing (Pip Hashes)
This pipeline explicitly mitigates Python Supply Chain attacks (e.g., malicious PyPI package spoofing) by enforcing strict hash checking upon build.

### How it is configured:
1. The `dataflow-worker/requirements.txt` contains SHA256 hashed declarations for all packages.
2. The `dataflow-worker/Dockerfile` uses the `--require-hashes` flag during `pip install`.

### How to test the Security Validations:
You can test this layer of security in your local environment or Cloud Shell.

1. **Verify Successful Secure Build:**
   Run the standard build to ensure the hashes legitimately map to the correct safe packages:
   ```bash
   cd dataflow-worker
   gcloud builds submit . --config cloudbuild.yaml
   ```
   *Result:* Cloud Build succeeds perfectly.

2. **Simulate a Supply-Chain Tampering Attack:**
   Open `dataflow-worker/requirements.txt` and artificially alter one number/letter of a `--hash=sha256:...` value for any package.
   Run the build command again.
   *Result:* `pip` throws a **Hash-Mismatch Error** and cleanly aborts the build, proving that if a package is tampered with upstream, our architecture will proactively refuse to install it!

3. **Simulate Unpinned/Rogue Package Injection:**
   Add a random unpinned line to the bottom of `requirements.txt` (e.g. `requests==2.31.0` without any hashing).
   Run the build command again.
   *Result:* `pip` throws a **Missing Hashes Error** and cleanly aborts, enforcing that developers cannot accidentally introduce unverified dependencies into the worker. 
