#!/usr/bin/env bash
# ==============================================================================
# lib/phase_02_network.sh – Docker transit network + WireGuard key generation
# ==============================================================================

_check_phase_02() {
  docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$" || return 1

  local entity
  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    [ -s "${WG_DIR}/${entity}.key" ] && [ -s "${WG_DIR}/${entity}.pub" ] || return 1
  done

  # Load keypairs into memory so later phases can use them even when skipped
  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    WG_PRIV[$entity]=$(tr -d '\r\n' < "${WG_DIR}/${entity}.key")
    WG_PUB[$entity]=$(tr -d '\r\n'  < "${WG_DIR}/${entity}.pub")
  done
  return 0
}

_do_phase_02_transit_network_and_wg_keys() {
  if ! docker network ls --format '{{.Name}}' | grep -q "^${TRANSIT_NET_NAME}$"; then
    log_info "Creating Docker transit network ($TRANSIT_SUBNET)..."
    docker network create \
      --driver bridge \
      --subnet "$TRANSIT_SUBNET" \
      --opt "com.docker.network.bridge.name"="br-01transit" \
      "$TRANSIT_NET_NAME"
  else
    log_info "Transit network '$TRANSIT_NET_NAME' already exists."
  fi

  for entity in gateway primaryhub secondaryhub spoke1 spoke2; do
    if [ ! -s "${WG_DIR}/${entity}.key" ] || [ ! -s "${WG_DIR}/${entity}.pub" ]; then
      log_info "Generating WireGuard keypair for $entity..."
      read -r priv pub < <(python3 -c "
from cryptography.hazmat.primitives.asymmetric import x25519
import base64
k = x25519.X25519PrivateKey.generate()
print(f'{base64.b64encode(k.private_bytes_raw()).decode()} {base64.b64encode(k.public_key().public_bytes_raw()).decode()}')
")
      echo "$priv" > "${WG_DIR}/${entity}.key"
      echo "$pub"  > "${WG_DIR}/${entity}.pub"
    fi
    WG_PRIV[$entity]=$(tr -d '\r\n' < "${WG_DIR}/${entity}.key")
    WG_PUB[$entity]=$(tr -d '\r\n'  < "${WG_DIR}/${entity}.pub")
  done

  log_success "WireGuard keypairs ready for all 5 entities."
}

phase_02_transit_network_and_wg_keys() {
  run_phase "02" "Transit Network & WireGuard Key Generation" _check_phase_02 _do_phase_02_transit_network_and_wg_keys
}
