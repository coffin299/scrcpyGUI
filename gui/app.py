"""scrcpy の全オプションを編集して起動するウィンドウ。"""

import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from gui.backend import (
    build_args,
    build_info_args,
    clean_text,
    command_line,
    find_adb,
    find_scrcpy,
    list_profile_names,
    profile_path,
    query_devices,
    read_json,
    request_window_close,
    selector_conflict,
    session_path,
    working_directory,
    write_json,
)
from gui.options_catalog import (
    GROUPS,
    INFO_ACTIONS,
    OPTIONS,
    SHORTCUTS,
)

UNSPECIFIED = "指定しない"
OPTIONAL_LABELS = (
    (UNSPECIFIED, "off"),
    ("フラグのみ", "flag"),
    ("値を指定", "value"),
)
LABEL_TO_MODE = dict(OPTIONAL_LABELS)
MODE_TO_LABEL = {mode: label for label, mode in OPTIONAL_LABELS}
EXIT_TEXT = {
    0: "正常終了",
    1: "起動に失敗しました",
    2: "実行中に端末が切断されました",
}
HELP_TEXT = (
    "空欄・オフの項目は scrcpy に渡しません。今までどおり scrcpy の既定値です。\n"
    "映像のウィンドウは scrcpy 本体が開きます。\n"
    "接続先（シリアル / USB / TCP / 無線）は一つだけ指定してください。\n"
    "詳しくは doc/gui.html を開いてください。"
)


def enable_dpi():
    """高 DPI で文字が滲まないようにする。"""
    if os.name != "nt":
        return
    import ctypes

    try:
        # プロセス単位の DPI 認識。Tk を作る前に呼ぶ必要がある
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return


