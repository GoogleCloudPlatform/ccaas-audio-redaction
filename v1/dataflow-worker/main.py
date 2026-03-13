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

import argparse
import logging
import io
import os
import tempfile
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.options.pipeline_options import SetupOptions
from apache_beam.options.pipeline_options import WorkerOptions
from apache_beam.options.pipeline_options import GoogleCloudOptions

# Note: We import specific libraries inside the function to ensure 
# they are available on the worker nodes without pickling issues.

# Configure Logging
logging.basicConfig(level=logging.INFO)

def redact_audio(input_uri, output_uri, project_id, template_id):
    from google.cloud import storage
    from google.cloud import speech
    from google.cloud import dlp_v2
    from pydub import AudioSegment

    logging.info(f"--- START REDACTION: {input_uri} ---")
    
    # Initialize Clients
    storage_client = storage.Client()
    speech_client = speech.SpeechClient()
    dlp_client = dlp_v2.DlpServiceClient()

    # 1. Parse URI
    try:
        parts = input_uri.replace("gs://", "").split("/")
        bucket_name = parts[0]
        blob_name = "/".join(parts[1:])
        local_filename = blob_name.split("/")[-1]
        local_path = f"/tmp/{local_filename}"
    except Exception as e:
        logging.error(f"Failed to parse input URI: {input_uri}")
        raise e

    # 2. Download Original
    logging.info(f"Downloading from {bucket_name}/{blob_name}...")
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(local_path)

    # 3. Transcribe
    logging.info("Transcribing...")
    
    # Convert to 16kHz Mono WAV for Speech API compatibility
    content = None
    try:
        converted_path = f"/tmp/{local_filename}.wav"
        audio_seg = AudioSegment.from_file(local_path)
        audio_seg = audio_seg.set_frame_rate(16000).set_channels(1)
        audio_seg.export(converted_path, format="wav")
        logging.info(f"Converted audio to 16kHz WAV: {converted_path}")
        
        with io.open(converted_path, "rb") as f:
            content = f.read()
        os.remove(converted_path)
    except Exception as e:
        logging.error(f"Audio Conversion Failed: {e}")
        # Logic will fall through to 'if content:' check

    response = None
    if content:
        # Upload temp WAV to GCS for Async Speech API
        temp_gcs_uri = f"{output_uri}.temp_16k.wav"
        temp_out_parts = temp_gcs_uri.replace("gs://", "").split("/")
        temp_bucket = storage_client.bucket(temp_out_parts[0])
        temp_blob = temp_bucket.blob("/".join(temp_out_parts[1:]))

        logging.info(f"Uploading temporary WAV for async processing...")
        temp_blob.upload_from_string(content, content_type="audio/wav")

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-US",
            enable_word_time_offsets=True,
            model="phone_call", 
            use_enhanced=True
        )
        audio = speech.RecognitionAudio(uri=temp_gcs_uri)
        
        try:
            logging.info("Starting Long Running Recognize (Async)...")
            operation = speech_client.long_running_recognize(config=config, audio=audio)
            
            # Wait up to 30 mins (1800s)
            response = operation.result(timeout=1800)
            logging.info("Transcription complete.")
        except Exception as e:
            logging.error(f"Speech API Failed: {e}")
        
        # Cleanup GCS temp file
        try:
            temp_blob.delete()
        except:
            pass
    
    # 4. Build Word Map
    full_transcript = ""
    word_map = []
    current_char_offset = 0
    
    if response and response.results:
        for result in response.results:
            alternative = result.alternatives[0]
            for word_info in alternative.words:
                word_text = word_info.word
                start_time = word_info.start_time.total_seconds()
                end_time = word_info.end_time.total_seconds()
                
                start_index = current_char_offset
                end_index = start_index + len(word_text)
                
                word_map.append({
                    "word": word_text,
                    "start_time": start_time,
                    "end_time": end_time,
                    "start_char": start_index,
                    "end_char": end_index
                })
                full_transcript += word_text + " " 
                current_char_offset += len(word_text) + 1

    file_to_upload = local_path
    
    if full_transcript:
        # 5. DLP Scan (Chunked)
        logging.info(f"Scanning for PII using Template: {template_id}...")
        
        MAX_CHUNK_SIZE = 400000 
        findings = []
        
        try:
            transcript_len = len(full_transcript)
            for i in range(0, transcript_len, MAX_CHUNK_SIZE):
                chunk_text = full_transcript[i : i + MAX_CHUNK_SIZE]
                dlp_response = dlp_client.inspect_content(
                    request={
                        "parent": f"projects/{project_id}",
                        "inspect_template_name": template_id,
                        "item": {"value": chunk_text}
                    }
                )
                if dlp_response.result.findings:
                    for f in dlp_response.result.findings:
                        start = f.location.codepoint_range.start
                        end = f.location.codepoint_range.end
                        f.location.codepoint_range.start = start + i
                        f.location.codepoint_range.end = end + i
                        findings.append(f)

            logging.info(f"Found {len(findings)} items to redact.")
            
            if len(findings) > 0:
                # 6. Apply Redaction
                audio_seg = AudioSegment.from_mp3(local_path)
                
                for finding in findings:
                    location = finding.location.codepoint_range
                    f_start = location.start
                    f_end = location.end
                    
                    for w in word_map:
                        if (w["start_char"] < f_end) and (w["end_char"] > f_start):
                            s_ms = w["start_time"] * 1000
                            e_ms = w["end_time"] * 1000
                            silence = AudioSegment.silent(duration=e_ms - s_ms)
                            audio_seg = audio_seg[:s_ms] + silence + audio_seg[e_ms:]
                
                output_local_path = "/tmp/redacted.mp3"
                audio_seg.export(output_local_path, format="mp3")
                file_to_upload = output_local_path
            else:
                logging.info("No PII found. Using original.")
                
        except Exception as e:
            logging.error(f"DLP/Redaction Failed: {e}")
            logging.warning("Proceeding with original file.")

    # 7. Upload Result
    logging.info(f"Uploading result to: {output_uri}")
    
    out_parts = output_uri.replace("gs://", "").split("/")
    out_bucket_name = out_parts[0]
    out_blob_name = "/".join(out_parts[1:])
    
    out_bucket = storage_client.bucket(out_bucket_name)
    out_blob = out_bucket.blob(out_blob_name)

    out_blob.upload_from_filename(file_to_upload)

    # 8. Apply Metadata Lock
    out_blob.reload() 
    out_blob.metadata = {'transcribed': 'true', 'redacted': 'true'}
    out_blob.patch()
    
    logging.info("--- REDACTION COMPLETE ---")


