#!/usr/bin/env bash
# Отдельный 10 ГБ диск под шару, папка vpn с .conf и README.
# Запускать на VPS от root, когда Samba и WireGuard уже работают.
set -euo pipefail

IMG=/var/lib/wg-share.img
MNT=/srv/share
VPN="$MNT/vpn"
SIZE=10G

if [[ $(id -u) -ne 0 ]]; then
  echo "sudo $0"
  exit 1
fi

if ! ip -4 addr show wg0 | grep -q '10.10.0.1'; then
  echo "Сначала подними WireGuard (wg0 / 10.10.0.1)"
  exit 1
fi

mkdir -p /root/share-bak
if [[ -d "$MNT" ]]; then
  cp -a "$MNT"/. /root/share-bak/ 2>/dev/null || true
fi

if ! mountpoint -q "$MNT"; then
  umount "$MNT" 2>/dev/null || true
fi

if [[ ! -f "$IMG" ]]; then
  fallocate -l "$SIZE" "$IMG"
  mkfs.ext4 -L wgshare -F "$IMG"
fi

mkdir -p "$MNT"
if ! grep -q "$IMG" /etc/fstab; then
  echo "$IMG $MNT ext4 loop,defaults 0 0" >> /etc/fstab
fi
mount "$MNT" 2>/dev/null || mount -o loop "$IMG" "$MNT"

mkdir -p "$VPN"
if [[ -d /root/share-bak ]]; then
  cp -a /root/share-bak/. "$MNT"/ 2>/dev/null || true
fi
if [[ -d /etc/wireguard/clients ]]; then
  cp -f /etc/wireguard/clients/*.conf "$VPN"/ 2>/dev/null || true
fi
chmod 644 "$VPN"/*.conf 2>/dev/null || true

if [[ -f /opt/wg-stand/vpn-README.txt ]]; then
  cp -f /opt/wg-stand/vpn-README.txt "$VPN/README.txt"
fi

id wgshare >/dev/null 2>&1 && chown -R wgshare:wgshare "$MNT"

if [[ -f /etc/samba/smb.conf ]]; then
  python3 - <<'PY'
from pathlib import Path
import re
p = Path("/etc/samba/smb.conf")
t = p.read_text()
if not re.search(r"(?m)^\s*max disk size\s*=", t):
    t = t.replace("[share]", "[share]\n   max disk size = 10240", 1)
else:
    t = re.sub(r"(?m)^\s*max disk size\s*=.*$", "   max disk size = 10240", t)
p.write_text(t)
PY
  systemctl restart smbd
fi

df -h "$MNT"
ls -l "$VPN"
echo
echo "Готово. На Windows обнови S: (F5). Папка vpn, лимит 10 ГБ."