def coerce_bool(value):
    """JSON や手入力の真偽値を bool にする。"""
    if isinstance(value, str):
        # 文字列の "false" は中身が空ではないので、bool() には渡せない
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class ScrcpyGui:
    """オプション編集、端末一覧、起動と停止を持つメイン画面。"""

    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.fields = {}
        self.rows = []
        self.group_frames = {}
        self.device_serials = {}
        self.proc = None
        self._stop_attempts = 0
        self._info_running = False
        self._loading = True
        self._current_args = []
        self.root.title("scrcpy GUI")
        self.root.minsize(960, 680)
        self.root.geometry("1120x840")
        self.root.option_add("*Font", ("Yu Gothic UI", 10))
        style = ttk.Style()
        # Windows 標準の見た目があればそちらを使う
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self._hint_bg = style.lookup("TFrame", "background") or "#f0f0f0"
        self._build_menu()
        outer = ttk.Frame(root, padding=8)
        outer.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self._build_header(outer)
        self._build_center(outer)
        self._build_footer(outer)
        self._bind_wheel(self.form)
        self.root.bind("<Control-Return>", self._on_start_key)
        self.root.bind("<Control-f>", self._focus_search)
        self.root.bind("<Control-F>", self._focus_search)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._loading = False
        self._apply_filter()
        self.load_session()
        self.root.after(200, self.refresh_devices)
        self.root.after(100, self._poll)
        self.root.after(400, self._place_sash)

    def _build_menu(self):
        """ファイル、端末情報、ヘルプ。"""
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="プロファイルを保存", command=self._save_profile)
        file_menu.add_command(label="プロファイルを読み込む", command=self._load_profile)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self._on_close)
        menubar.add_cascade(label="ファイル", menu=file_menu)
        device_menu = tk.Menu(menubar, tearoff=0)
        device_menu.add_command(label="端末を再読込", command=self.refresh_devices)
        device_menu.add_separator()
        for label, flag in INFO_ACTIONS:
            device_menu.add_command(
                label=label,
                command=lambda flag=flag, label=label: self._run_info(label, flag),
            )
        menubar.add_cascade(label="端末", menu=device_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使い方", command=self._open_help)
        menubar.add_cascade(label="ヘルプ", menu=help_menu)
        self.root.config(menu=menubar)

    def _build_header(self, parent):
        """実行ファイル、端末、検索。"""
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(2, weight=1)
        ttk.Label(
            header,
            text="未入力の項目はコマンドに含めません。映像は scrcpy のウィンドウで開きます。",
        ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))
        ttk.Label(header, text="scrcpy").grid(row=1, column=0, sticky="w")
        self.scrcpy_var = tk.StringVar()
        self.scrcpy_var.trace_add("write", self._on_form_changed)
        ttk.Entry(header, textvariable=self.scrcpy_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=6
        )
        ttk.Button(header, text="参照", command=self._browse_scrcpy).grid(
            row=1, column=3, padx=(0, 6)
        )
        ttk.Button(header, text="自動検出", command=self._detect_tools).grid(
            row=1, column=4
        )
        ttk.Label(header, text="adb").grid(row=2, column=0, sticky="w", pady=4)
        self.adb_var = tk.StringVar()
        ttk.Entry(header, textvariable=self.adb_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=4
        )
        ttk.Button(header, text="参照", command=self._browse_adb).grid(
            row=2, column=3, padx=(0, 6)
        )
        self.console_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            header,
            text="scrcpy のコンソールを表示",
            variable=self.console_var,
        ).grid(row=2, column=4, sticky="w")
        ttk.Label(header, text="端末").grid(row=3, column=0, sticky="w")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            header, textvariable=self.device_var, state="readonly", width=42
        )
        self.device_combo.grid(row=3, column=1, sticky="ew", padx=6)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_picked)
        ttk.Button(header, text="再読込", command=self.refresh_devices).grid(
            row=3, column=2, sticky="w"
        )
        ttk.Label(header, text="検索").grid(row=3, column=3, sticky="e")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._apply_filter)
        self.search_entry = ttk.Entry(header, textvariable=self.search_var, width=24)
        self.search_entry.grid(row=3, column=4, sticky="ew", padx=(6, 0))
        self.count_label = ttk.Label(header, text="")
        self.count_label.grid(row=3, column=5, sticky="e", padx=(8, 0))

    def _build_center(self, parent):
        """オプション一覧とログ。"""
        self.paned = ttk.Panedwindow(parent, orient=tk.VERTICAL)
        self.paned.grid(row=1, column=0, sticky="nsew")
        notebook_host = ttk.Frame(self.paned)
        log_host = ttk.Frame(self.paned)
        self.paned.add(notebook_host, weight=4)
        self.paned.add(log_host, weight=1)
        notebook = ttk.Notebook(notebook_host)
        notebook.pack(fill="both", expand=True)
        options_tab = ttk.Frame(notebook)
        shortcuts_tab = ttk.Frame(notebook)
        notebook.add(options_tab, text="オプション")
        notebook.add(shortcuts_tab, text="ショートカット")
        self._build_form(options_tab)
        self._build_shortcuts(shortcuts_tab)
        self._build_log(log_host)

    def _build_form(self, parent):
        """分類ごとの入力欄。スクロールはキャンバスで受ける。"""
        self.canvas = tk.Canvas(parent, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.form = ttk.Frame(self.canvas)
        self.form_id = self.canvas.create_window((0, 0), window=self.form, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.form.bind("<Configure>", self._on_form_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        for group in GROUPS:
            frame = ttk.LabelFrame(self.form, text=group, padding=6)
            self.group_frames[group] = frame
        for spec in OPTIONS:
            self._add_row(self.group_frames[spec["group"]], spec)

    def _add_row(self, parent, spec):
        """オプション 1 件分のラベル、入力、説明。"""
        row = ttk.Frame(parent)
        ttk.Label(row, text=spec["label"], width=28, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        field = {"spec": spec, "row": row, "kind": spec["kind"]}
        kind = spec["kind"]
        if kind == "bool":
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text="有効", variable=var).grid(
                row=0, column=1, sticky="w"
            )
            field["var"] = var
            var.trace_add("write", self._on_form_changed)
        elif kind == "choice":
            var = tk.StringVar(value=UNSPECIFIED)
            ttk.Combobox(
                row,
                textvariable=var,
                state="readonly",
                values=(UNSPECIFIED,) + tuple(spec["choices"]),
                width=44,
            ).grid(row=0, column=1, sticky="ew")
            field["var"] = var
            var.trace_add("write", self._on_form_changed)
        elif kind == "optional":
            mode = tk.StringVar(value=UNSPECIFIED)
            ttk.Combobox(
                row,
                textvariable=mode,
                state="readonly",
                values=[label for label, _mode in OPTIONAL_LABELS],
                width=14,
            ).grid(row=0, column=1, sticky="w")
            value = tk.StringVar()
            entry = ttk.Entry(row, textvariable=value)
            entry.grid(row=0, column=2, sticky="ew", padx=(8, 0))
            # 値を指定するまで、空の入力欄は触れないようにする
            entry.configure(state="disabled")
            field["mode"] = mode
            field["var"] = value
            field["entry"] = entry
            key = spec["key"]
            mode.trace_add("write", lambda *_args, key=key: self._sync_optional(key))
            mode.trace_add("write", self._on_form_changed)
            value.trace_add("write", self._on_form_changed)
        else:
            var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=var)
            entry.grid(row=0, column=1, sticky="ew")
            field["var"] = var
            field["entry"] = entry
            var.trace_add("write", self._on_form_changed)
            if kind == "path":
                ttk.Button(
                    row,
                    text="参照",
                    command=lambda key=spec["key"]: self._browse_path(key),
                ).grid(row=0, column=2, padx=(8, 0))
        hint = tk.Label(
            row,
            text=spec["flag"] + "   " + spec["hint"],
            anchor="w",
            justify="left",
            fg="#555555",
            bg=self._hint_bg,
            wraplength=760,
        )
        hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        row.columnconfigure(1, weight=1)
        field["hint"] = hint
        self.fields[spec["key"]] = field
        self.rows.append(field)

    def _build_shortcuts(self, parent):
        """実行中に使えるキーの一覧。編集はできない。"""
        box = tk.Text(parent, wrap="word", padx=12, pady=12, relief="flat")
        scroll = ttk.Scrollbar(parent, command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        lines = [
            "MOD はショートカット用の修飾キーです。",
            "既定は左 Alt または左 Super です。変更はオプションの「ショートカット修飾キー」です。",
            "",
        ]
        for keys, text in SHORTCUTS:
            lines.append(keys)
            lines.append("    " + text)
            lines.append("")
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_log(self, parent):
        """scrcpy と adb の出力。"""
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        ttk.Label(bar, text="ログ").pack(side="left")
        ttk.Button(bar, text="消去", command=self._clear_log).pack(side="right")
        self.log = tk.Text(parent, height=8, wrap="word", state="disabled")
        self.log.configure(font=("Consolas", 10))
        scroll = ttk.Scrollbar(parent, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_footer(self, parent):
        """追加引数、完成したコマンド、起動ボタン。"""
        footer = ttk.Frame(parent)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text="追加の引数").grid(row=0, column=0, sticky="w")
        self.extra_var = tk.StringVar()
        self.extra_var.trace_add("write", self._on_form_changed)
        ttk.Entry(footer, textvariable=self.extra_var).grid(
            row=0, column=1, columnspan=5, sticky="ew", padx=(6, 0)
        )
        ttk.Label(footer, text="コマンド").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.command_box = tk.Text(
            footer, height=3, wrap="word", relief="solid", bd=1, state="disabled"
        )
        self.command_box.configure(font=("Consolas", 10))
        self.command_box.grid(row=1, column=1, columnspan=5, sticky="ew", pady=(6, 0))
        buttons = ttk.Frame(footer)
        buttons.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(buttons, text="起動 (Ctrl+Enter)", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self._stop)
        self.stop_button.pack(side="left", padx=6)
        # 起動前は止める対象が無い
        self.stop_button.configure(state="disabled")
        ttk.Button(buttons, text="コマンドをコピー", command=self._copy_command).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="リセット", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="プロファイル保存", command=self._save_profile).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(buttons, text="プロファイル読込", command=self._load_profile).pack(
            side="left", padx=6
        )
        self.status_label = ttk.Label(footer, text="準備できました", anchor="w")
        self.status_label.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        self.selector_label = tk.Label(
            footer,
            text="",
            anchor="w",
            justify="left",
            fg="#a1260d",
            bg=self._hint_bg,
        )
        self.selector_label.grid(row=4, column=0, columnspan=6, sticky="ew")

    def _bind_wheel(self, widget):
        """フォーム上のホイールを、外側のキャンバスのスクロールに使う。"""
        widget.bind("<MouseWheel>", self._on_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _on_wheel(self, event):
        # Windows の delta は 120 刻み。下方向が正のスクロール量になるよう符号を反転する
        steps = int(-event.delta / 120)
        if steps:
            self.canvas.yview_scroll(steps, "units")
        return "break"

    def _on_form_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        # キャンバス幅に中身を合わせ、横スクロールを出さない
        self.canvas.itemconfigure(self.form_id, width=event.width)
        wrap = max(event.width - 48, 280)
        for field in self.rows:
            field["hint"].configure(wraplength=wrap)

    def _update_scroll(self):
        self.form.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _place_sash(self):
        """ログ欄がオプションを埋め尽くさない位置に仕切りを置く。"""
        try:
            height = self.paned.winfo_height()
            # 未表示の仕切りを動かすと TclError になるので、まとめて握る
            if height > 200:
                self.paned.sashpos(0, int(height * 0.72))
        except tk.TclError:
            return

    def _apply_filter(self, *_args):
        """検索語に一致する項目だけを残す。"""
        if not self.group_frames:
            return
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        visible_total = 0
        for group in GROUPS:
            frame = self.group_frames[group]
            shown = 0
            for field in self.rows:
                if field["spec"]["group"] != group:
                    continue
                spec = field["spec"]
                blob = " ".join(
                    (spec["label"], spec["flag"], spec["hint"], spec["key"])
                ).lower()
                # 空の検索は全件。語があれば名前、フラグ、説明のどれかに含まれるものだけ
                matched = (not query) or (query in blob)
                if matched:
                    field["row"].pack(fill="x", padx=4, pady=1)
                    shown += 1
                else:
                    field["row"].pack_forget()
            if shown:
                frame.pack(fill="x", padx=8, pady=6)
                visible_total += shown
            else:
                frame.pack_forget()
        self.count_label.configure(
            text=str(visible_total) + " / " + str(len(OPTIONS))
        )
        self._update_scroll()

    def _on_form_changed(self, *_args):
        # 読み込み中の代入で、コマンド欄を何度も作り直さない
        if self._loading:
            return
        self._refresh_preview()

    def _on_start_key(self, _event):
        self._start()
        return "break"

    def _focus_search(self, _event):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _sync_optional(self, key):
        """「値を指定」のときだけ、右の入力欄を有効にする。"""
        field = self.fields[key]
        enabled = field["mode"].get() == "値を指定"
        field["entry"].configure(state="normal" if enabled else "disabled")

    def _collect_values(self):
        """画面の内容を、引数生成用の辞書にする。"""
        values = {}
        for key, field in self.fields.items():
            kind = field["kind"]
            if kind == "bool":
                values[key] = bool(field["var"].get())
                continue
            if kind == "optional":
                mode = LABEL_TO_MODE.get(field["mode"].get(), "off")
                values[key] = {"mode": mode, "value": field["var"].get()}
                continue
            if kind == "choice":
                picked = field["var"].get().strip()
                # コンボの「指定しない」は、フラグを付けない空文字にする
                values[key] = "" if picked == UNSPECIFIED else picked
                continue
            values[key] = clean_text(field["var"].get())
        return values

    def _set_field(self, key, raw):
        """保存値を 1 項目へ戻す。"""
        field = self.fields[key]
        kind = field["kind"]
        if kind == "bool":
            field["var"].set(coerce_bool(raw))
            return
        if kind == "optional":
            payload = raw if isinstance(raw, dict) else {}
            mode = payload.get("mode", "off")
            field["mode"].set(MODE_TO_LABEL.get(mode, UNSPECIFIED))
            field["var"].set(str(payload.get("value", "")))
            self._sync_optional(key)
            return
        if kind == "choice":
            text = str(raw or "").strip()
            field["var"].set(text or UNSPECIFIED)
            return
        field["var"].set("" if raw is None else str(raw))

    def _reset_fields(self):
        """オプション欄だけを未指定に戻す。パス欄は触らない。"""
        for _key, field in self.fields.items():
            kind = field["kind"]
            if kind == "bool":
                field["var"].set(False)
            elif kind == "optional":
                field["mode"].set(UNSPECIFIED)
                field["var"].set("")
                self._sync_optional(field["spec"]["key"])
            elif kind == "choice":
                field["var"].set(UNSPECIFIED)
            else:
                field["var"].set("")

    def _refresh_preview(self):
        """下段のコマンドと、接続指定の警告を更新する。"""
        if not hasattr(self, "command_box"):
            return
        values = self._collect_values()
        program = clean_text(self.scrcpy_var.get()) or "scrcpy"
        try:
            extra = shlex.split(self.extra_var.get(), posix=True)
            extra_error = ""
        except ValueError as exc:
            extra = []
            extra_error = "追加の引数を解釈できません: " + str(exc)
        args = build_args(program, values, extra)
        self._current_args = args
        self._set_command(command_line(args))
        if selector_conflict(values):
            notice = "接続先の指定が複数あります。シリアル / USB / TCP / 無線 は一つにしてください。"
        else:
            notice = extra_error
        self.selector_label.configure(text=notice)

    def _set_command(self, text):
        self.command_box.configure(state="normal")
        self.command_box.delete("1.0", "end")
        self.command_box.insert("1.0", text)
        self.command_box.configure(state="disabled")

    def _resolve_exe(self, text):
        """入力が実ファイルか、PATH 上の名前ならそのパスを返す。"""
        cleaned = clean_text(text)
        if not cleaned:
            return ""
        if os.path.isfile(cleaned):
            return cleaned
        found = shutil.which(cleaned)
        return found or ""

    def _browse_executable(self, variable, title):
        chosen = filedialog.askopenfilename(
            title=title,
            filetypes=(
                ("実行ファイル", "*.exe"),
                ("すべて", "*.*"),
            ),
            parent=self.root,
        )
        # キャンセル時は、今入っているパスを消さない
        if chosen:
            variable.set(chosen)

    def _browse_scrcpy(self):
        self._browse_executable(self.scrcpy_var, "scrcpy.exe を選択")

    def _browse_adb(self):
        self._browse_executable(self.adb_var, "adb.exe を選択")

    def _browse_path(self, key):
        current = clean_text(self.fields[key]["var"].get())
        initial = os.path.dirname(current) if current else ""
        chosen = filedialog.asksaveasfilename(
            title="録画ファイル",
            initialdir=initial or None,
            defaultextension=".mp4",
            filetypes=(
                ("MP4", "*.mp4"),
                ("MKV", "*.mkv"),
                ("音声", "*.m4a *.mka *.opus *.aac *.flac *.wav"),
                ("すべて", "*.*"),
            ),
            parent=self.root,
        )
        if chosen:
            self.fields[key]["var"].set(chosen)

    def _detect_tools(self):
        """既知の場所から scrcpy と adb を入れ直す。"""
        scrcpy = find_scrcpy()
        if scrcpy:
            self.scrcpy_var.set(scrcpy)
        adb = find_adb(scrcpy)
        if adb:
            self.adb_var.set(adb)
        if not scrcpy:
            self._set_status("scrcpy.exe が見つかりません。参照から指定してください。")
            return
        self._set_status("検出しました")

    def _fill_missing_tools(self):
        """空欄のときだけ自動検出で埋める。手入力は残す。"""
        if not clean_text(self.scrcpy_var.get()):
            found = find_scrcpy()
            if found:
                self.scrcpy_var.set(found)
        if not clean_text(self.adb_var.get()):
            found_adb = find_adb(self._resolve_exe(self.scrcpy_var.get()))
            if found_adb:
                self.adb_var.set(found_adb)

    def refresh_devices(self):
        """adb devices -l を裏で実行する。"""
        adb_path = self._resolve_exe(self.adb_var.get())
        if not adb_path:
            adb_path = find_adb(self._resolve_exe(self.scrcpy_var.get()))
            if adb_path:
                self.adb_var.set(adb_path)
        if not adb_path:
            self._set_status("adb が見つかりません")
            return
        self._set_status("端末を検索しています…")

        def work():
            try:
                devices, output, code = query_devices(adb_path)
            except (OSError, subprocess.SubprocessError) as exc:
                self.queue.put(("devices-error", str(exc)))
                return
            self.queue.put(("devices", devices, output, code))

        threading.Thread(target=work, daemon=True).start()

    def _show_devices(self, devices):
        """コンボボックスを端末一覧で置き換える。"""
        self.device_serials = {}
        labels = []
        for device in devices:
            label = device["serial"] + " — " + device["state"]
            if device.get("model"):
                label += " (" + device["model"] + ")"
            self.device_serials[label] = device["serial"]
            labels.append(label)
        self.device_combo["values"] = labels
        # 1 台だけでシリアル未入力なら、その端末を選んだ状態にする
        serial_empty = not clean_text(self.fields["serial"]["var"].get())
        if len(devices) == 1 and serial_empty:
            self.device_var.set(labels[0])
            self.fields["serial"]["var"].set(devices[0]["serial"])

    def _on_device_picked(self, _event=None):
        serial = self.device_serials.get(self.device_var.get())
        if not serial:
            return
        # 一覧で選んだシリアルを、--serial の入力へコピーする
        self.fields["serial"]["var"].set(serial)

    def _busy(self):
        return self.proc is not None and self.proc.poll() is None

    def _start(self):
        """現在のフォームで scrcpy を起動する。"""
        if self._busy():
            self._set_status("すでに起動中です")
            return
        if self._info_running:
            self._set_status("端末情報の取得が終わるまで待ってください")
            return
        executable = self._resolve_exe(self.scrcpy_var.get())
        if not executable:
            messagebox.showerror(
                "scrcpy",
                "scrcpy.exe が見つかりません。パスを指定するか、自動検出を押してください。",
                parent=self.root,
            )
            return
        adb_text = clean_text(self.adb_var.get())
        adb_path = ""
        if adb_text:
            adb_path = self._resolve_exe(adb_text)
            if not adb_path:
                messagebox.showerror(
                    "adb",
                    "adb のパスが正しくありません。",
                    parent=self.root,
                )
                return
        try:
            extra = shlex.split(self.extra_var.get(), posix=True)
        except ValueError as exc:
            messagebox.showerror("追加の引数", str(exc), parent=self.root)
            return
        values = self._collect_values()
        if selector_conflict(values):
            proceed = messagebox.askyesno(
                "接続先",
                "接続先の指定が複数あります。このまま起動しますか？",
                parent=self.root,
            )
            if not proceed:
                return
        args = build_args(executable, values, extra)
        env = os.environ.copy()
        # 子プロセス側でも pyc を作らせない
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if adb_path:
            # scrcpy は環境変数 ADB の実行ファイルを使う
            env["ADB"] = adb_path
        show_console = bool(self.console_var.get())
        popen_kwargs = {
            "args": args,
            "cwd": working_directory(executable),
            "env": env,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if show_console:
                # 別コンソールにログを出す。GUI のログ欄には流れない
                flags |= subprocess.CREATE_NEW_CONSOLE
                popen_kwargs["creationflags"] = flags
            else:
                flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                popen_kwargs["creationflags"] = flags
                popen_kwargs["stdout"] = subprocess.PIPE
                popen_kwargs["stderr"] = subprocess.STDOUT
                popen_kwargs["text"] = True
                popen_kwargs["encoding"] = "utf-8"
                popen_kwargs["errors"] = "replace"
        else:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.STDOUT
            popen_kwargs["text"] = True
            popen_kwargs["encoding"] = "utf-8"
            popen_kwargs["errors"] = "replace"
        try:
            proc = subprocess.Popen(**popen_kwargs)
        except OSError as exc:
            messagebox.showerror("起動失敗", str(exc), parent=self.root)
            return
        self.proc = proc
        self._stop_attempts = 0
        self._set_running(True)
        self._append_log("起動: " + command_line(args))
        folder = popen_kwargs.get("cwd")
        if folder:
            self._append_log("作業フォルダ: " + folder)
        if adb_path:
            self._append_log("ADB=" + adb_path)
        if show_console:
            self._append_log("出力は scrcpy のコンソールに表示します")
        self._set_status("起動しました")
        self._spawn_reader(proc)
        try:
            self._save_session()
        except OSError as exc:
            self._append_log("設定の保存に失敗しました: " + str(exc))

    def _spawn_reader(self, proc):
        """標準出力をログへ流し、終了コードをキューへ返す。"""

        def work():
            stream = proc.stdout
            if stream is not None:
                for line in stream:
                    self.queue.put(("log", proc, line.rstrip("\r\n")))
            code = proc.wait()
            self.queue.put(("exit", proc, code))

        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        """一度目はウィンドウを閉じ、二度目はプロセスを強制終了する。"""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self._set_running(False)
            return
        self._stop_attempts += 1
        if self._stop_attempts >= 2:
            self._append_log("プロセスを強制終了します")
            proc.kill()
            self._set_status("強制終了しました")
            return
        closed = request_window_close(proc.pid)
        if closed:
            self._append_log("ウィンドウを閉じる要求を送りました")
            self._set_status("停止しています…もう一度押すと強制終了します")
            return
        # Windows の terminate は強制終了なので、後始末が走らないことがある
        self._append_log("ウィンドウが無いためプロセスを強制終了します")
        proc.terminate()
        self._set_status("停止しています…")

    def _set_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _run_info(self, label, flag):
        """一覧やバージョンを、ミラーせずに実行してログへ出す。"""
        if self._busy():
            self._set_status("ミラー中は端末情報を取れません")
            return
        if self._info_running:
            self._set_status("前の取得がまだ終わっていません")
            return
        executable = self._resolve_exe(self.scrcpy_var.get())
        if not executable:
            messagebox.showerror(
                "scrcpy",
                "scrcpy.exe が見つかりません。",
                parent=self.root,
            )
            return
        values = self._collect_values()
        args = build_info_args(executable, values, flag)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        adb_path = self._resolve_exe(self.adb_var.get())
        if adb_path:
            env["ADB"] = adb_path
        self._info_running = True
        self._set_status(label + " を取得しています…")
        self._append_log("実行: " + command_line(args))

        def work():
            try:
                proc = subprocess.Popen(
                    args,
                    cwd=working_directory(executable),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **self._hidden_popen(),
                )
            except OSError as exc:
                self.queue.put(("info-log", str(exc)))
                self.queue.put(("info-done", label, 1))
                return
            try:
                output, _err = proc.communicate(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill()
                output, _err = proc.communicate()
                output = (output or "") + "\nタイムアウトしました"
            self.queue.put(("info-log", output or ""))
            self.queue.put(("info-done", label, proc.returncode))

        threading.Thread(target=work, daemon=True).start()

    def _hidden_popen(self):
        """問い合わせ実行でコンソール窓を出さない。"""
        if os.name != "nt":
            return {}
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flag}

    def _poll(self):
        """ワーカースレッドからの通知を、画面スレッドで処理する。"""
        try:
            while True:
                self._handle_queue(self.queue.get_nowait())
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._poll)
        except tk.TclError:
            return

    def _handle_queue(self, item):
        kind = item[0]
        if kind == "log":
            proc, text = item[1], item[2]
            # すでに次の起動へ進んでいる場合、古い出力は捨てる
            if proc is self.proc and text:
                self._append_log(text)
            return
        if kind == "exit":
            proc, code = item[1], item[2]
            if proc is not self.proc:
                return
            self._set_running(False)
            known = EXIT_TEXT.get(code)
            if known is None:
                message = "終了コード " + str(code)
            else:
                message = known + "（" + str(code) + "）"
            self._append_log(message)
            self._set_status(message)
            return
        if kind == "devices":
            devices, output, code = item[1], item[2], item[3]
            self._show_devices(devices)
            if code != 0:
                if output.strip():
                    self._append_log(output.strip())
                self._set_status("adb が失敗しました")
                return
            self._set_status("端末 " + str(len(devices)) + " 台")
            return
        if kind == "devices-error":
            self._append_log(item[1])
            self._set_status("端末一覧に失敗しました")
            return
        if kind == "info-log":
            text = str(item[1] or "").strip()
            if text:
                self._append_log(text)
            return
        if kind == "info-done":
            label, code = item[1], item[2]
            self._info_running = False
            if code == 0:
                self._set_status(label + " が完了しました")
            else:
                self._set_status(label + " が失敗しました（" + str(code) + "）")

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _copy_command(self):
        text = command_line(self._current_args)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("コマンドをコピーしました")

    def _reset(self):
        proceed = messagebox.askyesno(
            "リセット",
            "入力したオプションをすべて空に戻しますか？",
            parent=self.root,
        )
        if not proceed:
            return
        self._loading = True
        try:
            self.extra_var.set("")
            self._reset_fields()
        finally:
            self._loading = False
        self._refresh_preview()
        self._set_status("オプションを空に戻しました")

    def _session_payload(self, with_paths):
        payload = {
            "extra": self.extra_var.get(),
            "show_console": bool(self.console_var.get()),
            "values": self._collect_values(),
        }
        if with_paths:
            payload["scrcpy_path"] = self.scrcpy_var.get().strip()
            payload["adb_path"] = self.adb_var.get().strip()
        return payload

    def _apply_session(self, data, replace_paths):
        """保存データでフォームを置き換える。"""
        self._loading = True
        try:
            if replace_paths:
                self.scrcpy_var.set(data.get("scrcpy_path") or "")
                self.adb_var.set(data.get("adb_path") or "")
            if "show_console" in data:
                self.console_var.set(coerce_bool(data.get("show_console")))
            self.extra_var.set(data.get("extra") or "")
            self._reset_fields()
            values = data.get("values") or {}
            # 中身が辞書でないファイルは、項目復元をスキップする
            if not isinstance(values, dict):
                values = {}
            for key, raw in values.items():
                if key in self.fields:
                    self._set_field(key, raw)
        finally:
            self._loading = False
        self._refresh_preview()
        self._apply_filter()

    def load_session(self):
        """前回の画面を開き、パスが空なら検出する。"""
        data = read_json(session_path())
        if data:
            self._apply_session(data, replace_paths=True)
        self._fill_missing_tools()
        self._refresh_preview()

    def _save_session(self):
        write_json(session_path(), self._session_payload(with_paths=True))

    def _save_profile(self):
        name = simpledialog.askstring(
            "プロファイル",
            "名前",
            parent=self.root,
        )
        if not name or not str(name).strip():
            return
        try:
            path = profile_path(name)
        except ValueError as exc:
            messagebox.showerror("プロファイル", str(exc), parent=self.root)
            return
        if os.path.isfile(path):
            overwrite = messagebox.askyesno(
                "上書き",
                "同じ名前があります。上書きしますか？",
                parent=self.root,
            )
            if not overwrite:
                return
        try:
            write_json(path, self._session_payload(with_paths=False))
        except OSError as exc:
            messagebox.showerror("プロファイル", str(exc), parent=self.root)
            return
        self._set_status("保存しました")

    def _load_profile(self):
        names = list_profile_names()
        if not names:
            messagebox.showinfo(
                "プロファイル",
                "保存済みのプロファイルはありません。",
                parent=self.root,
            )
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("プロファイルを読み込む")
        dialog.transient(self.root)
        dialog.grab_set()
        listed = tk.Listbox(dialog, height=min(max(len(names), 4), 14), width=42)
        for name in names:
            listed.insert("end", name)
        listed.selection_set(0)
        listed.pack(fill="both", expand=True, padx=8, pady=8)

        def selected_name():
            picked = listed.curselection()
            if not picked:
                return ""
            return listed.get(picked[0])

        def load():
            name = selected_name()
            if not name:
                return
            data = read_json(profile_path(name))
            if not data:
                messagebox.showerror(
                    "プロファイル",
                    "読み込めませんでした。",
                    parent=dialog,
                )
                return
            # 実行ファイルのパスは、この PC のセッション側を維持する
            self._apply_session(data, replace_paths=False)
            dialog.destroy()
            self._set_status("読み込みました: " + name)

        def delete():
            name = selected_name()
            if not name:
                return
            confirm = messagebox.askyesno(
                "削除",
                "「" + name + "」を削除しますか？",
                parent=dialog,
            )
            if not confirm:
                return
            try:
                os.remove(profile_path(name))
            except OSError as exc:
                messagebox.showerror("削除", str(exc), parent=dialog)
                return
            listed.delete(listed.curselection()[0])
            if listed.size() == 0:
                dialog.destroy()
                self._set_status("プロファイルを削除しました")

        bar = ttk.Frame(dialog)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="読み込む", command=load).pack(side="left")
        ttk.Button(bar, text="削除", command=delete).pack(side="left", padx=6)
        ttk.Button(bar, text="閉じる", command=dialog.destroy).pack(side="right")
        listed.bind("<Double-Button-1>", lambda _event: load())
        dialog.wait_window()

    def _open_help(self):
        path = os.path.join(resource_root(), "doc", "gui.html")
        if os.name == "nt" and os.path.isfile(path):
            os.startfile(path)
            return
        messagebox.showinfo("使い方", HELP_TEXT, parent=self.root)

    def _set_status(self, text):
        self.status_label.configure(text=text)

    def _on_close(self):
        if self._busy():
            stop = messagebox.askyesno(
                "終了",
                "scrcpy を停止して終了しますか？",
                parent=self.root,
            )
            if not stop:
                return
            self._stop()
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        try:
            self._save_session()
        except OSError as exc:
            messagebox.showerror("保存", str(exc), parent=self.root)
        self.root.destroy()


def resource_root():
    """説明ファイルを探す基準ディレクトリ。"""
    meipass = getattr(sys, "_MEIPASS", "")
    # PyInstaller で固めた exe は、同梱ファイルを一時フォルダへ展開する
    if getattr(sys, "frozen", False) and meipass:
        return meipass
    # ソースから起動したときは、gui の一つ上がリポジトリ直下
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    """DPI 設定のあとでウィンドウを開く。"""
    enable_dpi()
    root = tk.Tk()
    ScrcpyGui(root)
    root.mainloop()
