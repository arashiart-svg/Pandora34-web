#!/usr/bin/env bash
# WireGuard-сервер на Ubuntu. Запускать один раз, от root.
# Не затирает существующий /etc/wireguard/wg0.conf без --force.
set -euo pipefail

WG_DIR=/etc/wireguard
WG_IF=wg0
WG_PORT="${WG_PORT:-51820}"
WG_NET="${WG_NET:-10.10.0.0/24}"
WG_ADDR="${WG_ADDR:-10.10.0.1/24}"
ENDPOINT_IP="${ENDPOINT_IP:-}"

if [[ $(id -u) -ne 0 ]]; then
  echo "Запусти от root: sudo $0"
  exit 1
fi

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y wireguard wireguard-tools qrencode

mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"

if [[ -f "$WG_DIR/${WG_IF}.conf" && "$FORCE" -ne 1 ]]; then
  echo "Уже есть $WG_DIR/${WG_IF}.conf — не трогаю. Перезапись: $0 --force"
  wg show || true
  exit 0
fi

umask 077
wg genkey | tee "$WG_DIR/server_private.key" | wg pubkey > "$WG_DIR/server_public.key"
chmod 600 "$WG_DIR/server_private.key"

SERVER_PRIV=$(cat "$WG_DIR/server_private.key")
SERVER_PUB=$(cat "$WG_DIR/server_public.key")

if [[ -z "$ENDPOINT_IP" ]]; then
  ENDPOINT_IP=$(curl -4 -fsS --max-time 8 ifconfig.me || true)
fi
echo "$ENDPOINT_IP" > "$WG_DIR/endpoint_ip"
echo "$WG_NET" > "$WG_DIR/network"
echo "$WG_PORT" > "$WG_DIR/listen_port"
echo 2 > "$WG_DIR/next_ip"
: > "$WG_DIR/peers.tsv"

cat > "$WG_DIR/${WG_IF}.conf" <<EOF
[Interface]
Address = ${WG_ADDR}
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIV}
SaveConfig = false
MTU = 1280
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT
EOF

chmod 600 "$WG_DIR/${WG_IF}.conf"
sysctl -w net.ipv4.ip_forward=1 >/dev/null
grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.conf || echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf

systemctl enable --now "wg-quick@${WG_IF}"
systemctl restart "wg-quick@${WG_IF}"

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${WG_PORT}/udp" comment 'wireguard' || true
fi
iptables -C INPUT -p udp --dport "$WG_PORT" -j ACCEPT 2>/dev/null || iptables -I INPUT -p udp --dport "$WG_PORT" -j ACCEPT

echo
echo "=== сервер готов ==="
echo "Публичный ключ сервера: $SERVER_PUB"
echo "Адрес в туннеле:        $WG_ADDR"
echo "ListenPort:             $WG_PORT/udp"
echo "Публичный IP (endpoint): ${ENDPOINT_IP:-НЕ_ОПРЕДЕЛЁН — задай ENDPOINT_IP=x.x.x.x}"
echo
echo "Проверка:"
echo "  wg show"
echo "  ss -ulnp | grep $WG_PORT"
echo
echo "В панели Timeweb / firewall VPS открой UDP $WG_PORT на весь интернет (клиенты за NAT)."
echo "Дальше: ./add-peer.sh partner"
echo "        ./add-peer.sh me"
