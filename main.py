import ctypes
from tkinter import Tk, ttk

from config import ensure_default_config
from ui import App


def set_windows_app_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("civitai.notifier.app")
    except Exception:
        pass


def main():
    set_windows_app_id()
    ensure_default_config()

    root = Tk()

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()