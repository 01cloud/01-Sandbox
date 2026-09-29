#!/usr/bin/env bash
# ==============================================================================
# lib/01_logging.sh – Structured log functions
# All output goes to stdout so it can be piped/tee'd by the caller.
# ==============================================================================

log_info() {
  echo -e "${BLUE}${BOLD}[INFO]${NC}    $*"
}

log_success() {
  echo -e "${GREEN}${BOLD}[OK]${NC}      $*"
}

log_warn() {
  echo -e "${YELLOW}${BOLD}[WARN]${NC}    $*" >&2
}

log_error() {
  echo -e "${RED}${BOLD}[ERROR]${NC}   $*" >&2
}

log_step() {
  echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
  echo -e "${CYAN}${BOLD}  $*${NC}"
  echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}\n"
}
