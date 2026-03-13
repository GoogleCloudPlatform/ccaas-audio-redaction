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

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- 1. STORAGE BUCKETS ---
resource "google_storage_bucket" "raw_bucket" {
  name     = "${var.project_id}-raw-audio"
  location = var.region
  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "archive_bucket" {
  name     = "${var.project_id}-audio-archive"
  location = var.region
  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "dataflow_bucket" {
  name     = "${var.project_id}-dataflow-system"
  location = var.region
  uniform_bucket_level_access = true
}

# --- 2. NETWORKING (VPC) ---
resource "google_compute_network" "vpc" {
  name                    = "ccai-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "ccai-subnet"
  ip_cidr_range            = "10.0.0.0/24"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

resource "google_compute_firewall" "allow_internal" {
  name    = "allow-dataflow-internal"
  network = google_compute_network.vpc.name
  allow {
    protocol = "tcp"
    ports    = ["12345-12346"]
  }
  source_ranges = ["10.0.0.0/24"]
}

# --- 3. SERVICE ACCOUNT ---
resource "google_service_account" "worker_sa" {
  account_id   = "dataflow-worker-sa"
  display_name = "Dataflow Worker Service Account"
}

# IAM Role Bindings for the Service Account
resource "google_project_iam_member" "sa_roles" {
  for_each = toset([
    "roles/dataflow.worker",
    "roles/dataflow.developer",
    "roles/storage.objectAdmin",
    "roles/dlp.user",
    "roles/speech.client",
    "roles/contactcenterinsights.viewer",
    "roles/iam.serviceAccountUser"
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

# --- 7. PUB/SUB SUBSCRIPTION (INSIGHTS) ---
# Assuming topic 'insights-audio-redaction' exists in the project
resource "google_pubsub_subscription" "insights_subscription" {
  count = var.deploy_trigger == "INSIGHTS" ? 1 : 0
  name  = "insights-trigger-subscription"
  topic = "projects/${var.project_id}/topics/insights-audio-redaction"

  push_config {
    push_endpoint = google_cloud_run_service.insights_trigger[0].status[0].url
    
    oidc_token {
      service_account_email = google_service_account.worker_sa.email
    }
  }
}

# Allow Pub/Sub to create tokens for the Worker SA
data "google_project" "project" {}

resource "google_service_account_iam_member" "pubsub_token_creator" {
  count              = var.deploy_trigger == "INSIGHTS" ? 1 : 0
  service_account_id = google_service_account.worker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# Allow Worker SA to invoke the Cloud Run service
resource "google_cloud_run_service_iam_member" "insights_invoker" {
  count    = var.deploy_trigger == "INSIGHTS" ? 1 : 0
  location = google_cloud_run_service.insights_trigger[0].location
  project  = google_cloud_run_service.insights_trigger[0].project
  service  = google_cloud_run_service.insights_trigger[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker_sa.email}"
}

# --- 4. CLOUD RUN (GCS TRIGGER) ---
resource "google_cloud_run_service" "gcs_trigger" {
  count    = var.deploy_trigger == "GCS" ? 1 : 0
  name     = "redactor-gcs-trigger"
  location = var.region

  template {
    spec {
      service_account_name = google_service_account.worker_sa.email
      containers {
        # Image built from dataflow-worker/cloud-run-gcs-trigger
        image = "gcr.io/${var.project_id}/redactor-gcs-trigger:latest"
        
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "ARCHIVE_BUCKET"
          value = google_storage_bucket.archive_bucket.name
        }
        env {
          name  = "TEMPLATE_BUCKET"
          value = google_storage_bucket.dataflow_bucket.name
        }
        env {
          name  = "DLP_TEMPLATE"
          value = var.dlp_template_id
        }
        env {
          name  = "REGION"
          value = var.region
        }
        env {
          name  = "SUBNET"
          value = google_compute_subnetwork.subnet.name
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }
}

# --- 5. CLOUD RUN (INSIGHTS TRIGGER) ---
resource "google_cloud_run_service" "insights_trigger" {
  count    = var.deploy_trigger == "INSIGHTS" ? 1 : 0
  name     = "redactor-insights-trigger"
  location = var.region

  template {
    spec {
      service_account_name = google_service_account.worker_sa.email
      containers {
        # Image built from dataflow-worker/cloud-run-insights-trigger
        image = "gcr.io/${var.project_id}/redactor-insights-trigger:latest"
        
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "ARCHIVE_BUCKET"
          value = google_storage_bucket.archive_bucket.name
        }
        env {
          name  = "TEMPLATE_BUCKET"
          value = google_storage_bucket.dataflow_bucket.name
        }
        env {
          name  = "DLP_TEMPLATE"
          value = var.dlp_template_id
        }
        env {
          name  = "REGION"
          value = var.region
        }
        env {
          name  = "SUBNET"
          value = google_compute_subnetwork.subnet.name
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }
}

# --- 6. EVENTARC TRIGGER (GCS) ---
resource "google_eventarc_trigger" "gcs_trigger" {
  count           = var.deploy_trigger == "GCS" ? 1 : 0
  name     = "start-redaction-trigger"
  location = var.region
  service_account = google_service_account.worker_sa.email

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.raw_bucket.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_service.gcs_trigger[0].name
      region  = var.region
    }
  }
}