# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import time
import os
import re
import traceback
import base64
from flask import Flask, request
from google.cloud import storage
from google.cloud import dataflow_v1beta3

# --- FLASK APP DEFINITION (MUST BE GLOBAL) ---
app = Flask(__name__)
# ---------------------------------------------

@app.route("/", methods=["POST"])
def handle_post():
    return index(request)

def index(request):
    event_data = request.get_json()
    if not event_data:
        return "Bad Request: no event data", 400

    print(f"--- EVENT RECEIVED ---")
    
    # 1. Decode Pub/Sub Message if wrapped
    payload = event_data
    if 'message' in event_data and 'data' in event_data['message']:
        try:
            decoded_bytes = base64.b64decode(event_data['message']['data'])
            payload = json.loads(decoded_bytes.decode('utf-8'))
            print("Payload decoded from Pub/Sub")
        except Exception as e:
            print(f"Error decoding Pub/Sub message: {e}")
            return "OK", 200

    # 2. Extract GCS URI from Payload
    gcs_uri = None
    
    # Check for Insights specific structure
    if 'dataSource' in payload and 'gcsSource' in payload['dataSource']:
        gcs_uri = payload['dataSource']['gcsSource'].get('audioUri')
        print(f"Found Insights Audio URI: {gcs_uri}")
    
    # Fallback/Direct GCS event structure checks
    if not gcs_uri:
        bucket = payload.get('bucket')
        name = payload.get('name')
        if bucket and name:
            gcs_uri = f"gs://{bucket}/{name}"

    if not gcs_uri:
        print(f"Skipping: Could not find 'audioUri' or 'bucket/name' in payload: {payload.keys()}")
        return "OK", 200

    # 3. Parse Bucket and Filename from URI
    # URI format: gs://bucket-name/path/to/file.mp3
    if gcs_uri.startswith("gs://"):
        parts = gcs_uri[5:].split("/", 1)
        if len(parts) == 2:
            bucket_name = parts[0]
            file_name = parts[1]
        else:
            print(f"Invalid GCS URI format: {gcs_uri}")
            return "OK", 200
    else:
        print(f"URI does not start with gs://: {gcs_uri}")
        return "OK", 200

    print(f"Targeting: {bucket_name}/{file_name}")
    process_redaction(bucket_name, file_name, file_name)
    
    return "OK", 200

def process_redaction(bucket_name, file_name, conversation_id_raw):
    # 0. SKIP TEMP FILES
    if ".temp_" in file_name:
        print(f"Skipping temp file: {file_name}")
        return
    
    # 0.1 SKIP MOVED FILES (Loop Prevention)
    if file_name.endswith("_raw_moved"):
        print(f"Skipping moved file (loop prevention): {file_name}")
        return

    storage_client = storage.Client()
    bucket_obj = storage_client.bucket(bucket_name)
    source_blob = bucket_obj.get_blob(file_name)
    
    if not source_blob: 
        print(f"File missing: {file_name}")
        return

    # 1. IDEMPOTENCY CHECK
    if source_blob.metadata and source_blob.metadata.get('redacted') == 'true':
        print(f"SKIPPING: {file_name} is already redacted.")
        return

    # 2. ARCHIVE STEP
    archive_bucket_name = os.environ.get('ARCHIVE_BUCKET')
    if archive_bucket_name:
        archive_bucket = storage_client.bucket(archive_bucket_name)
        bucket_obj.copy_blob(source_blob, archive_bucket, file_name)
        print(f"ARCHIVED: {file_name} -> {archive_bucket_name}")

    # 3. RENAME FILE (Break Link)
    moved_file_name = f"{file_name}_raw_moved"
    print(f"Renaming {file_name} to {moved_file_name}...")
    try:
        # Note: rename_blob copies then deletes.
        new_blob = bucket_obj.rename_blob(source_blob, moved_file_name)
        source_blob = new_blob 
    except Exception as e:
        print(f"Error renaming blob: {e}")
        # If rename fails, we might want to abort or continue with original. 
        # Aborting is safer to ensure link is broken.
        return

    # 4. LAUNCH DATAFLOW
    launch_dataflow_job(bucket_name, moved_file_name, conversation_id_raw, source_blob, original_filename=file_name)

def launch_dataflow_job(bucket_name, file_name, conversation_id, source_blob, original_filename=None):
    project = os.environ.get('PROJECT_ID')
    region = os.environ.get('REGION', 'us-central1')
    
    # ENSURE gs:// IS HERE
    raw_template_bucket = os.environ.get('TEMPLATE_BUCKET')
    if not raw_template_bucket.startswith("gs://"):
        template_path = f"gs://{raw_template_bucket}/templates/redactor.json"
    else:
        template_path = f"{raw_template_bucket}/templates/redactor.json"

    dlp_template = os.environ.get('DLP_TEMPLATE')
    
    launch_service = dataflow_v1beta3.FlexTemplatesServiceClient()
    
    base_name = os.path.basename(conversation_id)
    safe_id = re.sub(r'[^a-z0-9-]', '-', base_name.lower())
    if safe_id and not safe_id[0].isalpha(): safe_id = "job-" + safe_id
    job_name = f"redact-{safe_id}-{int(time.time())}"
    input_uri = f"gs://{bucket_name}/{file_name}"

    parameters = {
        "input_file": input_uri,
        "output_bucket": bucket_name,
        "project": project,
        "project_id": project,
        "template_id": dlp_template,
        "output_filename": original_filename if original_filename else file_name
    }

    print(f"Launching Dataflow for {project} with input: {input_uri}")
    print(f"Using Query Template Path: {template_path}")
    
    # Use 'redaction-sa' explicitly as discovered
    service_account_email = f"redaction-sa@{project}.iam.gserviceaccount.com"

    launch_request = dataflow_v1beta3.LaunchFlexTemplateRequest(
        project_id=project,
        location=region,
        launch_parameter={
            "job_name": job_name,
            "container_spec_gcs_path": template_path,
            "parameters": parameters,
            "environment": {
                "temp_location": f"gs://{os.environ.get('TEMPLATE_BUCKET')}/temp",
                "service_account_email": service_account_email,
                "subnetwork": f"regions/{region}/subnetworks/{os.environ.get('SUBNET', 'ccai-subnet')}"
            }
        }
    )
    
    try:
        response = launch_service.launch_flex_template(request=launch_request)
        print(f"JOB LAUNCHED: {response.job.id}")
    except Exception as e:
        print(f"Error launching Dataflow job: {e}")
        raise e

# --- IMPORTANT: EXPOSE APP FOR GUNICORN ---
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))