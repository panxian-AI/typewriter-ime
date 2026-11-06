# -*- coding: utf-8 -*-
"""
逐字输入器 · TypeWriter IME
===========================
把粘贴进来的文本，一个字符一个字符地"敲"进当前焦点窗口。

原理：Win32 SendInput + KEYEVENTF_UNICODE
    绕开输入法，直接把 Unicode 码点作为键盘事件注入，
    因此中文、全角符号、emoji 都能逐字输入到任意接受文本的窗口。

仅依赖 Python 标准库（tkinter + ctypes），Windows 专用。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import random
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:          # 无 tkinter 时仍可用命令行模式
    tk = ttk = None

APP_NAME = "逐字输入器"
VERSION = "1.0"

# ─────────────────────────── Win32 底层 ───────────────────────────

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_RETURN = 0x0D
VK_TAB = 0x09
VK_SHIFT = 0x10
VK_END = 0x23
VK_HOME = 0x24
VK_DELETE = 0x2E
VK_RIGHT = 0x27
VK_F8 = 0x77
VK_F9 = 0x78
GA_ROOT = 2

# 编辑器会自动成对补全的字符 → 它补出来的字符
AUTO_PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
CLOSERS = set(AUTO_PAIRS.values())

BRACKET_LABELS = {
    "delete": "删除自动补全（推荐）",
    "skip": "用 → 跳过补全",
    "off": "不处理（编辑器无补全）",
}

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.argtypes = (wt.HWND, wt.UINT)
user32.GetAncestor.restype = wt.HWND

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.OpenClipboard.argtypes = (wt.HWND,)
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = (wt.UINT,)
user32.GetClipboardData.restype = wt.HANDLE
kernel32.GlobalLock.argtypes = (wt.HGLOBAL,)
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalUnlock.argtypes = (wt.HGLOBAL,)
kernel32.GlobalUnlock.restype = wt.BOOL


def _kb(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _emit(batch: list) -> bool:
    arr = (INPUT * len(batch))(*batch)
    sent = user32.SendInput(len(batch), arr, ctypes.sizeof(INPUT))
    return sent == len(batch)


def char_units(ch: str) -> list:
    """把一个字符拆成 UTF-16 码元（emoji 需要代理对）。"""
    cp = ord(ch)
    if cp > 0xFFFF:
        cp -= 0x10000
        return [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
    return [cp]


def send_char(ch: str) -> bool:
    units = char_units(ch)
    batch = [_kb(scan=u, flags=KEYEVENTF_UNICODE) for u in units]
    batch += [_kb(scan=u, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
              for u in reversed(units)]
    return _emit(batch)


def send_vk(vk: int, with_shift: bool = False) -> bool:
    batch = []
    if with_shift:
        batch.append(_kb(vk=VK_SHIFT))
    batch.append(_kb(vk=vk))
    batch.append(_kb(vk=vk, flags=KEYEVENTF_KEYUP))
    if with_shift:
        batch.append(_kb(vk=VK_SHIFT, flags=KEYEVENTF_KEYUP))
    return _emit(batch)


# ─────────────────────────── 节奏控制 ───────────────────────────

# 标点后停顿，让节奏更像真人
SOFT_PAUSE = set("，、；：,;:")
HARD_PAUSE = set("。！？.!?")
CLAUSE_END = set("）)】」』”》")


def char_delay(base_ms: float, ch: str, human: bool) -> float:
    """返回输入并在该字符后等待的秒数。"""
    d = float(base_ms)
    if human:
        d *= random.uniform(0.72, 1.38)
        if ch in HARD_PAUSE:
            d += random.uniform(90, 280)
        elif ch in SOFT_PAUSE:
            d += random.uniform(45, 150)
        elif ch in CLAUSE_END:
            d += random.uniform(30, 110)
        elif ch == " ":
            d += random.uniform(0, 25)
        if random.random() < 0.012:          # 偶尔"想一下"
            d += random.uniform(180, 520)
    return max(1.0, d) / 1000.0


def estimate_seconds(text: str, base_ms: float, human: bool) -> float:
    if not text:
        return 0.0
    k = 1.16 if human else 1.0
    return len(text) * base_ms / 1000.0 * k


# ───────────────────────── 代码模式：步骤编译 ─────────────────────────

def build_steps(text: str, newline: str = "enter",
                brackets: str = "off", indent: bool = False) -> list:
    """把待输入文本编译成按键步骤序列，返回 [(kind, arg, 权重), ...]。

    brackets —— 应对"编辑器自动补全括号"：
        "off"    原样输入。编辑器不做自动补全时用这个。
        "delete" 打完 ( [ { " ' 立刻用 Delete 删掉编辑器补出来的那一个。
                 从源头消除重复，之后源码里的 } ) ] " 全部照原样打。
        "skip"   保留编辑器补的字符，打到配对的闭合符时用 → 键跨过去。
                 只适合"补全不会跨行"的编辑器；补全会换行的编辑器请用 delete。
    indent —— True 时每次回车后把编辑器自动缩进的那串空格"选中"，
        再让源码里的缩进直接覆盖它，保证缩进和源码完全一致（多或少都不会）。
        步骤：Home ×2（回到第 0 列）→ Shift+End（选中自动缩进的空白）。
        接下来输入的第一个字符会替换掉这段选中内容，所以不会残留、也不会叠加。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    steps = []
    stack = []

    for li, line in enumerate(text.split("\n")):
        if li:
            if newline == "enter":
                steps.append(("vk", (VK_RETURN, False), 1))
            elif newline == "shift":
                steps.append(("vk", (VK_RETURN, True), 1))
            if indent:
                steps.append(("vk", (VK_HOME, False), 0))
                steps.append(("vk", (VK_HOME, False), 0))
                steps.append(("vk", (VK_END, True), 0))

        for ch in line:
            if ch == "\t":
                steps.append(("vk", (VK_TAB, False), 1))
                continue

            if brackets == "delete" and ch in AUTO_PAIRS:
                steps.append(("char", ch, 1))
                steps.append(("vk", (VK_DELETE, False), 0))
                continue

            if brackets == "skip" and ch in CLOSERS and stack and stack[-1] == ch:
                stack.pop()
                steps.append(("vk", (VK_RIGHT, False), 1))
                continue

            if ch in AUTO_PAIRS:
                stack.append(AUTO_PAIRS[ch])
            elif ch in CLOSERS and stack and stack[-1] == ch:
                stack.pop()
            steps.append(("char", ch, 1))

    return steps


