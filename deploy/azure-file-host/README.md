# Azure 전사용 파일 호스팅

`ave-whisper-api`에 전달할 공개 HTTPS 오디오 URL을 Azure Ubuntu VM에서 제공하기 위한 구성입니다.

파일 흐름은 다음과 같습니다.

```text
ave-client ──SFTP──> Azure VM /srv/ave-whisper-media
                              │
                              └──Caddy HTTPS──> https://<도메인>/files/<작업-파일>
                                                       │
                                                       └── ave-whisper-api
```

IP 주소만으로는 자동 HTTPS 인증서를 발급할 수 없습니다. DNS에 `stt-files.example.com` 같은 도메인 A 레코드를 실제 Azure VM 공인 IP로 연결한 뒤 사용하세요. 공인 IP나 실제 서비스 도메인은 저장소에 기록하지 마세요.

## 구성 파일

| 파일 | 용도 |
| --- | --- |
| `compose.yaml`, `Caddyfile` | HTTPS 파일 제공 컨테이너 |
| `scripts/provision-ubuntu.sh` | VM의 SFTP 사용자·저장 경로 초기화 |
| `scripts/deploy.ps1` | Windows에서 구성 파일을 VM으로 복사하고 실행 |
| `scripts/upload-file.ps1` | ave-client 서버에서 전사용 파일을 SFTP 업로드 |
| `scripts/cleanup-expired.sh` | 만료 임시 파일 삭제 |

## 1. Azure 네트워크 설정

Azure NSG에서 다음 인바운드 포트를 허용합니다.

| 포트 | 용도 | 권장 원본 |
| --- | --- | --- |
| TCP 22 | 관리 SSH 및 SFTP | ave-client 서버의 고정 IP만 |
| TCP 80 | Caddy 인증서 발급·HTTP→HTTPS 리다이렉트 | Internet |
| TCP 443 | Whisper API의 파일 다운로드 | Internet |

공인 파일 URL은 추측하기 어려운 작업 ID를 파일명에 포함하더라도 민감한 원본을 장기 보관하는 용도가 아닙니다. 업로드한 파일은 전사가 끝난 뒤 삭제하고, 최소 하루 한 번 정리 작업을 실행하세요.

## 2. Ubuntu 초기화

VM에 SSH로 접속해 이 디렉터리를 복사한 후 아래를 실행합니다. Docker Engine과 Docker Compose plugin이 설치되어 있어야 합니다.

```bash
sudo bash scripts/provision-ubuntu.sh /home/azureuser/.ssh/ave-media.pub
```

인자는 `ave-media` SFTP 계정에 허용할 공개키 파일 경로입니다. 스크립트는 chroot SFTP 계정, `/srv/ave-whisper-media/files` 저장 경로 및 읽기 전용 Caddy 컨테이너 권한을 만듭니다.

## 3. HTTPS 호스트 시작

`.env.example`을 `.env`로 복사하고 실제 도메인을 넣습니다.

```bash
cp .env.example .env
nano .env
docker compose up -d
docker compose logs -f caddy
```

DNS 전파와 TCP 80·443 허용 후 `https://<도메인>/files/`로 접속했을 때 404 또는 403이 반환되면 Caddy가 동작하는 것입니다. 디렉터리 목록은 제공하지 않습니다.

## 4. 파일 업로드

ave-client를 실행하는 Windows 서버에서 다음처럼 실행합니다.

```powershell
.\scripts\upload-file.ps1 `
  -SourcePath "C:\ave\media\youtube\cache-abc\video.mp4" `
  -HostName "<AZURE_VM_PUBLIC_IP>" `
  -SshKeyPath "C:\Keys\ave-media" `
  -RemoteName "<작업ID>-source.mp4"
```

출력된 URL을 Whisper 요청의 `audio_url`로 전달합니다. `-PublicBaseUrl https://stt-files.example.com`을 명시하지 않으면 URL은 출력하지 않습니다.

## 5. 임시 파일 정리

24시간보다 오래된 파일을 정리합니다.

```bash
sudo install -m 755 scripts/cleanup-expired.sh /usr/local/bin/ave-whisper-cleanup
sudo crontab -e
```

크론에 아래 줄을 추가합니다.

```cron
15 * * * * /usr/local/bin/ave-whisper-cleanup 24
```

## ave-client 연동 설정

ave-client에는 음원 추출·SFTP 업로드·Whisper 전사·즉시 삭제가 구현되어 있습니다. ave-client의 `token.env`에 아래 값을 추가하세요.

```env
AZURE_MEDIA_SFTP_HOST=
AZURE_MEDIA_SFTP_PORT=
AZURE_MEDIA_SFTP_USER=
AZURE_MEDIA_SFTP_KEY_PATH=
AZURE_MEDIA_SSH_KNOWN_HOSTS=
AZURE_MEDIA_PUBLIC_BASE_URL=
```

`known_hosts`에는 Azure VM의 SSH 호스트 키가 있어야 합니다. Windows PowerShell에서 아래를 한 번 실행해 등록하고, 출력된 지문이 VM의 SSH 호스트 지문과 일치하는지 확인하세요.

```powershell
ssh-keyscan -H <AZURE_VM_HOSTNAME> >> $HOME\.ssh\known_hosts
```

SFTP 계정의 개인키는 `token.env` 또는 OS 비밀 저장소에만 보관하고 Git에 커밋하지 않습니다.
