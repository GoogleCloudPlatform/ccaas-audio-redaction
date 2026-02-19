# 🚀 Deployment Guide: Speech Redaction Framework (SRF)

**Version:** 1.0 (Dataflow v15)
**Objective:** Deploy a serverless pipeline that detects new audio in a bucket, archives it, and then redacts PII in-place using Dataflow.

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
5. Build & Deploy Pipeline
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
Step 6.1: Deploy the GCS Trigger Service
Deploy the Python Flask app that listens for GCS events.

Bash
cd cloud-run-gcs-trigger

gcloud run deploy redactor-gcs-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --service-account $SA_EMAIL \
  --set-env-vars PROJECT_ID=$PROJECT_ID,TEMPLATE_BUCKET=$DATAFLOW_BUCKET,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION,SUBNET=$SUBNET_NAME

cd ..

Step 6.2: Deploy the Insights Trigger Service (Optional)
Deploy the Python Flask app that listens for Insights events.

Bash
cd cloud-run-insights-trigger

gcloud run deploy redactor-insights-trigger \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --service-account $SA_EMAIL \
  --set-env-vars PROJECT_ID=$PROJECT_ID,TEMPLATE_BUCKET=$DATAFLOW_BUCKET,ARCHIVE_BUCKET=$ARCHIVE_BUCKET,DLP_TEMPLATE=$DLP_TEMPLATE,REGION=$REGION,SUBNET=$SUBNET_NAME

cd ..

Step 6.3: Create Eventarc Trigger
Connect the bucket upload event to the Cloud Run service.

Bash
gcloud eventarc triggers create start-redaction-trigger \
  --location=$REGION \
  --destination-run-service=redactor-gcs-trigger \
  --destination-run-region=$REGION \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=$RAW_BUCKET" \
  --service-account=$SA_EMAIL
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

Pydub: Distributed under the MIT License. Copyright 