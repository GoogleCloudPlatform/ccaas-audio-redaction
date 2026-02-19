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

# Use the official Flex Template base image
FROM gcr.io/dataflow-templates-base/python310-template-launcher-base

ARG WORKDIR=/dataflow/template
RUN mkdir -p ${WORKDIR}
WORKDIR ${WORKDIR}

# --- CRITICAL: Install FFmpeg ---
# We install this here so this SINGLE image can handle audio processing
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# --- Install Python Dependencies ---
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# --- Setup the Application ---
COPY main.py .

# Tell Dataflow where the entry point is
ENV FLEX_TEMPLATE_PYTHON_PY_FILE="${WORKDIR}/main.py"