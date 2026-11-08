# 配布用フォルダと zip を作る。
#
#   powershell -ExecutionPolicy Bypass -File .\build.ps1
#
# 必要なもの: Python 3.11 以上（python が PATH にあること）、speech.cpp（speech コマンド）、インターネット接続（初回の ffmpeg 取得）
# できるもの: dist\<Name>\ （このフォルダをそのまま配れる）と dist\<Name>-日付.zip
#
#   -Name      exe とフォルダの名前（既定 ShitenMemo。日本語の名前は「爆速視点メモ」で、app.py の APP_TITLE）
#   -ModelPath 同梱するモデル（.gguf）。省略すると speech.cpp が取ってきたものを探す
#   -FfmpegZip 手元にある ffmpeg の zip を使う（BtbN の win64-lgpl-shared）
#   -NoModel   モデルを同梱しない（初回起動時に speech.cpp が取ってくる）
#   -NoZip     zip を作らない
param(
    [string]$Name = "ShitenMemo",
    [string]$ModelPath = "",
    [string]$FfmpegZip = "",
    [switch]$NoModel,
    [switch]$NoZip
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest が遅くならないように
$Root = $PSScriptRoot
$DistRoot = Join-Path $Root "dist"
$Dist = Join-Path $DistRoot $Name
$Cache = Join-Path $Root "build_cache"
# PyInstaller の作業フォルダ。ここにも ShitenMemo.exe ができるが、単体では動かない中間物なので目に付かない所に置く
$Work = Join-Path $Cache "pyinstaller"
$Lic = Join-Path $Dist "licenses"
$Src = Join-Path $Root "src"   # app.py / prepare_win.py / viewer.html
$Version = ([regex]::Match((Get-Content -Raw -Encoding UTF8 (Join-Path $Src "app.py")), 'VERSION = "([0-9.]+)"')).Groups[1].Value
$Title = ([regex]::Match((Get-Content -Raw -Encoding UTF8 (Join-Path $Src "app.py")), 'APP_TITLE = "([^"]+)"')).Groups[1].Value
if (-not $Version) { throw "app.py に VERSION がありません" }
New-Item -ItemType Directory -Force $Cache | Out-Null

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "   $msg" }
function Warn($msg) { Write-Host "   注意: $msg" -ForegroundColor Yellow }
# Windows PowerShell 5.1 では、エラー出力をリダイレクトした外部コマンドが $ErrorActionPreference=Stop で止まるので、その間だけ緩める
function Native([scriptblock]$block) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $block 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $old }
}
# ダウンロード。一時的な失敗（504 など）は待ってやり直し、途中で切れたファイルは残さない
function Download($uri, $out, [int]$tries = 4) {
    $part = "$out.part"
    for ($i = 1; $i -le $tries; $i++) {
        try {
            if (Test-Path $part) { Remove-Item -Force $part }
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 600 -OutFile $part -Uri $uri
            Move-Item -Force $part $out
            return
        } catch {
            if (Test-Path $part) { Remove-Item -Force $part }
            if ($i -eq $tries) { throw "ダウンロードに失敗しました（$uri）: $($_.Exception.Message)" }
            $wait = 15 * $i
            Warn "ダウンロードに失敗しました（$($_.Exception.Message)）。$wait 秒後にやり直します（$i/$tries）"
            Start-Sleep -Seconds $wait
        }
    }
}
function MB($path) { "{0:N0} MB" -f ((Get-ChildItem $path -Recurse -File | Measure-Object Length -Sum).Sum / 1MB) }

foreach ($f in "src\app.py", "src\prepare_win.py", "src\viewer.html", "THIRD_PARTY_NOTICES.txt", "使い方.txt") {
    if (-not (Test-Path (Join-Path $Root $f))) { throw "$f がありません" }
}

# ---------------------------------------------------------------- Python → exe
Step "ビルド専用の Python 環境を用意しています"
# 普段使いの Python のパッケージ（scipy など）を壊さないよう、build_cache\venv に入れる
$Venv = Join-Path $Cache "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) {
    python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "venv を作れませんでした" }
}
Native { & $Py -m pip install --disable-pip-version-check -q --upgrade pyinstaller numpy tkinterdnd2 } | Out-Host
if ($LASTEXITCODE -ne 0) { throw "pip に失敗しました" }
Info ((& $Py --version) + " / " + (& $Py -c "import numpy; print('numpy', numpy.__version__)"))

