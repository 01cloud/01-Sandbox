#!/usr/bin/env bash
# ==============================================================================
# build-and-load-scanners.sh
#
# Builds language-specific scanner images from code-interpreter/dockerfiles/
# and loads them into KinD clusters (e.g. spoke1, spoke2, primaryhub, secondaryhub).
#
# Usage:
#   ./build-and-load-scanners.sh                 # Builds all and loads into running clusters
#   ./build-and-load-scanners.sh --build-only    # Only builds docker images
#   ./build-and-load-scanners.sh --load-only     # Only loads existing images into clusters
#   ./build-and-load-scanners.sh --lang python   # Build and load specific language
#   ./build-and-load-scanners.sh --clusters spoke1,spoke2
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_INTERPRETER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKERFILES_DIR="${CODE_INTERPRETER_DIR}/dockerfiles"

# Colors for log output
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info()    { echo -e "${CYAN}[INFO]${NC}    $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}      $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}    $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC}   $*"; }

# Supported languages and their respective Dockerfile
ALL_LANGUAGES=(
  "python:Dockerfile.python"
  "go:Dockerfile.go"
  "java:Dockerfile.java"
  "node:Dockerfile.node"
  "k8s:Dockerfile.k8s"
  "rust:Dockerfile.rust"
  "shell:Dockerfile.shell"
  "cpp:Dockerfile.cpp"
  "ruby:Dockerfile.ruby"
  "terraform:Dockerfile.terraform"
)

DO_BUILD=true
DO_LOAD=true
TARGET_LANG=""
TARGET_CLUSTERS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only)
      DO_LOAD=false
      shift
      ;;
    --load-only)
      DO_BUILD=false
      shift
      ;;
    --lang)
      TARGET_LANG="$2"
      shift 2
      ;;
    --clusters)
      TARGET_CLUSTERS="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--build-only] [--load-only] [--lang <language>] [--clusters <cluster1,cluster2>]"
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Filter languages if specified
SELECTED_LANGUAGES=()
if [ -n "$TARGET_LANG" ]; then
  found=false
  for item in "${ALL_LANGUAGES[@]}"; do
    lang="${item%%:*}"
    if [ "$lang" == "$TARGET_LANG" ]; then
      SELECTED_LANGUAGES+=("$item")
      found=true
      break
    fi
  done
  if [ "$found" = false ]; then
    log_error "Unknown language '$TARGET_LANG'. Available: python, go, java, node, k8s, rust, shell, cpp, ruby, terraform"
    exit 1
  fi
else
  SELECTED_LANGUAGES=("${ALL_LANGUAGES[@]}")
fi

# Detect running KinD clusters
RUNNING_CLUSTERS=()
if [ -n "$TARGET_CLUSTERS" ]; then
  IFS=',' read -ra CLUST_ARRAY <<< "$TARGET_CLUSTERS"
  RUNNING_CLUSTERS=("${CLUST_ARRAY[@]}")
else
  KIND_OUT=$(kind get clusters 2>/dev/null || true)
  for c in spoke1 spoke2 primaryhub secondaryhub; do
    if echo "$KIND_OUT" | grep -q "^${c}\$"; then
      RUNNING_CLUSTERS+=("$c")
    fi
  done
fi

log_info "Target languages: $(for i in "${SELECTED_LANGUAGES[@]}"; do echo -n "${i%%:*} "; done)"
if [ "$DO_LOAD" = true ]; then
  log_info "Target clusters : ${RUNNING_CLUSTERS[*]:-none}"
fi

# 1. Build Docker Images
if [ "$DO_BUILD" = true ]; then
  log_info "══════════════════════════════════════════════════════════════════"
  log_info "Building Language-Specific Scanner Images..."
  log_info "══════════════════════════════════════════════════════════════════"

  for item in "${SELECTED_LANGUAGES[@]}"; do
    lang="${item%%:*}"
    df="${item##*:}"
    dockerfile_path="${DOCKERFILES_DIR}/${df}"

    if [ ! -f "$dockerfile_path" ]; then
      log_error "Dockerfile not found: $dockerfile_path"
      continue
    fi

    tag_dev="199012118961/01sandbox-scanner-${lang}:dev"
    tag_release="01community/01sandbox-scanner-${lang}:1.0.0"

    if docker image inspect "$tag_dev" >/dev/null 2>&1 && docker image inspect "$tag_release" >/dev/null 2>&1; then
      log_success "Images ${tag_dev} and ${tag_release} already exist locally – skipping build."
    else
      log_info "Building scanner image for '${lang}' (${df})..."
      if docker build -f "$dockerfile_path" \
        -t "$tag_dev" \
        -t "$tag_release" \
        "$CODE_INTERPRETER_DIR"; then
        log_success "Built ${tag_dev} & ${tag_release}"
      else
        log_error "Failed to build image for ${lang}"
        exit 1
      fi
    fi
  done
fi

# 2. Load into KinD clusters
if [ "$DO_LOAD" = true ]; then
  if [ ${#RUNNING_CLUSTERS[@]} -eq 0 ]; then
    log_warn "No running KinD clusters found. Skipping cluster loading."
  else
    log_info "══════════════════════════════════════════════════════════════════"
    log_info "Loading Scanner Images into KinD Clusters: ${RUNNING_CLUSTERS[*]}"
    log_info "══════════════════════════════════════════════════════════════════"

    for cluster in "${RUNNING_CLUSTERS[@]}"; do
      log_info "── Cluster: ${cluster} ──"
      for item in "${SELECTED_LANGUAGES[@]}"; do
        lang="${item%%:*}"
        tag_dev="199012118961/01sandbox-scanner-${lang}:dev"
        tag_release="01community/01sandbox-scanner-${lang}:1.0.0"

        # Check if dev tag is already in cluster
        if docker exec "${cluster}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-scanner-${lang}.*dev"; then
          log_success "${cluster}: ${tag_dev} already loaded (skipping)"
        else
          log_info "${cluster}: loading ${tag_dev}..."
          if ! kind load docker-image "$tag_dev" --name "$cluster" 2>/dev/null; then
            docker save "$tag_dev" | docker exec -i "${cluster}-control-plane" ctr -n k8s.io images import --local -
          fi
        fi

        # Check if release tag is already in cluster
        if docker exec "${cluster}-control-plane" crictl images 2>/dev/null | grep -q "01sandbox-scanner-${lang}.*1.0.0"; then
          log_success "${cluster}: ${tag_release} already loaded (skipping)"
        else
          log_info "${cluster}: loading ${tag_release}..."
          if ! kind load docker-image "$tag_release" --name "$cluster" 2>/dev/null; then
            docker save "$tag_release" | docker exec -i "${cluster}-control-plane" ctr -n k8s.io images import --local -
          fi
        fi
      done
    done
  fi
fi

log_success "All scanner images processed successfully!"
