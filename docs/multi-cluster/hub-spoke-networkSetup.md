# Libvirt/KVM Hub-Spoke Lab Network Setup

## Overview

This document records the setup of a 4-VM libvirt/KVM lab environment (`primaryhub`, `secondaryhub`, `spoke1`, `spoke2`) where each VM is assigned a distinct `/24` CIDR block via dedicated libvirt virtual networks.

| VM             | CIDR Block     | Libvirt Network      | Bridge Interface  | Gateway (bridge IP) |
|----------------|----------------|-----------------------|--------------------|----------------------|
| primaryhub     | 10.1.0.0/24    | `net-primaryhub`      | `virbr-phub`       | 10.1.0.1             |
| secondaryhub   | 10.2.0.0/24    | `net-secondaryhub`    | `virbr-shub`       | 10.2.0.1             |
| spoke1         | 10.3.0.0/24    | `net-spoke1`          | `virbr-spoke1`     | 10.3.0.1             |
| spoke2         | 10.4.0.0/24    | `net-spoke2`          | `virbr-spoke2`     | 10.4.0.1             |

Each libvirt network provides DHCP in the range `.100`–`.200`; static/manual guest IPs are assigned outside that range (e.g. `.10`).

---

## 1. Prerequisites — Install libvirt/KVM toolchain

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients virtinst bridge-utils virt-manager
sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt,kvm $USER
# log out / back in for group membership to apply
```

### Verify installation
```bash
virsh --version
virt-install --version
virsh list --all
virsh net-list --all
```

### Check hardware virtualization support
```bash
egrep -c '(vmx|svm)' /proc/cpuinfo   # >0 = supported
lsmod | grep kvm
```

---

## 2. Libvirt Connection URI — critical gotcha

`virsh`/`virt-install` can connect to two separate, isolated libvirt instances:

- `qemu:///session` — per-user, no networks/VMs by default (this was the accidental default in our shell)
- `qemu:///system` — system-wide, where virt-manager and all actual VMs/networks live

**Symptom encountered:** `virsh net-list --all` returned empty even after networks were "created," because commands were running against `qemu:///session` while the VMs lived under `qemu:///system`.

### Fix — set default connection permanently
```bash
export LIBVIRT_DEFAULT_URI="qemu:///system"
echo 'export LIBVIRT_DEFAULT_URI="qemu:///system"' >> ~/.zshrc   # use .zshrc if shell is zsh, .bashrc if bash
source ~/.zshrc
```

### Verify
```bash
virsh uri
# should return: qemu:///system
```

> **Note:** Determine your shell first with `echo $SHELL` before editing rc files — sourcing a `.bashrc` from `zsh` (or vice versa) produces syntax errors (`shopt: command not found`, etc.).

---

## 3. Creating a Libvirt Virtual Network (per CIDR)

Each VM's subnet is defined as an isolated/NAT libvirt network with its own bridge, gateway IP, and DHCP range.

### Example: net-spoke2 (10.4.0.0/24)
```bash
cat > /tmp/net-spoke2.xml << 'EOF'
<network>
  <name>net-spoke2</name>
  <bridge name="virbr-spoke2" stp="on" delay="0"/>
  <ip address="10.4.0.1" netmask="255.255.255.0">
    <dhcp>
      <range start="10.4.0.100" end="10.4.0.200"/>
    </dhcp>
  </ip>
</network>
EOF

sudo virsh net-define /tmp/net-spoke2.xml
sudo virsh net-start net-spoke2
sudo virsh net-autostart net-spoke2
```

Repeat with adjusted `name`, `bridge name`, and `ip address`/`range` for:
- `net-primaryhub` → 10.1.0.0/24
- `net-secondaryhub` → 10.2.0.0/24
- `net-spoke1` → 10.3.0.0/24

### Verify all networks
```bash
virsh net-list --all
```
Expected final state:
```
 Name              State    Autostart   Persistent
------------------------------------------------------
 default           active   yes         yes
 net-primaryhub    active   yes         yes
 net-secondaryhub  active   yes         yes
 net-spoke1        active   yes         yes
 net-spoke2        active   yes         yes
```

---

## 4. Creating a New VM Attached to a Custom Network

### Via virt-install (CLI)
```bash
virt-install \
  --name newvm \
  --memory 4096 \
  --vcpus 2 \
  --disk size=50 \
  --cdrom /home/berrybytes/Downloads/ubuntu-24.04.4-live-server-amd64.iso \
  --network network=net-spoke2,model=virtio \
  --os-variant ubuntu24.04
```

**Common errors & fixes:**
| Error | Cause | Fix |
|---|---|---|
| `unrecognized arguments: 24.04 LTS` | Unquoted display name with spaces passed to `--os-variant` | Use the short osinfo ID, e.g. `ubuntu24.04` (no spaces) |
| `Network not found: no network with matching name 'net-spoke2'` | Network doesn't exist yet, or command ran against the wrong libvirt connection | Create the network first (Section 3); confirm `virsh uri` is `qemu:///system` |

### Find valid `--os-variant` values
```bash
osinfo-query os | grep -i ubuntu
```

### Via virt-manager (GUI)
1. **Create a new virtual machine**
2. Proceed through ISO/memory/CPU/disk steps
3. On the final step, expand **Network selection**
4. Choose the target virtual network (e.g. `Virtual network 'net-spoke2'`)
5. Click **Finish**

---

## 5. Guest OS Network Configuration (Ubuntu Server installer / Netplan)

During the Ubuntu Server installer's **Network configuration** screen, the interface can be left on DHCP or set to Manual:

