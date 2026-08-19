#!/usr/bin/env bash
# Samba только для подсети WireGuard 10.10.0.0/24, бинд на wg0.
# Запускать после рабочего туннеля, от root.
set -euo pipefail

SHARE_DIR="${SHARE_DIR:-/srv/share}"
SHARE_NAME="${SHARE_NAME:-share}"
SMB_USER="${SMB_USER:-wgshare}"
WG_ADDR="${WG_ADDR:-10.10.0.1}"

if [[ $(id -u) -ne 0 ]]; then
  echo "Запусти от root: sudo $0"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

mkdir -p "$SHARE_DIR"
chmod 2770 "$SHARE_DIR"

if ! id "$SMB_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SMB_USER"
fi
chown "${SMB_USER}:${SMB_USER}" "$SHARE_DIR"

if [[ ! -f /etc/samba/smb.conf.bak-wgstand ]]; then
  cp /etc/samba/smb.conf /etc/samba/smb.conf.bak-wgstand
fi

cat > /etc/samba/smb.conf <<EOF
[global]
   workgroup = WORKGROUP
   server string = wg-share
   security = user
   map to guest = never
   smb ports = 445
   interfaces = wg0 127.0.0.1
   bind interfaces only = yes
   hosts allow = 10.10.0.0/24 127.0.0.1
   hosts deny = 0.0.0.0/0
   disable netbios = yes
   smb3 unix extensions = yes
   log file = /var/log/samba/log.%m
   max log size = 1000

[${SHARE_NAME}]
   path = ${SHARE_DIR}
   browseable = yes
   read only = no
   valid users = ${SMB_USER}
   force user = ${SMB_USER}
   create mask = 0664
   directory mask = 0775
EOF

echo
echo "Пароль Samba для пользователя ${SMB_USER} (его введут на Windows в net use):"
smbpasswd -a "$SMB_USER"
smbpasswd -e "$SMB_USER"

systemctl enable --now smbd
systemctl restart smbd

# Не открываем 445 в интернет. Только с wg0 это уже bind interfaces.
echo
echo "=== шара готова ==="
echo "Путь на диске: $SHARE_DIR"
echo "Адрес с Windows: \\\\${WG_ADDR}\\${SHARE_NAME}"
echo "Логин: ${SMB_USER}"
echo
echo "Проверка с VPS:"
echo "  ss -lntp | grep 445"
echo "  smbclient -L localhost -U ${SMB_USER}"
echo
echo "С Windows (после WireGuard):"
echo "  ping ${WG_ADDR}"
echo "  net use S: \\\\${WG_ADDR}\\${SHARE_NAME} /user:${SMB_USER} * /persistent:yes"
