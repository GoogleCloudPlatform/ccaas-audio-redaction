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

variable "project_id" {
  description = "The Google Cloud Project ID where resources will be deployed"
  type        = string
}

variable "region" {
  description = "The GCP Region for all resources (e.g., us-central1)"
  type        = string
  default     = "us-central1"
}

variable "dlp_template_id" {
  description = "The Full Resource Name of the Cloud DLP Inspection Template (e.g., projects/PROJECT_ID/locations/global/inspectTemplates/TEMPLATE_ID)"
  type        = string
}

variable "deploy_trigger" {
  description = "Which trigger to deploy: 'GCS' for Eventarc/Storage, 'INSIGHTS' for Pub/Sub/CCAI Insights, or 'NONE' for infra only."
  type        = string
  validation {
    condition     = contains(["GCS", "INSIGHTS", "NONE"], var.deploy_trigger)
    error_message = "The deploy_trigger value must be 'GCS', 'INSIGHTS', or 'NONE'."
  }
}