| Field | Value (example: spoke2) |
|---|---|
| IPv4 Method | Manual (or DHCP) |
| Subnet | 10.4.0.0/24 |
| Address | 10.4.0.10 |
| Gateway | 10.4.0.1 *(only if this VM needs to route out through the bridge)* |
| Name servers | 8.8.8.8 *(optional)* |

> This screen only configures the **guest's** IP — it has no effect unless the VM's NIC is already attached to the matching libvirt network (Section 3/6).

### Post-install static IP via Netplan
```bash
sudo nano /etc/netplan/00-installer-config.yaml
```
```yaml
network:
  ethernets:
    enp1s0:
      dhcp4: no
      addresses:
        - 10.4.0.10/24
      nameservers:
        addresses: [8.8.8.8]
  version: 2
```
```bash
sudo netplan apply
```

### Verify inside guest
```bash
ip a
ip route
```

---

## 6. Migrating an Existing VM to a Different CIDR/Network

Used to move `primaryhub` from the `default` network onto `net-primaryhub` (10.1.0.0/24).

### Step 1 — Check current attachment
```bash
virsh domiflist primaryhub
```
```
 Interface   Type      Source    Model    MAC
-------------------------------------------------------------
 vnet1       network   default   virtio   52:54:00:28:91:b7
```

### Step 2 — Ensure target network exists
```bash
virsh net-list --all
```
(create via Section 3 if missing)

### Step 3 — Shut down the VM
```bash
virsh shutdown primaryhub
virsh list --all      # confirm "shut off"
```

### Step 4 — Detach old interface, attach new one
```bash
virsh detach-interface primaryhub --type network --mac 52:54:00:28:91:b7 --config
virsh attach-interface primaryhub network net-primaryhub --model virtio --config
```
> A new MAC address is auto-generated on attach — this is expected.

### Step 5 — Verify and start
```bash
virsh domiflist primaryhub
virsh start primaryhub
```

### Step 6 — Confirm guest picked up the new subnet
```bash
ip a
```
Result observed:
```
2: enp1s0: ...
    inet 10.1.0.145/24 metric 100 brd 10.1.0.255 scope global dynamic enp1s0
```

### Step 7 — (Optional but recommended for hubs) Convert to static IP
```bash
sudo nano /etc/netplan/00-installer-config.yaml
```
```yaml
network:
  ethernets:
    enp1s0:
      dhcp4: no
      addresses:
        - 10.1.0.10/24
      nameservers:
        addresses: [8.8.8.8]
  version: 2
```
```bash
sudo netplan apply
```

This same procedure was repeated for:
- **secondaryhub** → `net-secondaryhub` (10.2.0.0/24), static IP `10.2.0.10/24`
- **spoke1** → `net-spoke1` (10.3.0.0/24), static IP `10.3.0.10/24`

---

## 7. Renaming a VM

Used to rename `newvm` to `spoke2` after installation.

```bash
virsh shutdown newvm
virsh list --all               # confirm "shut off"
virsh domrename newvm spoke2
virsh start spoke2
```

**Caveats:**
- Only renames the libvirt domain — does **not** rename the disk image file or change the guest's internal hostname.
- To also update the guest hostname:
  ```bash
  sudo hostnamectl set-hostname spoke2
  ```
  then update `/etc/hosts` accordingly.
- Disk file (e.g. `newvm.qcow2`) can optionally be renamed for consistency:
  ```bash
  virsh domblklist spoke2
  ```

---

## 8. Verification Commands Reference

| Purpose | Command |
|---|---|
| List all libvirt networks | `virsh net-list --all` |
| Show VM's NIC/network attachment | `virsh domiflist <vm-name>` |
| Show full VM XML (interface detail) | `virsh dumpxml <vm-name> \| grep -A 3 "<interface"` |
| List VMs and their state | `virsh list --all` |
| Check active DHCP leases on a network | `virsh net-dhcp-leases <network-name>` |
| Check current libvirt connection | `virsh uri` |
| Guest: show IP/interfaces | `ip a` |
| Guest: show routing table | `ip route` |

---

## 9. Known Issues Encountered & Resolutions

| Issue | Root Cause | Resolution |
|---|---|---|
| `virsh net-list --all` returns empty | Session was on `qemu:///session`, not `qemu:///system` | Set `LIBVIRT_DEFAULT_URI=qemu:///system` in shell rc file |
| `command not found: shopt` after `source ~/.bashrc` | Shell is zsh, not bash; `.bashrc` has bash-only syntax | Add exports to `~/.zshrc` instead; never source `.bashrc` from zsh |
| `virt-install --os-variant Ubuntu 24.04 LTS` fails | Unquoted value with spaces split into separate CLI args | Use short-form variant ID: `--os-variant ubuntu24.04` |
| `Network not found: no network with matching name` | Network XML never defined, or defined under wrong connection URI | Confirm target network exists via `virsh --connect qemu:///system net-list --all`; create if missing |

---

## 10. Outstanding / Next Steps

- [ ] Convert spoke1 and spoke2 to static IPs (spoke2 currently likely still DHCP)
- [ ] If true hub-spoke routing is required (spokes reaching each other via hubs), configure:
  - Second NIC on each hub connecting to the relevant spoke network(s)
  - `net.ipv4.ip_forward=1` on hub VMs (persist in `/etc/sysctl.conf`)
  - Static routes on spokes pointing to their hub's IP as gateway for other subnets
  - `iptables`/`nftables` rules if NAT or filtering is needed between segments
- [ ] Rename disk image files / guest hostnames for full consistency after `virsh domrename`
