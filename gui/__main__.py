"""python -m gui の入口。"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

# バイトコードを出さない設定は、他モジュールを import するより前に行う
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

ROOT = Path(__file__).resolve().parent.parent
ROOT_TEXT = str(ROOT)
# スクリプト直起動でも、リポジトリ直下をパッケージの起点にする
if ROOT_TEXT not in sys.path:
    sys.path.insert(0, ROOT_TEXT)


def _report_startup_error():
    """起動不能時の例外を、ファイルとダイアログの両方に残す。"""
    text = traceback.format_exc()
    path = os.path.join(tempfile.gettempdir(), "scrcpy-gui-error.txt")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        path = ""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        # 空の親ウィンドウだけが見えないようにする
        root.withdraw()
        detail = text if not path else text + "\n\n" + path
        messagebox.showerror("scrcpy GUI", detail)
        root.destroy()
    except Exception:
        return


try:
    from gui.app import main
except Exception:
    _report_startup_error()
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        _report_startup_error()
        raise SystemExit(1)
