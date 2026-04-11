import os
import re
import json
import time
import base64
import logging
import io
import tempfile
import traceback
from flask import Flask, request
from google.cloud import storage
from google.cloud import speech
from google.cloud import dlp_v2
from pydub import AudioSegment

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

@app.route("/", methods=["POST"])
def handle_post():
    event_data = request.get_json()
    if not event_data:
        return "Bad Request: no event data", 400

    logging.info(f"--- EVENT RECEIVED ---")
    
    # 1. Decode Pub/Sub Message if wrapped
    payload = event_data
    if 'message' in event_data and 'data' in event_data['message']:
        try:
            decoded_bytes = base64.b64decode(event_data['message']['data'])
            payload = json.loads(decoded_bytes.decode('utf-8'))
            logging.info("Payload decoded from Pub/Sub")
        except Exception as e:
            logging.error(f"Error decoding Pub/Sub message: {e}")
            return "OK", 200

    # 2. Extract GCS URI from Payload
    gcs_uri = None
    
    # Check for Insights specific structure
    if 'dataSource' in payload and 'gcsSource' in payload['dataSource']:
        gcs_uri = payload['dataSource']['gcsSource'].get('audioUri')
        logging.info(f"Found Insights Audio URI: {gcs_uri}")
    
    # Fallback/Direct GCS event structure checks
    if not gcs_uri:
        bucket = payload.get('bucket')
        name = payload.get('name')
        if bucket and name:
            gcs_uri = f"gs://{bucket}/{name}"

    if not gcs_uri:
        logging.info(f"Skipping: Could not find 'audioUri' or 'bucket/name' in payload.")
        return "OK", 200

    # 3. Parse Bucket and Filename from URI
    if gcs_uri.startswith("gs://"):
        parts = gcs_uri[5:].split("/", 1)
        if len(parts) == 2:
            bucket_name = parts[0]
            file_name = parts[1]
        else:
            logging.warning(f"Invalid GCS URI format: {gcs_uri}")
            return "OK", 200
    else:
        logging.warning(f"URI does not start with gs://: {gcs_uri}")
        return "OK", 200

    logging.info(f"Targeting: {bucket_name}/{file_name}")
    
    try:
        process_redaction(bucket_name, file_name, file_name)
    except Exception as e:
        # We always return 200 to Eventarc/PubSub so it doesn't infinitely retry 
        # unless it is a transient error. For Redaction we usually log the error and move on.
        logging.error(f"Failed to process {file_name}: {traceback.format_exc()}")
        
    return "OK", 200

def process_redaction(bucket_name, file_name, conversation_id_raw):
    # 0. SKIP TEMP FILES
    if ".temp_" in file_name:
        logging.info(f"Skipping temp file: {file_name}")
        return
    
    # 0.1 SKIP MOVED FILES (Loop Prevention)
    if file_name.endswith("_raw_moved"):
        logging.info(f"Skipping moved file (loop prevention): {file_name}")
        return

    storage_client = storage.Client()
    bucket_obj = storage_client.bucket(bucket_name)
    source_blob = bucket_obj.get_blob(file_name)
    
    if not source_blob: 
        logging.warning(f"File missing: {file_name}")
        return

    # 1. IDEMPOTENCY CHECK
    if source_blob.metadata and source_blob.metadata.get('redacted') == 'true':
        logging.info(f"SKIPPING: {file_name} is already redacted.")
        return

    # 2. ARCHIVE STEP
    archive_bucket_name = os.environ.get('ARCHIVE_BUCKET')
    if archive_bucket_name:
        archive_bucket = storage_client.bucket(archive_bucket_name)
        bucket_obj.copy_blob(source_blob, archive_bucket, file_name)
        logging.info(f"ARCHIVED: {file_name} -> {archive_bucket_name}")

    # 3. RENAME FILE (Break Link)
    moved_file_name = f"{file_name}_raw_moved"
    logging.info(f"Renaming {file_name} to {moved_file_name}...")
    try:
        new_blob = bucket_obj.rename_blob(source_blob, moved_file_name)
        source_blob = new_blob 
    except Exception as e:
        logging.error(f"Error renaming blob: {e}")
        return

    # 4. PERFORM REDACTION DIRECTLY IN-MEMORY / LOCAL DISK
    project = os.environ.get('PROJECT_ID')
    dlp_info_types = os.environ.get('DLP_INFO_TYPES', 'CREDIT_CARD_NUMBER, EMAIL_ADDRESS, US_SOCIAL_SECURITY_NUMBER, PHONE_NUMBER, DATE_OF_BIRTH, DATE')
    
    input_uri = f"gs://{bucket_name}/{moved_file_name}"
    # The output URI should replace the original filename that Insights expects
    output_uri = f"gs://{bucket_name}/{file_name}"

    logging.info(f"Starting orchestration redactor for {input_uri}...")
    redact_audio(
        input_uri=input_uri,
        output_uri=output_uri,
        project_id=project,
        info_types=dlp_info_types
    )


