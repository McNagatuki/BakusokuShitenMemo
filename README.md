# 爆速視点メモ

動画で話している所を文字に起こし、話していない所を飛ばしながら見て「視点メモ」を付けるためのツールです。
Windows で exe を実行するだけで使えます。

## すごいところ

- 喋ってないところは飛ばして見れる
- 文字起こしもされるので、会話内容をもとにおもしろシーンのあたりをつけられる
- インストール不要、インターネット接続も不要

## 使い方

### 下準備をする（動画1本につき最初に1回）

1. `ShitenMemo.exe` を起動します。
   初めて起動したときに「Windows によって PC が保護されました」と出た場合は、「詳細情報」→「実行」を押してください。
2. 「選ぶ…」で動画を選びます。
3. 「文字起こしに使う音声」で、人の声が入ったトラックを選びます。
   多くの場合、自分の声＋マイクラ内の他人の声が入ったトラックを選ぶことになると思います。
   ゲーム音などが混ざっていない、声だけのトラックの場合より正確になります。
   選んだトラックは次回も使われます。
4. 「処理方法」は通常「自動（GPU が使えれば使う）」のままで構いません。
5. 「準備を始める」を押します。
   目安は、90 分の動画で GPU のある PC なら 2〜3 分、CPU だけの PC なら 5〜15 分ほどです。
6. 終わったら「ビューワを開く」を押すと、動画と文字起こしが読み込まれた状態でブラウザが開きます。

- 準備が終わると、動画と同じ場所に `<動画名>_edit` フォルダができます。
- 次からは、その中の **`ビューワで開く.html` をダブルクリックするだけ**で動画が見られます（アプリの起動や再準備は不要）。
- 動画と `_edit` フォルダは一緒に移動すればそのまま使えます。動画だけを移動・改名すると開けなくなります。

### 動画をチェックする（059のめっちゃ借りました）

- 右側の**文字起こし**: 再生中の文が強調されます。文をクリックするとその場面へ移動し、上の欄で文字検索ができます。
- **メモ**: 再生中に `M` キーで、その場面（2 秒前）にメモを付けられます。テキストで保存・コピー・読み込みができます。
- **タイムライン**: 青が話している所です。拡大すると文の切れ目と本文も表示されます。
- **区間の決め方**: 発話の前後に残す余白や、何秒未満の無音を発話に含めるかを変えられます。