def execute(steps: list, base: float, human: bool, sleep_fn,
            progress_cb=None, aborted=None) -> tuple:
    """按步骤序列敲键。返回 (已完成, 总数, 状态)，状态为 ok / aborted / blocked。"""
    total = sum(w for _, _, w in steps)
    done = 0
    last_ch = ""

    for kind, arg, weight in steps:
        if aborted and aborted():
            return done, total, "aborted"

        t0 = time.perf_counter()
        if kind == "char":
            if not send_char(arg):
                return done, total, "blocked"
            last_ch = arg
        else:
            vk, shift = arg
            if not send_vk(vk, with_shift=shift):
                return done, total, "blocked"
            last_ch = "\n" if vk == VK_RETURN else ""

        done += weight
        if progress_cb:
            progress_cb(done, total)

        wait = char_delay(base, last_ch, human) - (time.perf_counter() - t0)
        if wait > 0:
            # 等待函数返回 False 才是"被中止"；time.sleep 返回 None 视为正常
            if sleep_fn(wait) is False:
                return done, total, "aborted"

    return done, total, "ok"


# ─────────────────────────── 配色 ───────────────────────────

BG = "#1b1d23"
PANEL = "#24262e"
FIELD = "#15171c"
BORDER = "#333842"
TEXT = "#e8eaf0"
SUB = "#9aa0ae"
DIM = "#6b7280"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9cff"
GREEN = "#3ddc97"
RED = "#ff6b6b"

UI_FONT = "Microsoft YaHei UI"


# ─────────────────────────── 主程序 ───────────────────────────

class TypeWriterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.msg_q: "queue.Queue[tuple]" = queue.Queue()
        self.stop_event = threading.Event()
        self.run_event = threading.Event()      # set = 正在运行（未暂停）
        self.worker: threading.Thread | None = None
        self.state = "idle"                     # idle | running | paused
        self.progress = (0, 0)
        self._hk_prev = {VK_F8: 0, VK_F9: 0}
        self._last_len = -1
        self._last_mod_count = 0

        self._build_ui()
        self.root_hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

        self._tick()
        self.text.focus_set()

    # ---------------- UI 构建 ----------------

    def _build_ui(self):
        r = self.root
        r.title(f"{APP_NAME} v{VERSION}")
        r.configure(bg=BG)
        r.geometry("660x780")
        r.minsize(560, 680)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.Vertical.TScrollbar",
            background="#3a3f4b", troughcolor=FIELD, bordercolor=FIELD,
            arrowcolor="#8a90a0", darkcolor="#3a3f4b", lightcolor="#3a3f4b",
            gripcount=0, relief="flat",
        )
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", "#4b515f")])
        style.configure("Dark.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor="#2b2f38",
                        bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Dark.TCombobox",
                        fieldbackground=PANEL, background=PANEL,
                        foreground=TEXT, arrowcolor="#c8cdd8",
                        selectbackground=PANEL, selectforeground=TEXT,
                        bordercolor=BORDER)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)])

        outer = tk.Frame(r, bg=BG, padx=18, pady=14)
        outer.pack(fill="both", expand=True)

        # 标题行
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="逐字输入器", bg=BG, fg=TEXT,
                 font=(UI_FONT, 15, "bold")).pack(side="left")
        tk.Label(head, text="  粘贴文本 → 逐个字符敲进当前窗口",
                 bg=BG, fg=DIM, font=(UI_FONT, 9)).pack(side="left", pady=(4, 0))

        # 文本框
        tk.Label(outer, text="① 在此粘贴要输入的内容（Ctrl+V）", bg=BG, fg=SUB,
                 font=(UI_FONT, 9)).pack(anchor="w", pady=(14, 5))

        box = tk.Frame(outer, bg=BORDER, padx=1, pady=1)
        box.pack(fill="both", expand=True)
        self.text = tk.Text(
            box, bg=FIELD, fg=TEXT, insertbackground=ACCENT,
            relief="flat", bd=0, font=(UI_FONT, 11), height=9, width=1,
            padx=12, pady=10, wrap="word", undo=True,
            selectbackground="#2f4a80", selectforeground=TEXT,
            highlightthickness=0,
        )
        self.text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview,
                           style="Dark.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=sb.set)
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Control-Return>", lambda e: (self.start(), "break")[1])

        # 计数 + 小按钮
        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.lbl_count = tk.Label(row, text="0 字符 · 预计 0.0 秒", bg=BG, fg=DIM,
                                  font=(UI_FONT, 9))
        self.lbl_count.pack(side="left")
        for txt, cmd in (("清空", self.clear_text), ("读取剪贴板", self.from_clipboard)):
            tk.Button(row, text=txt, command=cmd, bg=BG, fg=SUB,
                      activebackground=PANEL, activeforeground=TEXT,
                      relief="flat", bd=0, font=(UI_FONT, 9),
                      cursor="hand2", padx=8).pack(side="right")

        # 分隔
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x", pady=14)

        # 设置区
        cfg = tk.Frame(outer, bg=BG)
        cfg.pack(fill="x")

        # 速度
        sp = tk.Frame(cfg, bg=BG)
        sp.pack(fill="x")
        tk.Label(sp, text="速度", bg=BG, fg=SUB, font=(UI_FONT, 9),
                 width=9, anchor="w").pack(side="left")
        self.var_speed = tk.IntVar(value=60)
        self.scale = tk.Scale(
            sp, from_=5, to=400, orient="horizontal", variable=self.var_speed,
            showvalue=False, bg=BG, fg=TEXT, troughcolor="#2b2f38",
            activebackground=ACCENT, highlightthickness=0, bd=0,
            sliderrelief="flat", sliderlength=18, width=8,
            command=lambda _=None: self._refresh_estimate(),
        )
        self.scale.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.lbl_speed = tk.Label(sp, text="60 ms / 字符", bg=BG, fg=TEXT,
                                  font=(UI_FONT, 9), width=13, anchor="e")
        self.lbl_speed.pack(side="right")

        # 第二行设置
        row2 = tk.Frame(cfg, bg=BG)
        row2.pack(fill="x", pady=(10, 0))

        tk.Label(row2, text="启动延迟", bg=BG, fg=SUB, font=(UI_FONT, 9),
                 width=9, anchor="w").pack(side="left")
        self.var_delay = tk.StringVar(value="3 秒")
        cb = ttk.Combobox(row2, textvariable=self.var_delay, width=6,
                          state="readonly", style="Dark.TCombobox",
                          values=["1 秒", "2 秒", "3 秒", "5 秒", "8 秒"],
                          font=(UI_FONT, 9))
        cb.pack(side="left")

        self.var_human = tk.BooleanVar(value=True)
        self._check(row2, "拟人节奏（随机快慢 / 标点停顿）", self.var_human
                    ).pack(side="left", padx=(22, 0))

        row3 = tk.Frame(cfg, bg=BG)
        row3.pack(fill="x", pady=(10, 0))
        tk.Label(row3, text="换行方式", bg=BG, fg=SUB, font=(UI_FONT, 9),
                 width=9, anchor="w").pack(side="left")
        self.var_newline = tk.StringVar(value="enter")
        for val, txt in (("enter", "Enter"), ("shift", "Shift+Enter"), ("skip", "跳过换行")):
            self._radio(row3, txt, self.var_newline, val).pack(side="left", padx=(0, 14))

        # 第四行：代码模式（应对编辑器自动补全括号 / 自动缩进）
        row4 = tk.Frame(cfg, bg=BG)
        row4.pack(fill="x", pady=(10, 0))
        tk.Label(row4, text="括号补全", bg=BG, fg=SUB, font=(UI_FONT, 9),
                 width=9, anchor="w").pack(side="left")
        self.var_brackets = tk.StringVar(value=BRACKET_LABELS["delete"])
        ttk.Combobox(row4, textvariable=self.var_brackets, width=20,
                     state="readonly", style="Dark.TCombobox",
                     values=list(BRACKET_LABELS.values()),
                     font=(UI_FONT, 9)).pack(side="left")

        self.var_indent = tk.BooleanVar(value=True)
        self._check(row4, "缩进对齐（回车后回行首再按源码缩进）",
                    self.var_indent).pack(side="left", padx=(14, 0))

        self.lbl_hint = tk.Label(
            outer,
            text="代码模式：打完 { ( [ 会自动删掉编辑器补的那个闭合符，缩进按源码还原 → 不会出现 }} 、缩进翻倍",
            bg=BG, fg=DIM, font=(UI_FONT, 8), anchor="w", justify="left",
        )
        self.lbl_hint.pack(fill="x", pady=(8, 0))

        # 操作按钮
        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(18, 0))

        self.btn_start = tk.Button(
            btns, text="▶  开始输入    F8", command=self.toggle_start,
            bg=ACCENT, fg="#0d1118", activebackground=ACCENT_HOVER,
            activeforeground="#0d1118", relief="flat", bd=0,
            font=(UI_FONT, 11, "bold"), cursor="hand2", pady=10,
        )
        self.btn_start.pack(side="left", fill="x", expand=True)

        self.btn_stop = tk.Button(
            btns, text="■  停止    F9", command=self.stop,
            bg=PANEL, fg=SUB, activebackground="#33373f", activeforeground=TEXT,
            relief="flat", bd=0, font=(UI_FONT, 10), cursor="hand2",
            pady=10, state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(10, 0))

        # 进度
        self.pbar = ttk.Progressbar(outer, style="Dark.Horizontal.TProgressbar",
                                    mode="determinate")
        self.pbar.pack(fill="x", pady=(14, 0))

        self.lbl_status = tk.Label(outer, text="就绪 · 粘贴文本后按 F8 开始",
                                   bg=BG, fg=DIM, font=(UI_FONT, 9), anchor="w")
        self.lbl_status.pack(fill="x", pady=(6, 0))

    def _check(self, parent, text, var):
        return tk.Checkbutton(
            parent, text=text, variable=var, bg=BG, fg=SUB,
            activebackground=BG, activeforeground=TEXT,
            selectcolor=PANEL, relief="flat", bd=0,
            font=(UI_FONT, 9), cursor="hand2", highlightthickness=0,
            anchor="w", command=self._refresh_estimate,
        )

    def _radio(self, parent, text, var, value):
        return tk.Radiobutton(
            parent, text=text, variable=var, value=value, bg=BG, fg=SUB,
            activebackground=BG, activeforeground=TEXT, selectcolor=PANEL,
            relief="flat", bd=0, font=(UI_FONT, 9), cursor="hand2",
            highlightthickness=0,
        )

    # ---------------- 文本操作 ----------------

    def _on_modified(self, _evt=None):
        # Text 的 modified 标志需要用 edit_modified 复位才能连续触发
        cur = self.text.edit_modified()
        if cur:
            self.text.edit_modified(False)
            self._refresh_estimate()

    def _text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def _refresh_estimate(self):
        text = self._text()
        n = len(text)
        base = self.var_speed.get()
        secs = estimate_seconds(text, base, self.var_human.get())
        self.lbl_speed.configure(text=f"{base} ms / 字符")
        if self.state == "idle":
            self.lbl_count.configure(text=f"{n} 字符 · 预计 {secs:.1f} 秒")
            self.pbar.configure(maximum=max(n, 1), value=0)

    def clear_text(self):
        self.text.delete("1.0", "end")
        self._refresh_estimate()

    def from_clipboard(self):
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            self._status("剪贴板里没有文本", RED)
            return
        if data:
            self.text.insert("end", data)
            self._refresh_estimate()
            self._status(f"已从剪贴板读取 {len(data)} 个字符")

    # ---------------- 状态显示 ----------------

    def _status(self, msg: str, color: str = DIM):
        self.msg_q.put(("status", (msg, color)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "status":
                    msg, color = payload
                    self.lbl_status.configure(text=msg, fg=color)
        except queue.Empty:
            pass

    def _set_state(self, state: str):
        self.state = state
        if state == "running":
            self.btn_start.configure(text="⏸  暂停    F8")
            self.btn_stop.configure(state="normal", fg=TEXT)
        elif state == "paused":
            self.btn_start.configure(text="▶  继续    F8")
            self.btn_stop.configure(state="normal", fg=TEXT)
        else:
            self.btn_start.configure(text="▶  开始输入    F8")
            self.btn_stop.configure(state="disabled", fg=SUB)

    # ---------------- 定时器：进度 + 全局热键 ----------------

    def _tick(self):
        self._drain()

        done, total = self.progress
        if total:
            self.pbar.configure(maximum=total, value=done)
            if self.state in ("running", "paused"):
                left = max(total - done, 0)
                rate = max(self.var_speed.get() / 1000.0, 0.001)
                self.lbl_status.configure(
                    text=f"输入中 {done}/{total} 字符 · 剩余约 {left * rate:.0f} 秒"
                    if self.state == "running" else
                    f"已暂停 {done}/{total} · 按 F8 继续",
                    fg=ACCENT if self.state == "running" else "#e0b341",
                )

        # 全局热键（不依赖窗口焦点）
        for vk, fn in ((VK_F8, self.toggle_start), (VK_F9, self.stop)):
            pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            if pressed and not self._hk_prev[vk]:
                fn()
            self._hk_prev[vk] = pressed

        self.root.after(60, self._tick)

    # ---------------- 控制逻辑 ----------------

    def toggle_start(self):
        if self.state == "idle":
            self.start()
        elif self.state == "running":
            self.pause()
        elif self.state == "paused":
            self.resume()

    def start(self):
        if self.state != "idle":
            return
        text = self._text()
        if not text.strip():
            self._status("文本框是空的，先粘贴点内容吧", RED)
            return

        self.stop_event.clear()
        self.run_event.set()
        self.progress = (0, len(text))
        self.pbar.configure(maximum=len(text), value=0)
        self._set_state("running")

        delay = int(self.var_delay.get().split()[0])
        label = self.var_brackets.get()
        bracket_key = next((k for k, v in BRACKET_LABELS.items() if v == label), "delete")
        opts = {
            "text": text,
            "base": float(self.var_speed.get()),
            "human": self.var_human.get(),
            "newline": self.var_newline.get(),
            "brackets": bracket_key,
            "indent": self.var_indent.get(),
            "countdown": delay,
        }
        self.worker = threading.Thread(target=self._run, args=(opts,), daemon=True)
        self.worker.start()

    def pause(self):
        if self.state != "running":
            return
        self.run_event.clear()
        self._set_state("paused")

    def resume(self):
        if self.state != "paused":
            return
        self.run_event.set()
        self._set_state("running")

    def stop(self):
        if self.state == "idle":
            return
        self.stop_event.set()
        self.run_event.set()
        self._status("已停止", RED)

    def _finish(self, msg: str, color: str = DIM):
        self.progress = (0, 0)
        self._set_state("idle")
        self._status(msg, color)
        self.pbar.configure(value=0)

    # ---------------- 后台工作线程 ----------------

    def _sleep(self, seconds: float) -> bool:
        """可被暂停/停止打断的等待。返回 False 表示应终止。"""
        end = time.perf_counter() + seconds
        while True:
            if self.stop_event.is_set():
                return False
            if not self.run_event.is_set():
                while not self.run_event.is_set():
                    if self.stop_event.is_set():
                        return False
                    time.sleep(0.05)
                return True
            remain = end - time.perf_counter()
            if remain <= 0:
                return True
            time.sleep(min(0.02, remain))

    def _run(self, opts):
        text = opts["text"]
        base = opts["base"]
        human = opts["human"]

        # 倒计时，给用户切窗口的时间
        for i in range(opts["countdown"], 0, -1):
            self._status(f"{i} 秒后开始输入 · 请切换到目标窗口（F9 取消）", ACCENT)
            if not self._sleep(1.0):
                self._finish("已取消", RED)
                return

        # 若焦点仍在自己身上，等用户切走
        waited = 0.0
        while user32.GetForegroundWindow() == self.root_hwnd:
            if waited == 0:
                self._status("等待中…请点击要输入文字的那个窗口", "#e0b341")
            if not self._sleep(0.1):
                self._finish("已取消", RED)
                return
            waited += 0.1
            if waited > 20:
                self._finish("超时：目标窗口不是本工具以外的窗口，已中止", RED)
                return

        steps = build_steps(text, opts["newline"], opts["brackets"], opts["indent"])
        total = len(text)
        self.progress = (0, total)

        done, _, status = execute(
            steps, base, human, self._sleep,
            progress_cb=lambda d, t: setattr(self, "progress", (d, total)),
            aborted=self.stop_event.is_set,
        )

        if status == "ok":
            self.progress = (total, total)
            self._finish(f"✓ 完成，共输入 {total} 个字符", GREEN)
        elif status == "blocked":
            self._finish("输入被系统拒绝，可能目标窗口不支持文本注入", RED)
        else:
            self._finish(f"已停止 · 共输入 {done} 个字符", RED)


# ─────────────────────────── 自检 ───────────────────────────

def selftest() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if extra else ""))
        ok = ok and bool(cond)

    check("sizeof(INPUT) == 40", ctypes.sizeof(INPUT) == 40,
          f"实际 {ctypes.sizeof(INPUT)}")
    check("普通字符单码元", char_units("中") == [0x4E2D])
    check("emoji 拆代理对", char_units("😀") == [0xD83D, 0xDE00],
          str(char_units("😀")))
    check("估算正数", estimate_seconds("你好世界", 60, True) > 0)
    check("拟人延迟在合理区间",
          all(0.0005 <= char_delay(60, c, True) <= 1.5 for c in "你好，世界。\n"))

    # ── 代码模式：步骤编译 ──
    def kinds(steps):
        out = []
        for k, a, _w in steps:
            out.append(f"char{a}" if k == "char" else
                       (f"vk{a[0]}" if k == "vk" else k))
        return out

    check("delete 模式：左括号后立刻删掉补全",
          kinds(build_steps("f(x)", "enter", "delete")) ==
          [f"charf", f"char(", f"vk{VK_DELETE}", "charx", f"char)"],
          str(kinds(build_steps("f(x)", "enter", "delete"))))
    check("skip 模式：闭合符用 → 跨过",
          kinds(build_steps("f(x)", "enter", "skip")) ==
          ["charf", "char(", "charx", f"vk{VK_RIGHT}"],
          str(kinds(build_steps("f(x)", "enter", "skip"))))
    check("indent 模式：回车后两次 Home + Shift+End 选中自动缩进",
          kinds(build_steps("a\n  b", "enter", "off", True)) ==
          ["chara", f"vk{VK_RETURN}", f"vk{VK_HOME}", f"vk{VK_HOME}",
           f"vk{VK_END}", "char ", "char ", "charb"],
          str(kinds(build_steps("a\n  b", "enter", "off", True))))
    check("默认模式不插入任何控制键",
          kinds(build_steps("f(x)")) == ["charf", "char(", "charx", f"char)"])
    check("权重总和等于字符数",
          all(sum(w for _, _, w in build_steps(t, "enter", b, i)) == len(t)
              for t in ("f(x)", "a\n  b{c}", "int main() {\n\treturn 0;\n}")
              for b in ("off", "delete", "skip") for i in (False, True)))

    print("\n自检结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


# ─────────────────────────── 命令行模式 ───────────────────────────

CF_UNICODETEXT = 13
VK_ESCAPE = 0x1B


def read_clipboard() -> str:
    """用 Win32 API 读剪贴板文本，不依赖 tkinter、不抢焦点。"""
    if not user32.OpenClipboard(None):
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def run_headless(text: str, base: float, human: bool,
                 newline: str, countdown: float,
                 brackets: str = "off", indent: bool = False) -> int:
    if not text.strip():
        print("没有要输入的文本。", file=sys.stderr)
        return 2

    est = estimate_seconds(text, base, human)
    print(f"共 {len(text)} 字符 · 预计约 {est:.0f} 秒 · 输入过程中按 Esc 可中止")
    if brackets != "off" or indent:
        print(f"代码模式：括号补全={brackets} / 缩进对齐={'开' if indent else '关'}")
    for i in range(int(countdown), 0, -1):
        print(f"\r{i} 秒后开始 · 请切换到目标窗口…", end="", flush=True)
        time.sleep(1.0)
    print()

    steps = build_steps(text, newline, brackets, indent)
    total = len(text)
    t_start = time.perf_counter()
    last_report = [-1]

    def on_progress(done, _total):
        if done // 20 != last_report[0] or done == total:
            last_report[0] = done // 20
            print(f"\r输入中 {done}/{total}…", end="", flush=True)

    def aborted():
        return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)

    done, _, status = execute(steps, base, human, time.sleep,
                              progress_cb=on_progress, aborted=aborted)

    if status == "blocked":
        print(f"\n第 {done + 1} 个字符被系统拒绝，目标窗口可能禁止注入。",
              file=sys.stderr)
        return 1
    if status == "aborted":
        print(f"\n已中止 · 共输入 {done} 个字符")
        return 130

    print(f"\n✓ 完成，共输入 {total} 个字符，用时 {time.perf_counter() - t_start:.0f} 秒")
    return 0


