#!/usr/bin/env bash
set -euo pipefail

readonly SFTP_USER="ave-media"
readonly SFTP_ROOT="/srv/ave-whisper-media"
readonly AUTHORIZED_KEY_FILE="${1:?사용할 공개키 파일 경로를 지정하세요.}"

if [[ $EUID -ne 0 ]]; then
  echo "root 권한으로 실행하세요: sudo bash scripts/provision-ubuntu.sh <public-key-path>" >&2
  exit 1
fi
if [[ ! -f "$AUTHORIZED_KEY_FILE" ]]; then
  echo "공개키 파일을 찾을 수 없습니다: $AUTHORIZED_KEY_FILE" >&2
  exit 1
fi

id "$SFTP_USER" >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin "$SFTP_USER"
install -d -m 755 -o root -g root "$SFTP_ROOT"
install -d -m 750 -o "$SFTP_USER" -g "$SFTP_USER" "$SFTP_ROOT/files"
install -d -m 700 -o "$SFTP_USER" -g "$SFTP_USER" "/home/$SFTP_USER/.ssh"
install -m 600 -o "$SFTP_USER" -g "$SFTP_USER" "$AUTHORIZED_KEY_FILE" "/home/$SFTP_USER/.ssh/authorized_keys"

SSHD_CONFIG="/etc/ssh/sshd_config.d/ave-media-sftp.conf"
cat > "$SSHD_CONFIG" <<'EOF'
Match User ave-media
    ChrootDirectory /srv/ave-whisper-media
    ForceCommand internal-sftp -d /files
    PasswordAuthentication no
    PubkeyAuthentication yes
    AllowTcpForwarding no
    X11Forwarding no
EOF

sshd -t
systemctl restart ssh
echo "SFTP 사용자 $SFTP_USER 준비 완료: $SFTP_ROOT/files"
