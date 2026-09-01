[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $HostName,
    [Parameter(Mandatory)] [string] $AdminUser,
    [Parameter(Mandatory)] [string] $SshKeyPath,
    [Parameter(Mandatory)] [string] $SftpPublicKeyPath,
    [string] $RemoteDirectory = "/opt/ave-file-host"
)

$ErrorActionPreference = "Stop"
if (!(Test-Path -LiteralPath $SshKeyPath) -or !(Test-Path -LiteralPath $SftpPublicKeyPath)) {
    throw "SSH 개인키 또는 SFTP 공개키 파일을 찾을 수 없습니다."
}

$localDirectory = Split-Path -Parent $PSScriptRoot
ssh -i $SshKeyPath "${AdminUser}@${HostName}" "mkdir -p '$RemoteDirectory'"
if ($LASTEXITCODE -ne 0) { throw "배포 디렉터리를 만들지 못했습니다." }
scp -i $SshKeyPath -r "$localDirectory" "${AdminUser}@${HostName}:$RemoteDirectory"
if ($LASTEXITCODE -ne 0) { throw "구성 파일 복사에 실패했습니다." }
scp -i $SshKeyPath $SftpPublicKeyPath "${AdminUser}@${HostName}:$RemoteDirectory/ave-media.pub"
if ($LASTEXITCODE -ne 0) { throw "SFTP 공개키 복사에 실패했습니다." }

$remoteCommand = "cd '$RemoteDirectory'; sudo bash scripts/provision-ubuntu.sh '$RemoteDirectory/ave-media.pub'; cp .env.example .env; docker compose up -d"
ssh -i $SshKeyPath "${AdminUser}@${HostName}" $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "파일 호스트 초기화에 실패했습니다." }
