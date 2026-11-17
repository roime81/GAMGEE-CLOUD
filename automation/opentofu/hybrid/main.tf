terraform {
  required_version = ">= 1.9.0"
}

# This workspace is intentionally provider-neutral. Add a pinned cloud provider
# in a reviewed environment-specific workspace before planning remote resources.
