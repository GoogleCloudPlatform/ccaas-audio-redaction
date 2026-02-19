
import subprocess
import sys

def generate_hashes():
    # Install pip-tools if not present (using user install to avoid permission issues)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "pip-tools"])
    
    # Define requirements
    requirements = [
        "google-cloud-speech",
        "google-cloud-dlp",
        "google-cloud-storage",
        "pydub",
        "ffmpeg-python"
    ]
    
    # Write input file
    with open("requirements.in", "w") as f:
        f.write("\n".join(requirements))
    
    # Run pip-compile
    # We use python -m piptools to run it
    subprocess.check_call([
        sys.executable, "-m", "piptools", "compile", 
        "--generate-hashes", 
        "requirements.in"
    ])
    
    print("Hash generation complete! content is in requirements.txt")

if __name__ == "__main__":
    generate_hashes()
