"""爆速視点メモの「準備」画面（tkinter）。

動画を選ぶ → 文字起こしに使う音声トラックを選ぶ → 「準備を始める」→ 終わったら「ビューワを開く」。
exe に動画をドラッグして起動した場合は、その動画を選んだ状態で開く。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import prepare_win as core

VERSION = "0.1.0"  # build.ps1 はここから版を読む
APP_TITLE = "爆速視点メモ"

APP_NAME = f"{APP_TITLE} {VERSION}"
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ShitenMemo")
SETTINGS = os.path.join(SETTINGS_DIR, "settings.json")
VIDEO_TYPES = [("動画", "*.mp4 *.mov *.mkv *.m4v *.webm"), ("すべてのファイル", "*.*")]
PHASES = {"extract": "1/2 音声を取り出しています", "start": "2/2 文字起こしの準備をしています",
          "asr": "2/2 文字起こししています"}


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def open_path(path):
    if sys.platform == "win32":
        os.startfile(path)  # 既定のブラウザ／エクスプローラーで開く
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        webbrowser.open("file://" + os.path.abspath(path))


class App:
    def __init__(self, root, initial=None):
        self.root = root
        self.settings = load_settings()
        self.info = None
        self.video = None
        self.q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.running = False
        self.phase = None
        self.phase_t0 = 0.0
        self.phase_frac = 0.0
        self.t0 = 0.0
        self.closing = False

        root.title(APP_NAME)
        root.minsize(600, 460)
        root.geometry("680x520")
        try:
            ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        font = ("Yu Gothic UI", 10) if sys.platform == "win32" else None
        if font:
            root.option_add("*Font", font)

        f = ttk.Frame(root, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        # 1. 動画
        ttk.Label(f, text="動画").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.video_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.video_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        self.btn_pick = ttk.Button(f, text="選ぶ…", command=self.pick)
        self.btn_pick.grid(row=0, column=2)
        self.info_var = tk.StringVar(value="準備する動画を選んでください")
        ttk.Label(f, textvariable=self.info_var, foreground="#666").grid(row=1, column=1, columnspan=2,
                                                                         sticky="w", padx=6, pady=(0, 10))

        # 2. 文字起こしに使う音声
        ttk.Label(f, text="文字起こしに使う音声").grid(row=2, column=0, sticky="w")
        self.track_var = tk.StringVar()
        self.cb_track = ttk.Combobox(f, textvariable=self.track_var, state="disabled")
        self.cb_track.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6)
        ttk.Label(f, text="自分の声（マイク）だけが入ったトラックを選ぶと、ゲーム音などを拾わず正確になります。\n"
                          "ビューワで鳴る音は、動画の1本目の音声です。",
                  foreground="#666", justify="left").grid(row=3, column=1, columnspan=2, sticky="w",
                                                          padx=6, pady=(2, 10))

        # 3. 処理方法
        ttk.Label(f, text="処理方法").grid(row=4, column=0, sticky="w")
        self.device_var = tk.StringVar(value=self.settings.get("device", "auto"))
        dv = ttk.Frame(f)
        dv.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 12))
        ttk.Radiobutton(dv, text="自動（GPU が使えれば使う）", value="auto",
                        variable=self.device_var).pack(side="left")
        ttk.Radiobutton(dv, text="CPU のみ", value="cpu", variable=self.device_var).pack(side="left", padx=14)

        # 実行
        bar = ttk.Frame(f)
        bar.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.btn_run = ttk.Button(bar, text="準備を始める", command=self.start, state="disabled")
        self.btn_run.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="中止", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.btn_folder = ttk.Button(bar, text="作業フォルダを開く", command=self.open_folder, state="disabled")
        self.btn_folder.pack(side="right")
        self.btn_viewer = ttk.Button(bar, text="ビューワを開く", command=self.open_viewer, state="disabled")
        self.btn_viewer.pack(side="right", padx=6)

        self.phase_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.phase_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self.pb = ttk.Progressbar(f, maximum=1000)
        self.pb.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.time_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.time_var, foreground="#666").grid(row=8, column=0, columnspan=3,
                                                                         sticky="w", pady=(2, 8))

        self.logbox = ScrolledText(f, height=7, state="disabled", relief="solid", borderwidth=1)
        self.logbox.grid(row=9, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(9, weight=1)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        self.enable_drop()
        if initial:
            root.after(200, lambda: self.set_video(initial))

    # ---------- 動画を選ぶ ----------

    def enable_drop(self):
        """tkinterdnd2 があれば、窓に動画をドラッグして選べるようにする（無くても動く）。"""
        try:
            import tkinterdnd2
            self.root.drop_target_register(tkinterdnd2.DND_FILES)
            self.root.dnd_bind("<<Drop>>", lambda e: self.set_video(self.root.tk.splitlist(e.data)[0]))
        except Exception:
            pass

    def pick(self):
        path = filedialog.askopenfilename(title="動画を選ぶ", filetypes=VIDEO_TYPES,
                                          initialdir=self.settings.get("last_dir") or None)
        if path:
            self.set_video(path)

    def set_video(self, path):
        if self.running:
            return
        path = os.path.abspath(path)
        self.settings["last_dir"] = os.path.dirname(path)
        try:
            info = core.probe(path)
        except core.UserError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        except Exception as e:
            messagebox.showerror(APP_NAME, f"動画を読めませんでした: {e}")
            return
        if info["n_audio"] == 0:
            messagebox.showerror(APP_NAME, "この動画には音声が入っていません")
            return
        self.video, self.info = path, info
        self.video_var.set(path)
        n = info["n_audio"]
        self.info_var.set(f"長さ {core.fmt(info['duration'])}　／　音声トラック {n} 本")

        items = []
        for i, t in enumerate(info["tracks"], 1):
            ch = {1: "モノラル", 2: "ステレオ"}.get(t["channels"], f"{t['channels']}ch")
            items.append(f"トラック {i}（{ch}）" + (f"  {t['name']}" if t["name"] else "")
                         + ("  ← 動画を見るときに鳴る音" if i == 1 and n > 1 else ""))
        self.cb_track.configure(values=items, state="readonly")
        want = self.settings.get("voice_track", 1)  # 初めては 1 本目。前回選んだトラックがあればそれ
        self.cb_track.current((want if 1 <= want <= n else 1) - 1)
        self.btn_run.configure(state="normal")

        done = os.path.exists(os.path.join(core.work_dir(path), "viewer_data.json"))
        self.btn_viewer.configure(state="normal" if done else "disabled")
        self.btn_folder.configure(state="normal" if done else "disabled")
        self.phase_var.set("この動画は準備済みです。やり直す場合は「準備を始める」を押してください" if done else "")
        self.time_var.set("")
        self.pb["value"] = 1000 if done else 0

    # ---------- 実行 ----------

    def start(self):
        if not self.video or self.running:
            return
        track = self.cb_track.current() + 1
        self.settings.update(voice_track=track, device=self.device_var.get())
        save_settings(self.settings)
        o = core.Options(input=self.video, voice_tracks=[track], device=self.device_var.get())

        self.running = True
        self.cancel_flag.clear()
        self.t0 = time.perf_counter()
        self.phase = None
        for w in (self.btn_run, self.btn_pick, self.btn_viewer, self.btn_folder):
            w.configure(state="disabled")
        self.cb_track.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.clear_log()
        self.pb["value"] = 0
        threading.Thread(target=self.work, args=(o,), daemon=True).start()
        self.tick()

    def work(self, o):
        """別スレッドで動く。画面は触らず、キューに知らせるだけ。"""
        log = None
        try:
            log = core.open_log(core.work_dir(o.input))
            log.write(f"[app] {o}\n")
            say = lambda m: (self.q.put(("say", m)), log.write(f"[say] {m}\n"))
            prog = lambda ph, fr, d: self.q.put(("progress", ph, fr, d))
            r = core.run(o, say, log, prog, self.cancel_flag.is_set)
            self.q.put(("done", r))
        except core.Cancelled:
            self.q.put(("stopped", "中止しました"))
        except core.UserError as e:
            self.q.put(("failed", str(e), None))
        except Exception as e:
            if log:
                log.write(traceback.format_exc())
            self.q.put(("failed", str(e), core.work_dir(o.input)))
        finally:
            if log:
                log.close()

    def cancel(self):
        if self.running:
            self.cancel_flag.set()
            self.btn_cancel.configure(state="disabled")
            self.phase_var.set("中止しています…")

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "say":
                    self.log(msg[1])
                elif kind == "progress":
                    _, ph, fr, _d = msg
                    if ph != self.phase:
                        self.phase, self.phase_t0 = ph, time.perf_counter()
                        self.phase_frac = 0.0
                        self.pb.configure(mode="indeterminate" if ph == "start" else "determinate")
                        if ph == "start":
                            self.pb.start(15)
                        else:
                            self.pb.stop()
                    self.phase_frac = fr
                    if ph != "start":
                        self.pb["value"] = fr * 1000
                    if not self.cancel_flag.is_set():
                        self.phase_var.set(f"{PHASES[ph]}　{fr * 100:.0f}%" if ph != "start" else PHASES[ph])
                elif kind == "done":
                    self.finish()
                    self.pb["value"] = 1000
                    self.phase_var.set("完了しました。「ビューワを開く」で見られます")
                    self.btn_viewer.configure(state="normal")
                    self.btn_folder.configure(state="normal")
                    self.root.bell()
                elif kind == "stopped":
                    self.finish()
                    self.phase_var.set(msg[1])
                    self.log(msg[1])
                elif kind == "failed":
                    self.finish()
                    self.phase_var.set("エラーで止まりました")
                    self.log(f"エラー: {msg[1]}")
                    extra = f"\n\n詳しい記録: {os.path.join(msg[2], 'log.txt')}" if msg[2] else ""
                    if not self.closing:
                        messagebox.showerror(APP_NAME, msg[1] + extra)
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.root.destroy()  # ffmpeg / speech を止め終わってから閉じる
            return
        self.root.after(100, self.poll)

    def tick(self):
        """経過時間と、残り時間の目安を1秒ごとに出す。"""
        if not self.running:
            return
        now = time.perf_counter()
        text = f"経過 {core.fmt(now - self.t0)}"
        fr = self.phase_frac
        if self.phase in ("extract", "asr") and fr > 0.03:
            el = now - self.phase_t0
            text += f"　／　この手順の残り 約 {core.fmt(el / fr - el)}"
        self.time_var.set(text)
        self.root.after(1000, self.tick)

    def finish(self):
        self.running = False
        self.pb.stop()
        self.pb.configure(mode="determinate")
        self.btn_cancel.configure(state="disabled")
        for w in (self.btn_run, self.btn_pick):
            w.configure(state="normal")
        self.cb_track.configure(state="readonly")
        self.time_var.set(f"経過 {core.fmt(time.perf_counter() - self.t0)}")

    # ---------- 開く ----------

    def open_viewer(self):
        """この動画専用のビューワ（動画と文字起こしを読み込み済み）をブラウザで開く。"""
        if not self.video:
            return
        work = core.work_dir(self.video)
        try:
            # viewer.html を更新したときや、古い版で準備した動画でも使えるように、開くたびに作り直す
            page = core.write_viewer_page(self.video, work)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"ビューワを用意できませんでした: {e}")
            return
        open_path(page)

    def open_folder(self, select=False):
        if not self.video:
            return
        work = core.work_dir(self.video)
        if sys.platform == "win32" and select:
            subprocess.Popen(["explorer", "/select,", self.video])  # 動画と _edit フォルダが並んで見える
        else:
            open_path(work if os.path.isdir(work) else os.path.dirname(self.video))

    # ---------- その他 ----------

    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def on_close(self):
        if self.running:
            if not messagebox.askyesno(APP_NAME, "準備の途中です。中止して閉じますか？"):
                return
            self.closing = True
            self.cancel()
            save_settings(self.settings)
            return  # 止まったら poll() が閉じる
        save_settings(self.settings)
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:  # 高 DPI の画面でぼやけないようにする（窓を作る前に設定する）
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        import tkinterdnd2
        root = tkinterdnd2.TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    initial = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else None
    App(root, initial)
    root.mainloop()


if __name__ == "__main__":
    main()
