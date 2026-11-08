"""無音スキップビューワ用の「準備」をする本体（speech.cpp を使う）。

画面（app.py）からも、コマンドラインからも使う。

  python prepare_win.py 動画.mp4
  python prepare_win.py 動画.mp4 --voice-tracks 2 --device cpu --limit 300   # 先頭5分だけ CPU で試す

出力（動画と同じ場所の <動画名>_edit フォルダ）:
  viewer_data.json                      … viewer.html に読み込ませる
  _internal/tokens.json, sentences.json … 確認用
  log.txt                               … speech.cpp と ffmpeg の詳しいログ

ffmpeg / ffprobe / speech は、このファイル（exe）と同じフォルダか bin フォルダ、なければ PATH から探す。
モデルは models フォルダに .gguf があればそれを、なければ speech.cpp の名前（haas-ja-v1.0）で取ってくる。
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime
import difflib
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

SR = 16000
SENT_END = set("。？！?!")
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
# 画面版（コンソールなし）で ffmpeg などを動かしたときに黒い窓が出ないようにする
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UserError(Exception):
    """利用者に直してもらうエラー（トラックの指定違いなど）。"""


class Cancelled(Exception):
    """中止ボタンが押された。"""


@dataclasses.dataclass
class Options:
    input: str
    voice_tracks: list = dataclasses.field(default_factory=lambda: [2])
    viewer_audio: object = "video"  # "video" / "all" / [1, 3]
    device: str = "auto"            # auto / cpu / gpu
    threads: int = 0
    model: str = ""                 # 空なら models フォルダか haas-ja-v1.0
    sentence_gap: float = 0.6
    limit: float = 0


# ---------- 外部ツール ----------

TOOL_DIRS = ["", "bin", os.path.join("bin", "ffmpeg"), os.path.join("bin", "speech")]


def tool(name):
    for d in (os.path.join(HERE, x) for x in TOOL_DIRS):
        for cand in (os.path.join(d, name + ".exe"), os.path.join(d, name)):
            if os.path.isfile(cand):
                return cand
    found = shutil.which(name)
    if not found:
        raise UserError(f"{name} が見つかりません（アプリのフォルダの bin に入っているか確認してください）")
    return found


def speech_env():
    """GPU ドライバの無い PC には vulkan-1.dll が無く speech.exe が起動できないので、
    同梱の bin/vulkan_fallback を PATH に足す。ある PC では Windows の物を使う（同梱品で上書きしない）。"""
    env = os.environ.copy()
    if sys.platform == "win32":
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "vulkan-1.dll")
        fallback = os.path.join(HERE, "bin", "vulkan_fallback")
        if not os.path.exists(system) and os.path.isdir(fallback):
            env["PATH"] = fallback + os.pathsep + env.get("PATH", "")
    return env


def default_model():
    found = sorted(glob.glob(os.path.join(HERE, "models", "*.gguf")))
    return found[0] if found else "haas-ja-v1.0"


def work_dir(src):
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(os.path.dirname(os.path.abspath(src)), f"{stem}_edit")


def probe(path):
    """長さと、音声トラックごとの情報（チャンネル数・名前）を返す。"""
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                       stdin=subprocess.DEVNULL, capture_output=True, creationflags=NOWIN)
    if r.returncode != 0:
        raise UserError(f"ファイルを読めませんでした（動画ファイルですか？）: "
                        f"{r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    info = json.loads(r.stdout.decode("utf-8"))
    tracks = []
    for s in info.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {}) or {}
        name = tags.get("title") or tags.get("handler_name") or ""
        if name.strip().lower() in ("soundhandler", "core media audio", "isomedia file produced by google"):
            name = ""  # ソフトが自動で付ける意味のない名前は出さない
        tracks.append({"channels": int(s.get("channels", 0) or 0), "name": name.strip()})
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError):
        raise UserError("動画の長さを読み取れませんでした")
    return {"duration": duration, "tracks": tracks, "n_audio": len(tracks)}


def check_tracks(tracks, n, what):
    if not tracks:
        raise UserError(f"{what}のトラックが選ばれていません")
    for t in tracks:
        if not isinstance(t, int) or not 1 <= t <= n:
            raise UserError(f"{what}のトラック {t} はありません（このファイルの音声は {n} 本）")


def mix_args(tracks, extra_filter=""):
    if len(tracks) == 1:
        return ["-map", f"0:a:{tracks[0] - 1}"] + (["-af", extra_filter] if extra_filter else [])
    ins = "".join(f"[0:a:{t - 1}]" for t in tracks)
    f = f"{ins}amix=inputs={len(tracks)}:normalize=0" + (f",{extra_filter}" if extra_filter else "") + "[a]"
    return ["-filter_complex", f, "-map", "[a]"]


def extract_voice(src, tracks, limit, total, log, progress, cancel):
    """指定トラックを 16kHz モノラル 16bit にして int16 の配列で返す（一時ファイルは作らない）。"""
    cmd = [tool("ffmpeg"), "-v", "error", "-nostats", "-progress", "pipe:2", "-i", src]
    if limit:
        cmd += ["-t", str(limit)]
    cmd += mix_args(tracks) + ["-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=NOWIN)
    buf, errs = io.BytesIO(), []

    def read_out():
        for block in iter(lambda: p.stdout.read(1 << 20), b""):
            buf.write(block)

    def read_err():
        for line in p.stderr:
            s = line.decode("utf-8", "replace").strip()
            m = re.match(r"out_time_us=(\d+)", s)
            if m:
                progress("extract", min(1.0, int(m.group(1)) / 1e6 / max(total, 1e-6)), "")
            elif s and "=" not in s:
                errs.append(s)  # -progress の行以外（エラーなど）だけ残す

    th = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for t in th:
        t.start()
    while p.poll() is None:
        if cancel():
            p.kill()
            raise Cancelled()
        time.sleep(0.1)
    for t in th:
        t.join()
    log.write("\n".join(errs) + "\n")
    if p.returncode != 0:
        raise RuntimeError(f"音声の抽出に失敗しました: {' '.join(errs)[:300]}")
    progress("extract", 1.0, "")
    return np.frombuffer(buf.getvalue(), dtype=np.int16)


def start_viewer_audio(src, want, n, out, limit):
    """ビューワ用の音声を裏で作り始める。want は "video" / "all" / トラック番号のリスト。
    作らない場合は None、作る場合は ffmpeg のプロセスを返す（finish_viewer_audio で待つ）。
    今の画面では使っていない（動画の1本目をそのまま鳴らす）。必要になったときのために残してある。"""
    if want == "video" or n == 0:
        if os.path.exists(out):
            os.remove(out)
        return None
    tracks = list(range(1, n + 1)) if want == "all" else want
    check_tracks(tracks, n, "ビューワ用音声")
    cmd = [tool("ffmpeg"), "-y", "-v", "error", "-i", src]
    if limit:
        cmd += ["-t", str(limit)]
    if len(tracks) == 1:
        cmd += ["-map", f"0:a:{tracks[0] - 1}", "-c:a", "copy", "-movflags", "+faststart", out]
    else:
        cmd += mix_args(tracks, "alimiter=limit=0.95")
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", out]
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=NOWIN)


def finish_viewer_audio(p, log):
    if p is None:
        return False
    _, err = p.communicate()
    log.write(err.decode("utf-8", "replace"))
    if p.returncode != 0:
        raise RuntimeError(f"ビューワ用の音声を作れませんでした: {err.decode('utf-8', 'replace').strip()[:300]}")
    return True


# ---------- 切り方（公式 split_at_quiet_points 相当） ----------

def split_at_quiet_points(audio, chunk_s=30.0, search_s=2.0, win_s=0.1, hop_s=0.01):
    """約 chunk_s 秒ごとに、目標位置の前後 search_s 秒で一番静かな win_s 秒の中心で切る。"""
    n = len(audio)
    chunk, search, win, hop = (int(x * SR) for x in (chunk_s, search_s, win_s, hop_s))
    x = audio.astype(np.float32)
    out, start = [], 0
    while n - start > chunk + search:
        target = start + chunk
        lo, hi = target - search, min(n, target + search)
        seg = x[lo:hi] ** 2
        cs = np.concatenate(([0.0], np.cumsum(seg, dtype=np.float64)))
        starts = np.arange(0, len(seg) - win + 1, hop)
        energy = cs[starts + win] - cs[starts]
        cut = lo + int(starts[int(np.argmin(energy))]) + win // 2
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out


# ---------- speech.cpp worker ----------

class Worker:
    def __init__(self, model, device, threads, log):
        # モデルはファイル名だけを渡し、そのフォルダで speech を動かす。
        # アプリを日本語のフォルダ（C:\Users\竹本\Desktop など）に置いても、引数が ASCII だけになる
        cwd = None
        if os.path.isfile(model):
            cwd, model = os.path.dirname(os.path.abspath(model)), os.path.basename(model)
        cmd = [tool("speech"), "worker", model, "--device", device]
        if threads:
            cmd += ["--threads", str(threads)]
        log.write(f"$ {' '.join(cmd)}  (cwd={cwd})\n")
        self.log = log
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  creationflags=NOWIN, env=speech_env(), cwd=cwd)
        self.stderr_tail = []
        threading.Thread(target=self._drain, daemon=True).start()
        msg = self._read()
        if msg is None or msg.get("type") != "ready":
            self.kill()
            raise RuntimeError(f"speech worker が起動しませんでした: {msg or self.tail()}")
        self.ready = msg
        log.write(f"[worker ready] {json.dumps(msg, ensure_ascii=False)[:500]}\n")
        self.n = 0

    def _drain(self):
        for line in self.p.stderr:
            s = line.decode("utf-8", "replace")
            self.log.write(s)
            self.stderr_tail = (self.stderr_tail + [s.rstrip()])[-10:]

    def tail(self):
        return " / ".join(self.stderr_tail) or "（出力なし）"

    def _read(self):
        line = self.p.stdout.readline()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))

    def _send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def transcribe(self, pcm16):
        self.n += 1
        rid = f"r{self.n}"
        b = pcm16.astype("<i2").tobytes()
        step = SR * 2 * 10  # 10 秒ずつ送る
        for seq, i in enumerate(range(0, max(len(b), 1), step)):
            self._send({"type": "chunk", "id": rid, "seq": seq, "pcm": base64.b64encode(b[i:i + step]).decode()})
        self._send({"type": "transcribe", "id": rid, "sample_rate": SR, "timestamps": True})
        while True:
            msg = self._read()
            if msg is None:
                raise RuntimeError(f"speech worker が途中で終了しました: {self.tail()}")
            if msg.get("id") not in (rid, None):
                continue
            t = msg.get("type")
            if t == "end":
                return msg
            if t in ("error", "fatal"):
                raise RuntimeError(f"speech worker のエラー: {msg.get('error')}")
            if t == "cancelled":
                raise RuntimeError("speech worker が処理を取り消しました")

    def kill(self):
        try:
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:
            pass

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.kill()


def start_worker(model, device, threads, log, say):
    """device=auto なら GPU を試し、だめなら CPU で起動し直す。"""
    tries = ["auto", "cpu"] if device == "auto" else [device]
    last = None
    for d in tries:
        try:
            log.write(f"[device] {d}\n")
            w = Worker(model, d, threads, log)
            if d == "auto":
                # 短い無音で一度動かして、GPU で本当に動くか確かめる
                try:
                    w.transcribe(np.zeros(SR, dtype=np.int16))
                except BaseException:
                    w.kill()
                    raise
            shown = w.ready.get("device") or (w.ready.get("model") or {}).get("device") or ""
            gpu = bool(shown) and shown.upper() != "CPU"
            return w, (f"GPU（{shown}）" if gpu else "CPU")
        except (RuntimeError, OSError, json.JSONDecodeError) as e:
            last = e
            log.write(f"[device {d} failed] {e}\n")
            if d != tries[-1]:
                say("GPU では動かなかったので、CPU で文字起こしします")
    raise RuntimeError(str(last))


# ---------- トークン・文 ----------

def align_text(toks, text):
    """トークンの文字を本文に置き換える（つなげると本文と一致し、時刻はトークンのまま）。asr.py と同じ。"""
    if not toks:
        return toks
    a_chars, owner = [], []
    for k, t in enumerate(toks):
        for ch in t[2]:
            a_chars.append(ch)
            owner.append(k)
    if not a_chars:
        return []
    new = [""] * len(toks)
    sm = difflib.SequenceMatcher(None, "".join(a_chars), text, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for d in range(j2 - j1):
                new[owner[i1 + d]] += text[j1 + d]
        elif op == "replace":
            new[owner[i1]] += text[j1:j2]
        elif op == "insert":
            new[owner[i1 - 1] if i1 > 0 else 0] += text[j1:j2]
    return [[t[0], t[1], s] for t, s in zip(toks, new) if s]


def result_tokens(res, offset):
    toks = []
    for it in res.get("tokens") or []:
        t = (it.get("text") or "").replace("▁", "")
        if t and it.get("start") is not None:
            toks.append([round(it["start"] + offset, 3), round(it["end"] + offset, 3), t])
    text = (res.get("text") or "").strip()
    return align_text(toks, text) if text else toks


def split_sentences(tokens, gap):
    sents, cur = [], []
    for tok in tokens:
        if cur and tok[0] - cur[-1][1] >= gap:
            sents.append(cur)
            cur = []
        cur.append(tok)
        if tok[2] and tok[2][-1] in SENT_END:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    return [{"id": i, "start": sg[0][0], "end": sg[-1][1], "text": "".join(t[2] for t in sg)}
            for i, sg in enumerate(sents, 1)]


def fmt(t):
    m, s = divmod(int(max(0, t)), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


# ---------- 本体 ----------

def run(o: Options, say, log, progress=None, cancel=None):
    """準備をして、結果の dict を返す。
    say(msg): 利用者向けの一行。progress(phase, 0〜1, 補足): phase は "extract" / "start" / "asr"。
    cancel(): True を返したら中止（Cancelled を投げる）。"""
    progress = progress or (lambda *a: None)
    cancel = cancel or (lambda: False)
    src = os.path.abspath(o.input)
    if not os.path.isfile(src):
        raise UserError(f"ファイルがありません: {src}")
    info = probe(src)
    if info["n_audio"] == 0:
        raise UserError("この動画には音声が入っていません")
    check_tracks(o.voice_tracks, info["n_audio"], "文字起こし用")
    work = work_dir(src)
    internal = os.path.join(work, "_internal")
    os.makedirs(internal, exist_ok=True)
    total_media = min(info["duration"], o.limit) if o.limit else info["duration"]

    t0 = time.perf_counter()
    say(f"音声を取り出しています（トラック {', '.join(map(str, o.voice_tracks))}）…")
    audio = extract_voice(src, o.voice_tracks, o.limit, total_media, log, progress, cancel)
    dur_audio = len(audio) / SR
    if dur_audio < 0.5:
        raise UserError("音声を取り出せませんでした（トラックが空かもしれません）")
    chunks = split_at_quiet_points(audio)
    t_extract = time.perf_counter() - t0
    log.write(f"[extract] {dur_audio:.1f}s audio, {len(chunks)} chunks, {t_extract:.1f}s\n")

    vproc = start_viewer_audio(src, o.viewer_audio, info["n_audio"],
                               os.path.join(work, "viewer_audio.m4a"), o.limit)
    w = None
    try:
        say("文字起こしの準備をしています…")
        progress("start", 0.0, "")
        w, dev = start_worker(o.model or default_model(), o.device, o.threads, log, say)
        say(f"文字起こししています（{dev}）…")
        t1 = time.perf_counter()
        tokens, stops = [], 0
        for k, (s, e) in enumerate(chunks, 1):
            if cancel():
                raise Cancelled()
            res = w.transcribe(audio[s:e])
            if res.get("stop") not in (None, "complete"):
                stops += 1
            tokens.extend(result_tokens(res, s / SR))
            progress("asr", e / len(audio), f"{k}/{len(chunks)}")
        asr_s = time.perf_counter() - t1
        w.close()
        w = None
    except BaseException:
        if w:
            w.kill()
        if vproc:
            vproc.kill()
        raise
    log.write(f"[asr] {dev} {asr_s:.1f}s x{dur_audio / max(asr_s, 1e-6):.1f}\n")
    if stops:
        say(f"注意: {stops} か所で文字起こしが途中で打ち切られました（文字が欠けている可能性があります）")
    if not tokens:
        raise UserError("発話が1つも見つかりませんでした。文字起こしに使うトラックを確認してください")

    sents = split_sentences(tokens, o.sentence_gap)
    with open(os.path.join(internal, "tokens.json"), "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False)
    with open(os.path.join(internal, "sentences.json"), "w", encoding="utf-8") as f:
        json.dump(sents, f, ensure_ascii=False, indent=0)
    with open(os.path.join(internal, "transcript.txt"), "w", encoding="utf-8") as f:
        f.writelines(f"[{fmt(s['start'])}] {s['text']}\n" for s in sents)

    if vproc and vproc.poll() is None:
        say("ビューワ用の音声の完成を待っています…")
    has_audio = finish_viewer_audio(vproc, log)
    data = {"version": 1, "source": os.path.basename(src), "duration": total_media,
            "audio": "viewer_audio.m4a" if has_audio else None,
            "sentences": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in sents]}
    with open(os.path.join(work, "viewer_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    page = write_viewer_page(src, work, data)

    total = time.perf_counter() - t0
    say(f"完了しました: {len(sents)} 文（{fmt(total)} かかりました。{dev}、文字起こし x{dur_audio / max(asr_s, 1e-6):.0f} 速）")
    return {"work": work, "page": page, "sentences": len(sents), "device": dev, "seconds": total}


VIEWER_PAGE = "ビューワで開く.html"


def write_viewer_page(src, work, data=None):
    """viewer.html に文字起こしと動画の場所を書き込んだ、この動画専用のページを作業フォルダに作る。
    開くだけで動画と文字起こしが読み込まれる（動画は相対パスなので、動画と _edit を一緒に動かしても使える）。"""
    from urllib.parse import quote
    template = os.path.join(HERE, "viewer.html")
    if not os.path.isfile(template):
        raise UserError(f"viewer.html が見つかりません: {template}")
    if data is None:
        with open(os.path.join(work, "viewer_data.json"), encoding="utf-8") as f:
            data = json.load(f)
    rel = os.path.relpath(os.path.abspath(src), work).replace(os.sep, "/")
    pre = {"video": quote(rel), "videoName": os.path.basename(src), "data": data}
    blob = json.dumps(pre, ensure_ascii=False).replace("</", "<\\/")  # </script> で途切れないように
    with open(template, encoding="utf-8") as f:
        html = f.read()
    tag = f'<script id="preload" type="application/json">{blob}</script>\n'
    i = html.rfind("<script>")
    html = html[:i] + tag + html[i:]
    out = os.path.join(work, VIEWER_PAGE)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def open_log(work):
    os.makedirs(work, exist_ok=True)
    log = open(os.path.join(work, "log.txt"), "a", encoding="utf-8", buffering=1)
    log.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    return log


def parse_tracks(s):
    if s in ("all", "video"):
        return s
    try:
        return [int(x) for x in s.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"トラックの指定「{s}」が読めません。例: 2 / 1,3 / all / video")


def main():
    ap = argparse.ArgumentParser(description="無音スキップビューワ用の準備（speech.cpp 版）")
    ap.add_argument("input", help="動画ファイル")
    ap.add_argument("--voice-tracks", default="2", help="文字起こしに使う音声トラック。例: 2 / 2,3（既定 2）")
    ap.add_argument("--viewer-audio", default="video", help="ビューワで鳴らす音声: video / all / 1,3（既定 video）")
    ap.add_argument("--device", default="auto", help="auto（GPU があれば GPU、だめなら CPU）/ cpu / gpu")
    ap.add_argument("--threads", type=int, default=0, help="CPU のスレッド数（既定は speech.cpp に任せる）")
    ap.add_argument("--model", default="", help="モデル名か .gguf のパス")
    ap.add_argument("--sentence-gap", type=float, default=0.6, help="この秒数以上の間で文を区切る")
    ap.add_argument("--limit", type=float, default=0, help="先頭の何秒だけ処理するか（試験用。0 なら全部）")
    a = ap.parse_args()
    o = Options(input=a.input, voice_tracks=parse_tracks(a.voice_tracks), viewer_audio=parse_tracks(a.viewer_audio),
                device=a.device, threads=a.threads, model=a.model, sentence_gap=a.sentence_gap, limit=a.limit)
    work = work_dir(a.input)
    with open_log(work) as log:
        log.write(" ".join(sys.argv) + "\n")

        def say(msg):
            print(f"\n{msg}" if say.bar else msg, flush=True)
            say.bar = False
            log.write(f"[say] {msg}\n")
        say.bar = False

        names = {"extract": "取り出し", "start": "起動", "asr": "文字起こし"}

        def progress(phase, frac, detail):
            print(f"\r  {names[phase]} {frac * 100:5.1f}% {detail}   ", end="", flush=True)
            say.bar = True

        try:
            r = run(o, say, log, progress)
            print(f"  ビューワ: {r['page']}")
            return 0
        except UserError as e:
            say(f"エラー: {e}")
            return 2
        except Exception as e:
            import traceback
            log.write(traceback.format_exc())
            say(f"エラー: {e}")
            say(f"  詳細: {os.path.join(work, 'log.txt')}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
