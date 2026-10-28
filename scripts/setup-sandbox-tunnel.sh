#!/usr/bin/env bash
# RU: Одноразовая настройка VPS. EN: A forwarding-only account, never a root login.
set -euo pipefail
if [[ "$EUID" != 0 ]]; then
  echo "Run as root on the VPS."
  exit 1
fi
cd -P "$(dirname "$0")/.."
if [[ "$PWD" != /opt/setlist_bot_sandbox ]]; then
  echo "Run only from /opt/setlist_bot_sandbox."
  exit 1
fi
public_file="${1:?Pass a file containing the cloud public ED25519 key}"
ssh-keygen -lf "$public_file" >/dev/null
public_key="$(cat "$public_file")"
if [[ ! "$public_key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [A-Za-z0-9_.@-]+)?$ ]]; then
  echo "Expected exactly one plain ED25519 public key."
  exit 1
fi
if ! id setlist-tunnel >/dev/null 2>&1; then
  useradd --create-home --shell /bin/sh setlist-tunnel
fi
install -d -o root -g root -m 755 /home/setlist-tunnel/.ssh
printf 'restrict,port-forwarding,permitopen="127.0.0.1:5433" %s\n' "$public_key" > /home/setlist-tunnel/.ssh/authorized_keys
chown root:root /home/setlist-tunnel/.ssh/authorized_keys
chmod 644 /home/setlist-tunnel/.ssh/authorized_keys
config=/etc/ssh/sshd_config.d/40-setlist-sandbox-tunnel.conf
if [[ -e "$config" ]]; then
  cp -p "$config" "${config}.previous"
fi
cat > "$config" <<'CONFIG'
Match User setlist-tunnel
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:5433
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    MaxSessions 0
    ForceCommand /bin/false
Match all
CONFIG
if ! /usr/sbin/sshd -t; then
  if [[ -e "${config}.previous" ]]; then
    mv "${config}.previous" "$config"
  else
    rm "$config"
  fi
  echo "Invalid SSH configuration; active SSH service was not changed."
  exit 1
fi
systemctl reload ssh
echo "Forwarding-only account prepared for 127.0.0.1:5433. Existing root SSH and bot processes unchanged."
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
cat /etc/ssh/ssh_host_ed25519_key.pub
