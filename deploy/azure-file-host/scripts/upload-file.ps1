[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $SourcePath,
    [Parameter(Mandatory)] [string] $HostName,
    [Parameter(Mandatory)] [string] $SshKeyPath,
    [Parameter(Mandatory)] [string] $RemoteName,
    [string] $SftpUser = "ave-media",
    [string] $PublicBaseUrl = ""
)

$ErrorActionPreference = "Stop"
if (!(Test-Path -LiteralPath $SourcePath) -or !(Test-Path -LiteralPath $SshKeyPath)) {
    throw "원본 파일 또는 SSH 개인키를 찾을 수 없습니다."
}
if ($RemoteName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,240}$') {
    throw "RemoteName에는 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다."
}

scp -i $SshKeyPath -- $SourcePath "${SftpUser}@${HostName}:$RemoteName"
if ($LASTEXITCODE -ne 0) { throw "Azure 파일 업로드에 실패했습니다." }

if ($PublicBaseUrl) {
    "{0}/files/{1}" -f $PublicBaseUrl.TrimEnd('/'), $RemoteName
}
