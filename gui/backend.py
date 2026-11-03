"""scrcpy と adb の探索、引数の組み立て、プロセスの停止。"""

import glob
import json
import os
import shutil
import subprocess

from gui.options_catalog import INFO_KEYS, OPTIONS, SELECTOR_KEYS


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def clean_text(text):
    """前後の空白と、貼り付け時の引用符を外す。"""
    cleaned = str(text or "").strip()
    quote_pair = len(cleaned) >= 2 and cleaned[0] == cleaned[-1]
    # パスを "..." のまま貼られたとき、引用符自体を値にしない
    if quote_pair and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def config_dir():
    """設定の保存先。リポジトリ内には書かない。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    # ユーザーごとに残し、ソースツリーを汚さない
    path = os.path.join(base, "scrcpyGUI")
    os.makedirs(path, exist_ok=True)
    return path


def session_path():
    """最後に使った画面状態のファイル。"""
    return os.path.join(config_dir(), "session.json")


def profiles_dir():
    """名前付きプロファイルのフォルダ。"""
    path = os.path.join(config_dir(), "profiles")
    os.makedirs(path, exist_ok=True)
    return path


def _hidden_kwargs():
    """子プロセスのコンソール窓を出さないための引数。"""
    if os.name != "nt":
        return {}
    # adb devices の黒い窓を、GUI の裏で出さない
    return {"creationflags": CREATE_NO_WINDOW}


def _first_file(pattern):
    """グロブに一致した実ファイルのうち、名前順で最初のものを返す。"""
    matches = sorted(glob.glob(pattern))
    for match in matches:
        # ディレクトリではなく実行ファイルだけを採用する
        if os.path.isfile(match):
            return match
    return ""


def find_scrcpy():
    """PATH と既知のインストール場所から scrcpy を探す。"""
    env_path = clean_text(os.environ.get("SCRCPY", ""))
    # 利用者が明示したパスを最優先する
    if env_path and os.path.isfile(env_path):
        return env_path
    on_path = shutil.which("scrcpy")
    # winget や scoop が PATH に入れている場合はそれを使う
    if on_path:
        return on_path
    local_app = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    patterns = []
    if local_app:
        winget = os.path.join(
            local_app, "Microsoft", "WinGet", "Packages", "Genymobile.scrcpy*"
        )
        # 展開フォルダの深さが配布物によって一段違う
        patterns.append(os.path.join(winget, "scrcpy.exe"))
        patterns.append(os.path.join(winget, "*", "scrcpy.exe"))
    if user_profile:
        patterns.append(
            os.path.join(
                user_profile, "scoop", "apps", "scrcpy", "current", "scrcpy.exe"
            )
        )
    patterns.append(os.path.join(program_data, "chocolatey", "bin", "scrcpy.exe"))
    for pattern in patterns:
        found = _first_file(pattern)
        if found:
            return found
    return ""


def find_adb(scrcpy_path):
    """scrcpy と同じフォルダ、PATH、Android SDK から adb を探す。"""
    env_path = clean_text(os.environ.get("ADB", ""))
    # scrcpy 本体と同じ規則で、環境変数 ADB を優先する
    if env_path and os.path.isfile(env_path):
        return env_path
    scrcpy_text = clean_text(scrcpy_path)
    if scrcpy_text and os.path.isfile(scrcpy_text):
        sibling = os.path.join(os.path.dirname(scrcpy_text), "adb.exe")
        # 公式 zip は scrcpy.exe と adb.exe が隣同士にある
        if os.path.isfile(sibling):
            return sibling
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        sdk_adb = os.path.join(
            local_app, "Android", "Sdk", "platform-tools", "adb.exe"
        )
        # Android Studio 標準の platform-tools
        if os.path.isfile(sdk_adb):
            return sdk_adb
    return ""


def working_directory(executable):
    """相対パスの録画先が scrcpy の隣に落ちるよう、作業フォルダを決める。"""
    if executable and os.path.isabs(executable) and os.path.isfile(executable):
        folder = os.path.dirname(executable)
        # ドライブルートだけだと相対パスの意味がぼやけるので、通常のフォルダだけ返す
        if folder:
            return folder
    return None


def build_args(executable, values, extra_args):
    """フォーム値を、シェルを通さない argv にする。"""
    # 実行ファイル自身が argv の先頭
    args = [executable]
    for spec in OPTIONS:
        raw = None if not values else values.get(spec["key"])
        # 保存データに無い項目は、scrcpy の既定値へ任せる
        if raw is None:
            continue
        flag = spec["flag"]
        kind = spec["kind"]
        if kind == "bool":
            # オフのチェックは「オプションを付けない」という意味
            if raw:
                args.append(flag)
            continue
        if kind == "optional":
            payload = raw if isinstance(raw, dict) else {}
            mode = payload.get("mode", "off")
            # フラグだけ（--tcpip や --new-display）は値を続けない
            if mode == "flag":
                args.append(flag)
            elif mode == "value":
                text = clean_text(payload.get("value", ""))
                # 空の「値を指定」は、フラグだけに誤変換しない
                if text:
                    args.append(flag + "=" + text)
            continue
        text = clean_text(raw)
        # 空欄は未指定なので、既定値を上書きしない
        if not text:
            continue
        # スペースを含んでも 1 引数にするため、= で値を接着する
        args.append(flag + "=" + text)
    for extra in extra_args or ():
        token = str(extra).strip()
        # 追加欄のトークンは解釈せず、そのまま後ろへ足す
        if token:
            args.append(token)
    return args


def build_info_args(executable, values, flag):
    """一覧表示やバージョン用に、必要な引数だけを組み立てる。"""
    # --version は端末も他オプションも不要
    if flag == "--version":
        return [executable, "--version"]
    subset = {}
    for key in INFO_KEYS:
        # 接続先と、一覧の結果を変える項目だけ残す
        if values and key in values:
            subset[key] = values[key]
    args = build_args(executable, subset, [])
    # 一覧フラグを最後に足し、ミラー開始ではなく問い合わせにする
    args.append(flag)
    return args


def command_line(args):
    """画面表示用に、Windows の引用規則で 1 行へ戻す。"""
    return subprocess.list2cmdline(args)


def selector_conflict(values):
    """接続先の指定が複数あるか。"""
    count = 0
    for key in SELECTOR_KEYS:
        raw = None if not values else values.get(key)
        if key == "serial":
            # シリアルは文字が入っているときだけ指定になる
            if clean_text(raw):
                count += 1
            continue
        if key == "tcpip":
            mode = raw.get("mode") if isinstance(raw, dict) else "off"
            # フラグのみも値ありも、接続先の指定として数える
            if mode in ("flag", "value"):
                count += 1
            continue
        # USB 選択と TCP 選択はチェック自体が指定
        if raw:
            count += 1
    return count > 1


def parse_adb_devices(text):
    """adb devices -l の出力を、シリアルと状態の配列にする。"""
    devices = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        # 見出し行と空行は端末ではない
        if not line or line.lower().startswith("list of"):
            continue
        parts = line.split()
        # 「シリアル 状態」が無い行は無視する
        if len(parts) < 2:
            continue
        model = ""
        for token in parts[2:]:
            # -l が付ける model: だけを表示名に使う
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
                break
        devices.append(
            {"serial": parts[0], "state": parts[1], "model": model}
        )
    return devices


def query_devices(adb_path):
    """adb devices -l を実行して端末一覧を返す。"""
    completed = subprocess.run(
        [adb_path, "devices", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
        **_hidden_kwargs(),
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    # 終了コードが異常でも、読める範囲の一覧は返す
    return parse_adb_devices(output), output, completed.returncode


def read_json(path):
    """JSON オブジェクトを読む。壊れていれば None。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    # 配列や文字列だけが入ったファイルはセッションとして使わない
    if not isinstance(data, dict):
        return None
    return data