Step "exe を作っています（PyInstaller、$Title $Version）"
if (Test-Path $Dist) {
    # 前回の配布物が起動したままだと、ファイルが使用中で消せない
    $busy = @(Get-Process -ErrorAction SilentlyContinue |
              Where-Object { $_.Path -and $_.Path.StartsWith($Dist, [StringComparison]::OrdinalIgnoreCase) })
    if ($busy.Count) {
        throw ("前回作った配布物が起動したままです（" + (($busy | ForEach-Object { "$($_.Name).exe" } | Sort-Object -Unique) -join ", ") +
               "）。アプリを閉じるか、タスクマネージャーで終了してからやり直してください")
    }
    try { Remove-Item -Recurse -Force $Dist }
    catch { throw "前回の配布物（$Dist）を消せませんでした。エクスプローラーやウイルス対策ソフトが使っているかもしれません。少し待ってやり直してください: $($_.Exception.Message)" }
}
# exe のプロパティに出る情報。製品名や版の入っていない exe はウイルス対策ソフトに疑われやすい
New-Item -ItemType Directory -Force $Work | Out-Null
$v4 = (($Version.Split(".") + @("0", "0", "0", "0"))[0..3]) -join ", "
$VerFile = Join-Path $Work "version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v4), prodvers=($v4), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041104b0', [
      StringStruct('FileDescription', '$Title'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('InternalName', '$Name'),
      StringStruct('OriginalFilename', '$($Name).exe'),
      StringStruct('ProductName', '$Title'),
      StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1041, 1200])])
  ]
)
"@ | ForEach-Object { [IO.File]::WriteAllText($VerFile, $_, (New-Object Text.UTF8Encoding $false)) }  # BOM なし（BOM があると PyInstaller が読めない）
& $Py -m PyInstaller --noconfirm --clean --log-level WARN --windowed --noupx --version-file $VerFile `
    --name $Name --distpath $DistRoot --workpath $Work --specpath $Work `
    --collect-all tkinterdnd2 --hidden-import prepare_win `
    (Join-Path $Src "app.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller に失敗しました" }
Copy-Item (Join-Path $Src "viewer.html") $Dist
Copy-Item (Join-Path $Root "使い方.txt") $Dist
New-Item -ItemType Directory -Force $Lic | Out-Null
Copy-Item (Join-Path $Root "THIRD_PARTY_NOTICES.txt") $Lic
# 本体のライセンス（LICENSE ファイルを置いたら配布物にも入る。まだ決めていなければ何もしない）
if (Test-Path (Join-Path $Root "LICENSE")) { Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Lic "ShitenMemo_LICENSE.txt") }
Info "exe: $(MB $Dist)"

# ---------------------------------------------------------------- ffmpeg（LGPL 版）
Step "ffmpeg（LGPL 版）を入れています"
if (-not $FfmpegZip) {
    $FfmpegZip = Join-Path $Cache "ffmpeg-master-latest-win64-lgpl-shared.zip"
    if (-not (Test-Path $FfmpegZip)) {
        Info "ダウンロードしています…"
        Info "（失敗が続くときは、前に使った zip を -FfmpegZip で指定できます）"
        Download "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip" $FfmpegZip
    }
}
$ffTmp = Join-Path $Cache "ffmpeg"
if (Test-Path $ffTmp) { Remove-Item -Recurse -Force $ffTmp }
try { Expand-Archive $FfmpegZip -DestinationPath $ffTmp -Force }
catch {
    if ($FfmpegZip.StartsWith($Cache)) { Remove-Item -Force $FfmpegZip }  # 壊れたキャッシュは次回取り直す
    throw "ffmpeg の zip を展開できませんでした（壊れているかもしれません）: $($_.Exception.Message)"
}
$ffExe = Get-ChildItem $ffTmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if (-not $ffExe) { throw "zip の中に ffmpeg.exe がありません" }
$ffDst = Join-Path $Dist "bin\ffmpeg"
New-Item -ItemType Directory -Force $ffDst | Out-Null
Copy-Item (Join-Path $ffExe.DirectoryName "ffmpeg.exe"), (Join-Path $ffExe.DirectoryName "ffprobe.exe") $ffDst
Copy-Item (Join-Path $ffExe.DirectoryName "*.dll") $ffDst
$ffLic = Get-ChildItem $ffTmp -Recurse -Filter "LICENSE*" | Select-Object -First 1
if ($ffLic) { Copy-Item $ffLic.FullName (Join-Path $Lic "FFmpeg_LICENSE.txt") }
$ver = Native { & (Join-Path $ffDst "ffmpeg.exe") -hide_banner -version } | Out-String
if ($ver -match "--enable-gpl" -or $ver -match "--enable-nonfree") { Warn "この ffmpeg は GPL/nonfree 版です。LGPL 版（lgpl-shared）を使ってください" }
Set-Content -Encoding UTF8 (Join-Path $Lic "FFmpeg_build.txt") $ver
# 同梱した版のソースの場所（LGPL のため）。master のビルドは「N-番号-gコミット」、リリースは「n7.1」のような版名
$first = ($ver -split "`n")[0]
if ($first -match "-g([0-9a-f]{7,40})") { $src = "https://github.com/FFmpeg/FFmpeg/tree/$($Matches[1])" }
elseif ($first -match "version n?([0-9][0-9.]*)") { $src = "https://github.com/FFmpeg/FFmpeg/tree/n$($Matches[1])" }
else { $src = "https://ffmpeg.org/download.html"; Warn "ffmpeg の版からコミットを読み取れませんでした: $first" }
Set-Content -Encoding UTF8 (Join-Path $Lic "FFmpeg_source.txt") @(
    "同梱している FFmpeg のソースコード",
    "",
    "版: $($first.Trim())",
    "ソース: $src",
    "（右上の Code → Download ZIP で取得できます）"
)
Info "ffmpeg: $(MB $ffDst)  ($(($ver -split "`n")[0].Trim()))"

# ---------------------------------------------------------------- speech.cpp
Step "speech.cpp を入れています"
$cmd = Get-Command speech -ErrorAction SilentlyContinue
if (-not $cmd) { throw "speech コマンドが見つかりません。speech.cpp をインストールしてください" }
$spExe = $cmd.Source
if ([IO.Path]::GetExtension($spExe) -ne ".exe") { throw "speech が exe ではありません（$spExe）。この場所を教えてください" }
$spDir = Split-Path $spExe
$spDst = Join-Path $Dist "bin\speech"
New-Item -ItemType Directory -Force $spDst | Out-Null
$spFiles = Get-ChildItem $spDir -File | Where-Object { $_.Extension -in ".exe", ".dll" }
$spFiles | Copy-Item -Destination $spDst
Info ("コピーしたファイル: " + (($spFiles | ForEach-Object Name) -join ", "))
$spLic = Join-Path $Lic "speech.cpp"
New-Item -ItemType Directory -Force $spLic | Out-Null
$found = @(Get-ChildItem $spDir, (Split-Path $spDir) -File -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -match "^(LICENSE|NOTICE|COPYING|THIRD)" })
if ($found.Count) { $found | Copy-Item -Destination $spLic }
else {
    Info "ライセンス文をダウンロードしています…"
    Download "https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/LICENSE" (Join-Path $spLic "LICENSE")
}
if (Test-Path (Join-Path $spDst "vulkan-1.dll")) {
    Warn "speech.cpp のフォルダに vulkan-1.dll が入っていました。GPU のある PC で古い物が使われないか確認してください"
}
Info "speech.cpp: $(MB $spDst)  ($((Native { & $spExe --version } | Out-String).Trim()))"

# ---------------------------------------------------------------- Vulkan ローダー（GPU ドライバの無い PC 用）
Step "Vulkan ローダーの予備を入れています"
$vk = Join-Path $env:SystemRoot "System32\vulkan-1.dll"
if (Test-Path $vk) {
    $vkDst = Join-Path $Dist "bin\vulkan_fallback"
    New-Item -ItemType Directory -Force $vkDst | Out-Null
    Copy-Item $vk $vkDst
    Info "vulkan-1.dll $((Get-Item $vk).VersionInfo.FileVersion)（その PC に無いときだけ使う）"
} else {
    Warn "この PC に vulkan-1.dll がありません。GPU ドライバの無い PC では動かない可能性があります"
}

# ---------------------------------------------------------------- モデル
if (-not $NoModel) {
    Step "音声認識モデルを入れています"
    if (-not $ModelPath) {
        Native { & $spExe pull haas-ja-v1.0 } | Out-Host
        $roots = @($env:LOCALAPPDATA, $env:APPDATA, (Join-Path $env:USERPROFILE ".cache"), $spDir, (Split-Path $spDir)) |
                 Where-Object { $_ -and (Test-Path $_) }
        $cands = @(Get-ChildItem $roots -Recurse -Depth 6 -File -Filter "*.gguf" -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -match "haas" } | Sort-Object { $_.Name -notmatch "q8_0" }, Length)
        if (-not $cands.Count) { throw "モデルの .gguf が見つかりませんでした。-ModelPath で場所を指定してください（speech models で確認できます）" }
        $ModelPath = $cands[0].FullName
        if ($cands.Count -gt 1) { Info ("候補: " + (($cands | ForEach-Object Name) -join ", ")) }
    }
    $mDst = Join-Path $Dist "models"
    New-Item -ItemType Directory -Force $mDst | Out-Null
    Copy-Item $ModelPath $mDst
    Info "モデル: $(Split-Path $ModelPath -Leaf)（$(MB $mDst)）"
    $apache = Join-Path $Cache "Apache-2.0.txt"
    if (-not (Test-Path $apache)) {
        Download "https://www.apache.org/licenses/LICENSE-2.0.txt" $apache
    }
    Copy-Item $apache $Lic
}

# ---------------------------------------------------------------- 確認と zip
Step "同梱した物で動くか確認しています"
Native { & (Join-Path $Dist "bin\ffmpeg\ffprobe.exe") -hide_banner -version } | Select-Object -First 1
Native { & (Join-Path $Dist "bin\speech\speech.exe") --version }
Native { & (Join-Path $Dist "bin\speech\speech.exe") devices }
Info "フォルダ全体: $(MB $Dist)"

if (-not $NoZip) {
    Step "zip を作っています"
    $zip = Join-Path $DistRoot ("{0}-{1}.zip" -f $Name, $Version)
    if (Test-Path $zip) { Remove-Item $zip }
    if (Get-Command tar.exe -ErrorAction SilentlyContinue) {
        Push-Location $DistRoot; tar.exe -a -c -f $zip $Name; Pop-Location   # Compress-Archive より速い
    } else {
        Compress-Archive -Path $Dist -DestinationPath $zip
    }
    Info "$zip（$(MB $zip)）"
}
Step "完了: $(Join-Path $Dist "$($Name).exe")"
