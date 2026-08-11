variable "aws_region" {
  description = "AWS region — ap-south-1 is Mumbai (closest to India)"
  type        = string
  default     = "ap-south-1"
}

variable "env" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "db_password" {
  description = "RDS PostgreSQL master password"
  type        = string
  sensitive   = true
}

variable "qdrant_url" {
  description = "Qdrant Cloud cluster URL (free tier from cloud.qdrant.io)"
  type        = string
}

variable "qdrant_api_key" {
  description = "Qdrant Cloud API key"
  type        = string
  sensitive   = true
}

variable "litellm_master_key" {
  description = "LiteLLM proxy master key"
  type        = string
  sensitive   = true
  default     = "sk-vibeforge-litellm-dev-change-me"
}