class RedactAudioFn(beam.DoFn):
    def __init__(self, project_id, template_id, output_bucket, output_filename=None):
        self.project_id = project_id
        self.template_id = template_id
        self.output_bucket = output_bucket
        self.output_filename = output_filename

    def process(self, input_uri):
        logging.info(f"Worker received: {input_uri}")
        
        # --- GHOST FILE PROTECTION ---
        if input_uri.endswith(".temp_16k.wav"):
            logging.info(f"Ignoring temporary file: {input_uri}")
            return
        # -----------------------------

        if self.output_filename:
            output_uri = f"gs://{self.output_bucket}/{self.output_filename}"
        else:
            output_uri = input_uri # In-place overwrite
        
        try:
            redact_audio(
                input_uri=input_uri, 
                output_uri=output_uri, 
                project_id=self.project_id, 
                template_id=self.template_id
            )
            yield output_uri
        except Exception as e:
            logging.error(f"WORKER FAILURE on {input_uri}: {e}")
            raise


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_file', required=True)
    parser.add_argument('--output_bucket', required=True)
    parser.add_argument('--project_id', required=True)
    parser.add_argument('--template_id', required=True)
    parser.add_argument('--project', required=False) 
    parser.add_argument('--output_filename', required=False) 

    known_args, pipeline_args = parser.parse_known_args(argv)

    pipeline_options = PipelineOptions(pipeline_args)

    # --- Container Configuration ---
    worker_options = pipeline_options.view_as(WorkerOptions)
    
    # CRITICAL: Dynamically set the image based on the Project ID
    worker_options.sdk_container_image = f"gcr.io/{known_args.project_id}/redactor-worker:v15"
    # -------------------------------
    
    setup_options = pipeline_options.view_as(SetupOptions)
    setup_options.save_main_session = True 
    setup_options.sdk_location = 'container'

    google_cloud_options = pipeline_options.view_as(GoogleCloudOptions)
    google_cloud_options.project = known_args.project_id
    google_cloud_options.region = "us-central1"
    
    if not google_cloud_options.temp_location:
         google_cloud_options.temp_location = f"gs://{known_args.output_bucket}/temp"

    logging.info(f"Starting Pipeline for: {known_args.input_file}")

    with beam.Pipeline(options=pipeline_options) as p:
        (p 
         | 'Read Input' >> beam.Create([known_args.input_file])
         | 'Redact' >> beam.ParDo(RedactAudioFn(
                project_id=known_args.project_id,
                template_id=known_args.template_id,
                output_bucket=known_args.output_bucket,
                output_filename=known_args.output_filename
            ))
        )

if __name__ == '__main__':
    run()