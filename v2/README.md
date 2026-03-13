# 🚀 Deployment Guide: Speech Redaction Framework (SRF) V2

**Version:** 2.0 (Cloud Run Orchestrator)
- **Redaction Pipeline**: A fully serverless Python pipeline running on Cloud Run that leverages the native capabilities of DLP to scan and redact PII from audio files.
- **Triggers**: Two separate Cloud Run configurations to launch the pipeline depending on your use case:
  - **GCS Trigger**: Fires immediately when a file is uploaded to the raw audio bucket.
  - **Insights Trigger**: Fires when Conversational Insights publishes a conversation notification.

## ⚠️ Important: Choose One Trigger Workflow
To avoid double-redaction and race conditions, you should generally **use only one** of the following workflows for a given set of files:

1.  **Direct Upload Workflow (GCS Trigger)**: Use this if you want files redacted *immediately* upon upload, before or independent of Conversational Insights.
2.  **Insights Workflow (Insights Trigger)**: Use this if you are using CCaaS/Insights as the source of truth and want redaction to happen *after* a conversation is created.

**If you upload a file to the GCS bucket AND ingest it into Insights, both triggers will theoretically attempt to fire for the same event log.**
To prevent this, deploy only the trigger relevant to your use case. This is natively handled and enforced as a Terraform variable.

## Architecture
Deploy a serverless Cloud Run orchestrated pipeline that detects new audio in a bucket, archives it locally, and then streams the PII redaction back in-place.

## 1. Prerequisites

Before starting, ensure you have the **Google Cloud SDK** installed and authenticated.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```
### Enable Required APIs
Run this to turn on all necessary services:

```bash
gcloud services enable \
  storage.googleapis.com \
  dlp.googleapis.com \
  speech.googleapis.com \
  run.googleapis.com \
  eventarc.googleapis.com \
  logging.googleapis.com
```

## 2. Configuration
Copy and paste this block into your terminal to set common variables. Update the values to match your specific environment.

```bash
# --- CONFIGURATION ---
export PROJECT_ID=$(gcloud config get-value project)
export REGION="us-central1"

# Buckets (Must be unique globally)
export RAW_BUCKET="${PROJECT_ID}-raw-audio"
export ARCHIVE_BUCKET="${PROJECT_ID}-audio-archive"
```

## 3. Infrastructure Setup
### Step 3.1: Create Storage Buckets
```bash
# Create buckets
gcloud storage buckets create gs://$RAW_BUCKET --location=$REGION
gcloud storage buckets create gs://$ARCHIVE_BUCKET --location=$REGION
```

## 4. DLP Template Configuration
*Note: You must do this in the Console or import a JSON file. Alternatively, use the included terraform files for automated setup.*

Go to Security > Sensitive Data Protection (DLP) > Create Template.
Type: Inspect Template.
Detectors: Select CREDIT_CARD_NUMBER, US_SOCIAL_SECURITY_NUMBER, DATE, DATE_OF_BIRTH.

**Custom InfoTypes (Important for Speech):**
Name: **SPOKEN_DIGITS**
Regex: `(?i)\b(?:(?:\d|one|won|two|to|too|three|four|for|five|six|seven|eight|ate|nine|zero|oh)[\s-]*?){13,22}\b`
Likelihood: Possible

Name: **SPOKEN_DATE**
Regex: `(?i)\b(?:(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+(?:of\s+)?(?:\d{1,2}(?:st|nd|rd|th)?(?:\s+(?:of\s+)?\d{2,4})?|\d{2,4})|\d{1,2}\s+slash\s+\d{1,2}(?:\s+slash\s+\d{2,4})?)\b`
Likelihood: Possible

Save and copy the Template ID (e.g., `projects/YOUR_PROJECT/locations/global/inspectTemplates/TEMPLATE_ID`).
Export this ID as a variable:

```bash
export DLP_TEMPLATE="projects/$PROJECT_ID/locations/global/inspectTemplates/YOUR_TEMPLATE_ID"
```

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
If you prefer not to use Terraform, you can build and deploy the Cloud Run service manually.

### Build and Deploy Context
Navigate to the `cloud-run-orchestrator` directory.

### CRITICAL DECISION: Choose Your Trigger
**Do NOT deploy both triggers manually**.
*   **Option A (Direct GCS)**: Redacts files *immediately* when uploaded.
*   **Option B (Insights)**: Redacts files *only* when Conversational Insights processes them.

#### Option A: Direct GCS Trigger (Manual)
Deploy the service and connect it to Google Cloud Storage.

```bash
cd cloud-run-orchestrator

# 1. Deploy Cloud Run Service
gcloud run deploy redactor-gcs-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION

# 2. Connect GCS Event (Eventarc)
# Note: You need an appropriate service account for this Eventarc trigger
gcloud eventarc triggers create start-redaction-trigger \
  --location=$REGION \
  --destination-run-service=redactor-gcs-trigger \
  --destination-run-region=$REGION \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=$RAW_BUCKET" 
```

#### Option B: Insights Trigger (Manual)
Deploy the service and connect it to your Insights Pub/Sub topic.

```bash
cd cloud-run-orchestrator

# 1. Deploy Cloud Run Service
gcloud run deploy redactor-insights-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION

# 2. Hook to Pub/Sub Subscription manually
# Point your Insights Pub/Sub topic to push to the resulting Cloud Run URL endpoint.
```

## 7. Validation Test
Upload a test MP3 file to your raw bucket to verify the workflow works as intended.

```bash
gcloud storage cp test-call.mp3 gs://$RAW_BUCKET/
```

Monitor the Cloud Run Logs for the respective Orchestrator deployment. You should dynamically see the `STT Transcript Generate` log alongside a `Scanning for PII` followed by an upload cycle.

Check `gs://$ARCHIVE_BUCKET` to confirm the original unredacted copy was safely archived prior to redaction kicking off.

## 8. License & Legal Notices
**License:** This project is licensed under the Apache License, Version 2.0. See LICENSE for the full text.

**3rd Party Components:**
- **pydub**: Distributed under the MIT License. Copyright (c) 2011 James Robert.
