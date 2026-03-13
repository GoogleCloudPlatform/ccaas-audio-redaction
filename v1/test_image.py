import urllib.request
import json
import base64
import subprocess

def run(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8').strip()

project = run("gcloud config get-value project")
token = run("gcloud auth print-access-token")

req = urllib.request.Request(f"https://gcr.io/v2/{project}/redactor-orchestrator/manifests/latest")
req.add_header("Authorization", f"Bearer {token}")
req.add_header("Accept", "application/vnd.docker.distribution.manifest.v2+json")

try:
    res = urllib.request.urlopen(req)
    data = json.loads(res.read())
    print("Latest digest:", data.get('config', {}).get('digest'))
except Exception as e:
    print("Error:", e)