| 操作 | 内容 |
| --- | --- |
| `Space` | 再生 / 一時停止 |
| `←` `→` | 5 秒戻る / 進む（`Shift` を押しながらで前 / 次の発話へ） |
| `↑` `↓` | 話している所の再生速度を上げる / 下げる |
| `S` | 話していない所の扱い（飛ばす / 早送り）を切り替える |
| `M` | その場面にメモを付ける |
| `/` | 文字起こしを検索 |
| `+` `-` `\` | タイムラインの拡大 / 縮小 / 全体表示 |
| `Ctrl`（または `Alt`）+ホイール | マウスの位置を中心にタイムラインを拡大 / 縮小 |
| ホイール | タイムラインを左右に移動 |
| タイムライン下のバー | つまみで移動、両端で拡大・縮小、ダブルクリックで全体 |
| `?` | キー操作の一覧 |

再生される音は**動画の 1 本目の音声トラック**です。録画ソフトで音声を複数のトラックに分けて録っている場合は、1 本目にすべての音が入るように設定してください。

ビューワは Microsoft Edge か Google Chrome で動作確認済みです。

### うまくいかないとき

- **「発話が1つも見つかりませんでした」**: 選んだトラックに声が入っていません。別のトラックを選んでください。
- **途中でエラーになった**: `<動画名>_edit\log.txt` に詳しい記録があります。問い合わせの際に添えてください。
- **処理が遅い**: 「処理方法」を「自動」にしてください（GPU が使えれば使います）。

---

検証の数字や未確認のことなど、詳しくは [docs/handoff_windows_viewer.md](docs/handoff_windows_viewer.md) にあります。

---

## ファイルの説明

### 配布フォルダ（`dist\ShitenMemo\`）

| ファイル | 中身 |
| --- | --- |
| `ShitenMemo.exe` / `_internal\` | アプリ本体（Python と numpy などを含む） |
| `viewer.html` / `使い方.txt` | ビューワの元と説明書 |
| `bin\ffmpeg\` | ffmpeg / ffprobe（LGPL 版） |
| `bin\speech\` | speech.cpp |
| `bin\vulkan_fallback\` | vulkan-1.dll（PC に無いときだけ使う） |
| `models\` | 音声認識モデル（Haas-616M-ja-v1.0-Q8_0.gguf、約 630MB） |
| `licenses\` | 各ライセンス文 |

### 準備でできるもの（`<動画名>_edit\`）

| ファイル | 中身 |
| --- | --- |
| `ビューワで開く.html` | この動画専用のビューワ |
| `viewer_data.json` | 文字起こし（文ごとの開始・終了時刻と本文） |
| `log.txt` | 処理の詳しい記録 |
| `_internal\tokens.json` / `sentences.json` / `transcript.txt` | 文字ごとの時刻、文の一覧、テキスト版の文字起こし |

設定（選んだトラックなど）は `%APPDATA%\ShitenMemo\settings.json` に保存されます。

### このリポジトリ

| ファイル | 役割 |
| --- | --- |
| `src/app.py` | 準備画面（tkinter）。版（`VERSION`）と表示名（`APP_TITLE`）もここで決める |
| `src/prepare_win.py` | 準備の本体。動画から音声を取り出す → 切り分ける → speech.cpp で文字起こし → 文に区切る → `<動画名>_edit` に書き出す。コマンドラインでも動く |
| `src/viewer.html` | ビューワ。準備の最後に、これに動画の場所と文字起こしを埋め込んだ `ビューワで開く.html` を作る |
| `build.ps1` | 配布用フォルダと zip を作る |
| `使い方.txt` | 配布物に入れる説明書。この README の「すごいところ」「使い方」と同じ内容なので、片方を直したらもう片方も直す |
| `THIRD_PARTY_NOTICES.txt` | 同梱しているソフトウェアとモデルのライセンス一覧 |
| `tools/start_sandbox.ps1` | 配布物を Windows サンドボックス（GPU・ネットなし）で試す |

---

## 開発

### 動かす

必要なもの: Python 3.10 以上、numpy、PATH の通った ffmpeg / ffprobe / speech（speech.cpp）

```powershell
python src\app.py                               # 画面
python src\prepare_win.py 動画.mp4 --limit 300  # コマンドライン（先頭 5 分だけ）
python src\prepare_win.py 動画.mp4 --device cpu # CPU だけで
```

### 配布物を作る

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

`dist\ShitenMemo\`（そのまま配れるフォルダ）と `dist\ShitenMemo-<版>.zip` ができます。
Python のパッケージは `build_cache\venv` に入れるので、普段の Python 環境は変わりません。
ffmpeg のダウンロードに失敗し続けるときは、前に使った zip を `-FfmpegZip` で指定できます。
版を上げるときは `src\app.py` の `VERSION` を変えます。

### 配布前の確認

1. `dist\ShitenMemo\ShitenMemo.exe` で、準備 → ビューワまで一通り動くか
2. Windows サンドボックスで、GPU のない PC として動くか

   ```powershell
   ffmpeg -i video.mp4 -t 300 -map 0 -c copy sandbox_test\short.mp4   # 試験用の短い動画（初回だけ）
   powershell -ExecutionPolicy Bypass -File .\tools\start_sandbox.ps1
   ```

   `dist` の最新の zip が `sandbox_test` にコピーされ、サンドボックスのデスクトップの `test` に見えます。

## License

このリポジトリのコード（`src\` の `app.py`・`prepare_win.py`・`viewer.html`、`build.ps1` など）のライセンスは未定です（決めるまでは作者が権利を持ちます）。

配布物（`dist\ShitenMemo\`）には、次のソフトウェアとモデルを同梱しています。これらはそれぞれのライセンスに従います。
ライセンス文は配布物の `licenses\` に、一覧は [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) にあります。

| 同梱物 | ライセンス |
| --- | --- |
| [speech.cpp](https://github.com/nyosegawa/speech.cpp)（ggml を含む） | MIT |
| 音声認識モデル [Haas-ja-v1.0](https://huggingface.co/third-intelligence/Haas-ja-v1.0)（[GGUF 版](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF)） | Apache-2.0 |
| [FFmpeg](https://ffmpeg.org/)（LGPL 版のビルド、改変なし） | LGPL-2.1 以降 |
| [Vulkan Loader](https://github.com/KhronosGroup/Vulkan-Loader) | Apache-2.0 |
| Python / NumPy / tkinterdnd2 / PyInstaller | PSF / BSD-3-Clause / MIT / GPL-2.0 以降（配布を認める例外つき） |

ビューワの時刻つきメモ（一覧・txt 保存・コピー・読み込み）は、[Web Media Player with Annotations](https://github.com/TACOWASA059/mediaplayer_v2.github.io)（MIT License, Copyright (c) 2026 TACOWASA059）を参考に作りました。
