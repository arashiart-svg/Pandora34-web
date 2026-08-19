#!/usr/bin/env bash
# Добавляет peer в WireGuard.
#   ./add-peer.sh <имя>                 — сам генерирует ключи, полный .conf
#   ./add-peer.sh <имя> <публичный_ключ> — только pubkey, в .conf плейсхолдер PrivateKey
set -euo pipefail

WG_DIR=/etc/wireguard
WG_IF=wg0

if [[ $(id -u) -ne 0 ]]; then
  echo "Запусти от root: sudo $0 ..."
  exit 1
fi

NAME="${1:-}"
CLIENT_PUB="${2:-}"
if [[ -z "$NAME" ]]; then
  echo "usage: $0 <имя> [публичный_ключ]"
  exit 1
fi
if [[ ! "$NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Имя только латиница/цифры/._-"
  exit 1
fi
if [[ ! -f "$WG_DIR/${WG_IF}.conf" ]]; then
  echo "Сначала setup-server.sh"
  exit 1
fi
if grep -q "^# peer: ${NAME}$" "$WG_DIR/${WG_IF}.conf"; then
  echo "Peer '$NAME' уже есть. Удали блок вручную из ${WG_IF}.conf и строку из peers.tsv"
  exit 1
fi

NEXT=$(cat "$WG_DIR/next_ip")
if [[ "$NEXT" -gt 254 ]]; then
  echo "Подсеть закончилась"
  exit 1
fi
CLIENT_IP="10.10.0.${NEXT}"
SERVER_PUB=$(cat "$WG_DIR/server_public.key")
ENDPOINT=$(cat "$WG_DIR/endpoint_ip" 2>/dev/null || true)
PORT=$(cat "$WG_DIR/listen_port" 2>/dev/null || echo 51820)
OUT_DIR="$WG_DIR/clients"
mkdir -p "$OUT_DIR"

CLIENT_PRIV_LINE="<ВСТАВЬ_СВОЙ_ПРИВАТНЫЙ_КЛЮЧ>"
if [[ -z "$CLIENT_PUB" ]]; then
  umask 077
  CLIENT_PRIV=$(wg genkey)
  CLIENT_PUB=$(printf '%s\n' "$CLIENT_PRIV" | wg pubkey)
  CLIENT_PRIV_LINE="$CLIENT_PRIV"
  printf '%s\n' "$CLIENT_PRIV" > "$OUT_DIR/${NAME}.private.key"
  chmod 600 "$OUT_DIR/${NAME}.private.key"
fi

CONF="$OUT_DIR/${NAME}.conf"
cat > "$CONF" <<EOF
[Interface]
PrivateKey = ${CLIENT_PRIV_LINE}
Address = ${CLIENT_IP}/24
MTU = 1280

[Peer]
PublicKey = ${SERVER_PUB}
Endpoint = ${ENDPOINT:-VPS_PUBLIC_IP}:${PORT}
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 25
EOF
chmod 600 "$CONF"

cat >> "$WG_DIR/${WG_IF}.conf" <<EOF

# peer: ${NAME}
[Peer]
PublicKey = ${CLIENT_PUB}
AllowedIPs = ${CLIENT_IP}/32
EOF

echo -e "${NAME}\t${CLIENT_IP}\t${CLIENT_PUB}" >> "$WG_DIR/peers.tsv"
echo $((NEXT + 1)) > "$WG_DIR/next_ip"

wg set "$WG_IF" peer "$CLIENT_PUB" allowed-ips "${CLIENT_IP}/32"
wg syncconf "$WG_IF" <(wg-quick strip "$WG_IF") 2>/dev/null || true

echo
echo "=== peer $NAME ==="
echo "Туннельный IP: $CLIENT_IP"
echo "Публичный ключ клиента: $CLIENT_PUB"
echo "Файл для Windows: $CONF"
echo
echo "Скачай на Windows (пример):"
echo "  scp root@${ENDPOINT:-VPS}:$CONF ."
echo "Импорт в WireGuard: Import tunnel(s) from file"
echo
echo "Проверка с VPS после подключения клиента:"
echo "  ping -c 3 $CLIENT_IP"
echo "  wg show"