def redact_audio(input_uri, output_uri, project_id, info_types):
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
    logging.info(f"Downloading from {bucket_name}/{blob_name} to {local_path}...")
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

    response = None
    if content:
        # Upload temp WAV to GCS for Async Speech API
        temp_gcs_uri = f"{output_uri}.temp_16k.wav"
        temp_out_parts = temp_gcs_uri.replace("gs://", "").split("/")
        temp_bucket = storage_client.bucket(temp_out_parts[0])
        temp_blob = temp_bucket.blob("/".join(temp_out_parts[1:]))

        logging.info(f"Uploading temporary WAV for async processing...")
        temp_blob.upload_from_string(content, content_type="audio/wav")

        # Inject context hints so STT is more likely to correctly output emails
        speech_context = speech.SpeechContext(phrases=['@', 'dot com', 'dot net', 'dot org'], boost=20.0)

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-US",
            enable_word_time_offsets=True,
            model="phone_call", 
            use_enhanced=True,
            speech_contexts=[speech_context]
        )
        audio = speech.RecognitionAudio(uri=temp_gcs_uri)
        
        try:
            logging.info("Starting Long Running Recognize (Async)...")
            operation = speech_client.long_running_recognize(config=config, audio=audio)
            
            # Request timeout handles the max limits of Cloud Run instances by default.
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
        logging.info(f"Scanning for PII using InfoTypes: {info_types}...")
        
        MAX_CHUNK_SIZE = 400000 
        findings = []
        
        try:
            parsed_info_types = [{"name": it.strip()} for it in info_types.split(",") if it.strip()]
            custom_info_types = [
                {
                    "info_type": {"name": "SPOKEN_DIGITS"},
                    # Match 13 to 19 digits potentially separated by spaces, dashes, or nothing.
                    "regex": {"pattern": r"\b(?:\d[\s-]*?){13,19}\b"},
                    "likelihood": dlp_v2.Likelihood.POSSIBLE,
                },
                {
                    "info_type": {"name": "SPOKEN_EMAIL"},
                    # Catch combinations of standard emails, spoken emails, and strongly fragmented emails
                    "regex": {"pattern": r"\b(?:[a-zA-Z0-9.-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|(?:[a-zA-Z0-9.-]+\s+){1,7}at\s+(?:[a-zA-Z0-9.-]+\s*){1,4}(?:dot|com|net|org|edu))\b"},
                    "likelihood": dlp_v2.Likelihood.POSSIBLE,
                },
                {
                    "info_type": {"name": "SPOKEN_DATE"},
                    # Catch combinations of spoken dates like 'oh four of twenty six' or '12 slash 25' or 'november tenth nineteen ninety'
                    "regex": {"pattern": r"\b(?:(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\w+(?:\s+(?:nineteen|twenty|\d{2,4})\s*\w*)?|\d{1,2}\s+slash\s+\d{2,4}|[oOaA]?\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:of|slash)?\s+(?:twenty\s+\w+|nineteen\s+\w+|\d{2,4}))\b"},
                    "likelihood": dlp_v2.Likelihood.POSSIBLE,
                }
            ]
            
            inspect_config = {
                "info_types": parsed_info_types,
                "custom_info_types": custom_info_types,
                "min_likelihood": dlp_v2.Likelihood.POSSIBLE,
                "include_quote": False,
            }

            transcript_len = len(full_transcript)
            for i in range(0, transcript_len, MAX_CHUNK_SIZE):
                chunk_text = full_transcript[i : i + MAX_CHUNK_SIZE]
                dlp_response = dlp_client.inspect_content(
                    request={
                        "parent": f"projects/{project_id}",
                        "inspect_config": inspect_config,
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
                
                logging.info(f"Transcript generated length: {len(full_transcript)} | Text: {full_transcript}")
                
                for finding in findings:
                    location = finding.location.codepoint_range
                    f_start = location.start
                    f_end = location.end
                    
                    logging.info(f"DLP Finding '{finding.info_type.name}' at range {f_start}-{f_end}")
                    
                    for w in word_map:
                        if (w["start_char"] < f_end) and (w["end_char"] > f_start):
                            s_ms = int(w["start_time"] * 1000)
                            e_ms = int(w["end_time"] * 1000)
                            logging.info(f" - Matched word '{w['word']}' [{w['start_char']}:{w['end_char']}] -> silencing {s_ms}ms to {e_ms}ms")
                            
                            silence = AudioSegment.silent(duration=e_ms - s_ms)
                            audio_seg = audio_seg[:s_ms] + silence + audio_seg[e_ms:]
                
                import uuid
                output_local_path = f"/tmp/redacted_{uuid.uuid4().hex}.mp3"
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

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
