# 配布物を Windows サンドボックス（GPU なし・ネットなし）で試す。
#
#   powershell -ExecutionPolicy Bypass -File .\tools\start_sandbox.ps1
#
# このリポジトリの sandbox_test フォルダを、サンドボックスのデスクトップの test として見せる。
# dist にある一番新しい zip を sandbox_test にコピーしてから起動する（短い動画は自分で sandbox_test に入れておく）。
# .wsb はフォルダの場所を絶対パスで書く必要があるので、毎回ここで作る（どの PC・どのフォルダでも使える）。
$ErrorActionPreference = "Stop"
# リポジトリのフォルダ（build.ps1 がある所）。tools\ に置いても、リポジトリの直下に置いても動く
$Root = @($PSScriptRoot, (Split-Path $PSScriptRoot)) | Where-Object { Test-Path (Join-Path $_ "build.ps1") } | Select-Object -First 1
if (-not $Root) { throw "build.ps1 が見つかりません。このスクリプトはリポジトリの tools フォルダに置いてください" }
$Test = Join-Path $Root "sandbox_test"
New-Item -ItemType Directory -Force $Test | Out-Null

if (-not (Test-Path (Join-Path $env:SystemRoot "System32\WindowsSandbox.exe"))) {
    throw "Windows サンドボックスが有効になっていません（「Windows の機能の有効化または無効化」で有効にして再起動してください。Windows の Pro 以上が必要です）"
}

$zip = Get-ChildItem (Join-Path $Root "dist") -Filter "*.zip" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($zip) {
    $dst = Join-Path $Test $zip.Name
    if (-not (Test-Path $dst) -or (Get-Item $dst).LastWriteTime -lt $zip.LastWriteTime) {
        Write-Host "配布物をコピーしています: $($zip.Name)"
        Copy-Item $zip.FullName $dst -Force
    }
} else {
    Write-Host "注意: dist に zip がありません（先に build.ps1 を実行してください）" -ForegroundColor Yellow
}
if (-not (Get-ChildItem $Test -File | Where-Object { $_.Extension -in ".mp4", ".mov", ".mkv" })) {
    Write-Host "注意: sandbox_test に試験用の動画がありません。例:" -ForegroundColor Yellow
    Write-Host "  ffmpeg -i video.mp4 -t 300 -map 0 -c copy sandbox_test\short.mp4"
}

$host_ = [Security.SecurityElement]::Escape($Test)
$wsb = Join-Path $env:TEMP "shitenmemo_sandbox.wsb"
$xml = @"
<Configuration>
  <!-- GPU なし・ネットなしの「何も入っていない PC」として起動する -->
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$host_</HostFolder>
      <SandboxFolder>C:\Users\WDAGUtilityAccount\Desktop\test</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
</Configuration>
"@
[IO.File]::WriteAllText($wsb, $xml, (New-Object Text.UTF8Encoding $true))
Write-Host "サンドボックスを起動します（共有フォルダ: $Test）"
Start-Process $wsb