def cli(argv: list) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="typewriter_ime",
        description="逐字输入器：把文本一个字符一个字符地敲进当前焦点窗口，"
                    "不经过剪贴板，接收方看到的和真人打字一样。")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--text", help="要输入的文本")
    src.add_argument("--file", help="从 UTF-8 文本文件读取内容")
    src.add_argument("--clipboard", action="store_true", help="从剪贴板读取内容")
    p.add_argument("--speed", type=float, default=60.0,
                   help="基础速度，毫秒/字符（默认 60）")
    p.add_argument("--delay", type=float, default=3.0,
                   help="开始前倒计时秒数（默认 3）")
    p.add_argument("--newline", choices=["enter", "shift", "skip"], default="enter",
                   help="换行处理：enter / shift(Shift+Enter) / skip（默认 enter）")
    p.add_argument("--no-human", action="store_true",
                   help="关闭拟人节奏，改为匀速机械输入")
    p.add_argument("--code", action="store_true",
                   help="代码模式：删除编辑器自动补全的括号 + 缩进对齐（等价于 "
                        "--brackets delete --indent）")
    p.add_argument("--brackets", choices=["off", "delete", "skip"], default=None,
                   help="括号补全处理：delete 删掉编辑器补的那个（推荐）/ "
                        "skip 用 → 跨过 / off 不处理")
    p.add_argument("--indent", action="store_true",
                   help="缩进对齐：回车后先回行首，再按源码缩进输入")
    p.add_argument("--gui", action="store_true", help="强制打开图形界面")
    args = p.parse_args(argv)

    if args.gui or not (args.text or args.file or args.clipboard):
        return -1          # 交给 GUI

    brackets = args.brackets or ("delete" if args.code else "off")
    indent = args.indent or args.code

    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = read_clipboard()
        if not text:
            print("剪贴板里没有文本。", file=sys.stderr)
            return 2

    return run_headless(text, args.speed, not args.no_human,
                        args.newline, args.delay, brackets, indent)


if __name__ == "__main__":
    argv = sys.argv[1:]

    if "--selftest" in argv:
        raise SystemExit(selftest())

    code = cli(argv)
    if code != -1:
        raise SystemExit(code)

    # ── 图形界面模式 ──
    if tk is None:
        print("此 Python 未安装 tkinter，无法打开界面。", file=sys.stderr)
        print("可改用命令行模式，例如：", file=sys.stderr)
        print("  python typewriter_ime.py --clipboard", file=sys.stderr)
        print("  python typewriter_ime.py --text \"你好世界\" --speed 80", file=sys.stderr)
        raise SystemExit(1)

    # 高 DPI 下界面不发虚
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    TypeWriterApp(root)
    root.mainloop()
