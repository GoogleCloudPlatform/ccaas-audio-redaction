import urllib.request
from google.cloud import speech

client = speech.SpeechClient()
audio = speech.RecognitionAudio(uri="gs://prj-inc-amber-raw-audio/ccai/call_recording/call-1076.mp3_raw_moved")

speech_context = speech.SpeechContext(phrases=['@', 'dot com', 'dot net', 'dot org'], boost=20.0)

config = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.MP3,
    sample_rate_hertz=16000,
    language_code="en-US",
    enable_word_time_offsets=True,
    use_enhanced=True,
    model="latest_long",
    speech_contexts=[speech_context]
)

operation = client.long_running_recognize(config=config, audio=audio)
print("Waiting for STT...")
response = operation.result(timeout=600)

full_transcript = " ".join([result.alternatives[0].transcript for result in response.results])
print("--- TRANSCRIPT ---")
print(full_transcript)
