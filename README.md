# 🚀 Speech Redaction Framework (SRF)

Welcome to the Speech Redaction Framework (SRF), a solution for redacting Personally Identifiable Information (PII) from audio files on Google Cloud.

This repository contains the major versions of the pipeline architecture:

## [Version 1: Dataflow (Apache Beam) Pipeline](./v1)
The original `v1` architecture leverages a Dataflow worker pipeline to process and redact audio files. It is launched via Cloud Run triggers (GCS or Insights).

*   **Best for**: Highly customizable, distributed processing.
*   **Documentation**: [v1/README.md](./v1/README.md)

## [Version 2: Cloud Run Orchestrator](./v2)
The newer `v2` architecture is a fully serverless Python pipeline running purely on Cloud Run. It leverages the native streaming capabilities of DLP to scan and redact PII from audio files without maintaining Dataflow infrastructure.

*   **Best for**: Lower maintenance overhead, faster start times, fully serverless execution.
*   **Documentation**: [v2/README.md](./v2/README.md)

---

## Getting Started
Please navigate into either the `v1` or `v2` directory to read their respective architecture guides and deployment instructions.

## License & Contributing
Please see `LICENSE` and `CONTRIBUTING.md` for project details.
