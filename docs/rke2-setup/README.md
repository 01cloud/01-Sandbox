# RKE2 Multi-Network Setup & Troubleshooting

This guide explains how to handle network changes (e.g., moving your machine between offices or home networks) and troubleshoot common RKE2 cluster issues.

---

## The Network Migration Issue

RKE2 binds its control plane components (like `etcd`, `kube-apiserver`, and `kubelet`) and cryptographic certificates to the active Local Area Network (LAN) IP address of your machine.

When you connect to a new network:
1. **IP Change:** Your machine receives a new local IP address via DHCP.
2. **Configuration Mismatch:** RKE2's config files and certificates still reference the old IP address.
3. **Etcd Failure:** The database detects that the new node IP does not match the IP registered in the database, throwing a fatal error: `this server is not a member of the etcd cluster`.

---

## How to Resolve Network Migration Issues (Standard Methods)

When moving your local development computer to another office or network, choose one of the following methods:

### Method A: Clean Re-initialization (Recommended for Local Dev)
If you do not have critical state saved in Kubernetes Persistent Volumes (PVs) and want to spin up the sandbox quickly, run the RKE2 setup script with the `--init` flag:
```bash
sudo docs/rke2-setup/setup-rke2.sh server --init
```
**What this does:**
- Detects the new network interface IP.
- Wipes the old database state and clears cached network resources.
- Automatically regenerates `config.yaml` with the correct local configuration.
- Installs and starts RKE2 on the new IP address.

### Method B: Manual Configuration Update (To Preserve Data)
If you have data on the cluster you want to preserve:

1. **Identify your new LAN IP:**
   ```bash
   ip route get 1.1.1.1
   ```
2. **Update the RKE2 config:**
   Open `/etc/rancher/rke2/config.yaml` and update any hardcoded IP addresses (e.g., `node-ip`, `tls-san`) to match the new LAN IP.
3. **Clean up etcd mismatch (if stuck):**
   If the database cannot resolve the host change, you must clear the etcd state (note: this resets internal database records but preserves most file mount structures):
   ```bash
   sudo systemctl stop rke2-server
   sudo rm -rf /var/lib/rancher/rke2/server/db/etcd
   ```
4. **Restart the service:**
   ```bash
   sudo systemctl start rke2-server
   ```

---

## Permanent Solution: Persistent Virtual Dummy Interface

To avoid re-running the configuration steps above every time you change physical networks, you can use a **virtual dummy network interface** (`rke2-dummy`) with a static IP address (`192.168.99.1`) that remains active on your local computer regardless of the network you connect to.

### How it is configured:
1. **Systemd Service:** A systemd unit `/etc/systemd/system/rke2-dummy-ip.service` initializes the `rke2-dummy` link and assigns `192.168.99.1/24` to it automatically at boot (before `rke2-server` starts).
2. **RKE2 Bind:** The configuration file `/etc/rancher/rke2/config.yaml` is set up with:
   - `node-ip: "192.168.99.1"`
   - `node-external-ip: "192.168.99.1"`
   - `advertise-address: "192.168.99.1"`
3. **Outcome:** When you switch physical networks, RKE2 remains bound to the virtual interface IP `192.168.99.1` and starts up with zero reconfiguration.

### Managing the Virtual Interface
*   **Check interface status:**
    ```bash
    ip addr show dev rke2-dummy
    ```
*   **Enable/Start the service:**
    ```bash
    sudo systemctl enable --now rke2-dummy-ip.service
    ```
*   **Disable/Stop the service:**
    ```bash
    sudo systemctl stop rke2-dummy-ip.service
    ```

---

## User-space Kubeconfig Access (No sudo/copy required)

RKE2's kubeconfig is written to `/etc/rancher/rke2/rke2.yaml` with global read permissions (`0644`).

We configured your shell profiles (`~/.zshrc` and `~/.bashrc`) to export this path directly:
```bash
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
```

**Benefits:**
- You can run `kubectl` or `helm` directly from your user terminal without using `sudo`.
- You never need to copy `/etc/rancher/rke2/rke2.yaml` to `~/.kube/config` or run `chown` when re-initializing the cluster.

---

## Common Gotchas

### 1. Loopback Address Binding
Do not bind `node-ip` to loopback addresses like `127.0.0.1` in `/etc/rancher/rke2/config.yaml` when running multi-component clusters. This causes port conflicts on the peer port (`2380`) during initialization.

### 2. MetalLB IP Pool Allocation
If you use MetalLB for local ingress/load balancer simulation (e.g., routing to `10.0.8.9`), make sure the virtual IP range does not conflict with the physical office network gateway or host interface subnet.