def write_json(path, data):
    """一時ファイル経由で JSON を置き換える。"""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            # 末尾改行を付け、差分ツールで見やすくする
            handle.write("\n")
        # 書き込み完了後に差し替え、途中のファイルを読ませない
        os.replace(temporary, path)
    except OSError:
        if os.path.isfile(temporary):
            os.remove(temporary)
        raise


def profile_path(name):
    """プロファイル名を、そのフォルダ内の JSON パスにする。"""
    cleaned = []
    for char in str(name or "").strip():
        # Windows のファイル名に使えない文字だけを落とす
        if char in '<>:"/\\|?*' or ord(char) < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)
    safe = "".join(cleaned).strip().rstrip(".")
    # 空や親ディレクトリ参照は保存先にしない
    if not safe or safe in (".", ".."):
        raise ValueError("プロファイル名が空です")
    return os.path.join(profiles_dir(), safe + ".json")


def list_profile_names():
    """保存済みプロファイル名を名前順で返す。"""
    names = []
    folder = profiles_dir()
    for entry in sorted(os.listdir(folder)):
        # 拡張子だけを見て、途中ファイルは一覧に出さない
        if entry.lower().endswith(".json"):
            names.append(entry[:-5])
    return names


def request_window_close(pid):
    """指定プロセスの見えるウィンドウへ閉じるメッセージを送る。"""
    if os.name != "nt" or not pid:
        return 0
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return 0
    wm_close = 0x0010
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    targets = []

    @callback_type
    def _callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        # 別プロセスの窓や、見えない補助窓は閉じない
        if owner.value != int(pid) or not user32.IsWindowVisible(hwnd):
            return True
        # 列挙中に閉じるとウィンドウ一覧が壊れ得るので、まず集める
        targets.append(hwnd)
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    # コールバックが列挙中に回収されないよう、呼び出しが終わるまで握る
    user32.EnumWindows(_callback, 0)
    closed = 0
    for hwnd in targets:
        # Destroy ではなく Close を送り、scrcpy 側の後始末を走らせる
        if user32.PostMessageW(hwnd, wm_close, 0, 0):
            closed += 1
    return closed
