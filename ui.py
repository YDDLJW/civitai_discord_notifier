import threading
import time
from datetime import datetime
from tkinter import END, BooleanVar, StringVar
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from config import (
    DEFAULT_ICON_NAME,
    DEFAULT_INTERVAL_SECONDS,
    LOG_PATH,
    append_log,
    load_config_file,
    resolve_icon_path,
    save_config_file,
)
from notifier_core import NotifierCore
from tray import TrayManager


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Civitai Notifier")
        self.root.geometry("980x780")

        self.username_var = StringVar()
        self.webhook_var = StringVar()
        self.api_key_var = StringVar()
        self.proxy_var = StringVar()
        self.interval_var = StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        self.auto_start_var = BooleanVar(value=False)
        self.start_hidden_var = BooleanVar(value=False)
        self.icon_path_var = StringVar(value=DEFAULT_ICON_NAME)

        self.status_var = StringVar(value="Stopped")
        self.last_run_var = StringVar(value="-")
        self.next_run_var = StringVar(value="-")
        self.total_models_var = StringVar(value="0")
        self.last_new_var = StringVar(value="0")
        self.tray_status_var = StringVar(value="托盘未启动")
        self.log_file_var = StringVar(value=str(LOG_PATH))

        self.core = NotifierCore(self.thread_safe_log)
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self.exiting = False

        self.tray = TrayManager(
            icon_path_getter=lambda: self.icon_path_var.get(),
            logger=self.thread_safe_log,
            on_open=self.restore_from_tray,
            on_close=self.close_app,
        )

        self.load_config()
        self.build_ui()
        self.apply_window_icon()
        self.ensure_tray_started()

        self.root.protocol("WM_DELETE_WINDOW", self.handle_close_request)

        if self.auto_start_var.get():
            self.root.after(300, self.start_loop)

        if self.start_hidden_var.get():
            self.root.after(600, self.minimize_to_tray)

    def load_config(self):
        cfg = load_config_file()
        self.username_var.set(cfg.get("username", ""))
        self.webhook_var.set(cfg.get("webhook", ""))
        self.api_key_var.set(cfg.get("api_key", ""))
        self.proxy_var.set(cfg.get("proxy_url", ""))
        self.interval_var.set(str(cfg.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)))
        self.auto_start_var.set(bool(cfg.get("auto_start", False)))
        self.start_hidden_var.set(bool(cfg.get("start_hidden_to_tray", False)))
        self.icon_path_var.set(cfg.get("icon_path", DEFAULT_ICON_NAME))

    def save_config(self):
        try:
            interval = self.get_interval_seconds()
        except ValueError as e:
            messagebox.showerror("错误", str(e))
            return

        cfg = {
            "username": self.username_var.get().strip(),
            "webhook": self.webhook_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "proxy_url": self.proxy_var.get().strip(),
            "interval_seconds": interval,
            "auto_start": self.auto_start_var.get(),
            "start_hidden_to_tray": self.start_hidden_var.get(),
            "icon_path": self.icon_path_var.get().strip() or DEFAULT_ICON_NAME,
        }
        save_config_file(cfg)
        self.apply_window_icon()
        self.restart_tray_icon()
        self.thread_safe_log("配置已保存。")

    def get_interval_seconds(self):
        raw = self.interval_var.get().strip() or str(DEFAULT_INTERVAL_SECONDS)
        interval = int(raw)
        if interval < 30:
            raise ValueError("轮询间隔不能小于 30 秒")
        return interval

    def collect_settings(self):
        return {
            "username": self.username_var.get().strip(),
            "webhook": self.webhook_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "proxy_url": self.proxy_var.get().strip(),
            "interval": self.get_interval_seconds(),
        }

    def build_ui(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        config_frame = ttk.LabelFrame(frm, text="配置", padding=10)
        config_frame.pack(fill="x")

        self.add_labeled_entry(config_frame, "Civitai 用户名", self.username_var, 0)
        self.add_labeled_entry(config_frame, "Discord Webhook", self.webhook_var, 1, width=82)
        self.add_labeled_entry(config_frame, "Civitai API Key", self.api_key_var, 2, show="*")
        self.add_labeled_entry(config_frame, "代理 URL（可选）", self.proxy_var, 3)
        self.add_labeled_entry(config_frame, "轮询间隔（秒）", self.interval_var, 4)
        self.add_labeled_entry(config_frame, "图标路径（相对根目录或绝对路径）", self.icon_path_var, 5)

        ttk.Checkbutton(config_frame, text="启动时自动开始", variable=self.auto_start_var).grid(
            row=6, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(config_frame, text="启动后自动隐藏到托盘", variable=self.start_hidden_var).grid(
            row=7, column=1, sticky="w", pady=(6, 0)
        )

        button_frame = ttk.Frame(frm)
        button_frame.pack(fill="x", pady=10)

        ttk.Button(button_frame, text="保存配置", command=self.save_config).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="立即执行一次", command=self.run_once_async).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="开始循环", command=self.start_loop).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="停止", command=self.stop_loop).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="隐藏到托盘", command=self.minimize_to_tray).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="打开窗口", command=self.restore_window).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="重新加载图标", command=self.reload_icon).pack(side="left")

        status_frame = ttk.LabelFrame(frm, text="状态", padding=10)
        status_frame.pack(fill="x")

        self.add_status_row(status_frame, "当前状态", self.status_var, 0)
        self.add_status_row(status_frame, "上次运行", self.last_run_var, 1)
        self.add_status_row(status_frame, "下次运行", self.next_run_var, 2)
        self.add_status_row(status_frame, "最近一次发现新版本数", self.last_new_var, 3)
        self.add_status_row(status_frame, "当前抓取模型数", self.total_models_var, 4)
        self.add_status_row(status_frame, "托盘状态", self.tray_status_var, 5)
        self.add_status_row(status_frame, "日志文件", self.log_file_var, 6)

        log_frame = ttk.LabelFrame(frm, text="日志", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = ScrolledText(log_frame, wrap="word", height=25)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def add_labeled_entry(self, parent, label, var, row, width=60, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(parent, textvariable=var, width=width, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)

    def add_status_row(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="w", pady=2)

    def set_status(self, value):
        self.root.after(0, lambda: self.status_var.set(value))

    def thread_safe_log(self, msg):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}\n"

        try:
            append_log(line)
        except Exception:
            pass

        def append_ui():
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert(END, line)
                self.log_text.see(END)
                self.log_text.configure(state="disabled")
            except Exception:
                pass

        self.root.after(0, append_ui)

    def run_once_async(self):
        threading.Thread(target=lambda: self.execute_once(manual=True), daemon=True).start()

    def execute_once(self, manual=False):
        try:
            settings = self.collect_settings()
        except ValueError as e:
            self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
            return

        if manual:
            self.set_status("Running once")

        self.thread_safe_log("Starting check...")
        result = self.core.run_once(
            username=settings["username"],
            webhook=settings["webhook"],
            api_key=settings["api_key"],
            proxy_url=settings["proxy_url"],
        )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.root.after(0, lambda: self.last_run_var.set(now_str))
        self.root.after(0, lambda: self.total_models_var.set(str(result.get("total_count", 0))))
        self.root.after(0, lambda: self.last_new_var.set(str(result.get("new_count", 0))))

        if result.get("new_count", 0) > 0:
            if result.get("initialized") and result.get("first_run_notified_record"):
                rec = result["first_run_notified_record"]
                self.show_tray_notification(
                    "Civitai 首次初始化通知",
                    f"{rec.get('name')} / {rec.get('latest_version_name')}",
                )
            else:
                count = result.get("new_count", 0)
                names = [r.get("name", "Unknown") for r in result.get("notified_records", [])[:3]]
                suffix = ", ".join(names) if names else f"{count} 个新版本"
                self.show_tray_notification("Civitai 有更新", suffix)

        if manual and not self.is_running:
            self.set_status("Stopped")

    def start_loop(self):
        if self.is_running:
            self.thread_safe_log("Loop already running.")
            return

        try:
            self.collect_settings()
        except ValueError as e:
            messagebox.showerror("错误", str(e))
            return

        self.is_running = True
        self.stop_event.clear()
        self.set_status("Running")
        self.worker_thread = threading.Thread(target=self.loop_worker, daemon=True)
        self.worker_thread.start()
        self.thread_safe_log("Background loop started.")

    def stop_loop(self):
        if not self.is_running:
            self.thread_safe_log("Loop already stopped.")
            return
        self.stop_event.set()
        self.is_running = False
        self.set_status("Stopped")
        self.root.after(0, lambda: self.next_run_var.set("-"))
        self.thread_safe_log("Stopping background loop...")

    def loop_worker(self):
        while not self.stop_event.is_set():
            start_time = time.time()
            self.execute_once(manual=False)

            try:
                interval = self.get_interval_seconds()
            except Exception:
                interval = DEFAULT_INTERVAL_SECONDS

            next_run_ts = datetime.fromtimestamp(start_time + interval).strftime("%Y-%m-%d %H:%M:%S")
            self.root.after(0, lambda ts=next_run_ts: self.next_run_var.set(ts))

            elapsed = time.time() - start_time
            remaining = max(1, int(interval - elapsed))
            for _ in range(remaining):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        self.root.after(0, lambda: self.next_run_var.set("-"))

    def apply_window_icon(self):
        icon_path = resolve_icon_path(self.icon_path_var.get())
        if icon_path.exists() and icon_path.suffix.lower() == ".ico":
            try:
                self.root.iconbitmap(default=str(icon_path))
            except Exception:
                pass

    def reload_icon(self):
        self.apply_window_icon()
        self.restart_tray_icon()
        self.thread_safe_log("图标已重新加载。")

    def ensure_tray_started(self):
        ok = self.tray.start()
        self.tray_status_var.set("托盘已启动" if ok else "缺少 pystray/pillow 或托盘启动失败")

    def restart_tray_icon(self):
        ok = self.tray.restart()
        self.tray_status_var.set("托盘已启动" if ok else "托盘重启失败")

    def minimize_to_tray(self):
        self.ensure_tray_started()
        self.root.withdraw()
        self.tray_status_var.set("已隐藏到托盘")
        self.thread_safe_log("窗口已隐藏到托盘。")

    def restore_window(self):
        self.root.after(0, self._restore_window_ui)

    def restore_from_tray(self):
        self.root.after(0, self._restore_window_ui)

    def _restore_window_ui(self):
        self.root.deiconify()
        self.root.after(50, self.root.lift)
        self.root.after(100, lambda: self.root.attributes("-topmost", True))
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.tray_status_var.set("托盘已启动（窗口已打开）")
        self.thread_safe_log("窗口已打开。")

    def show_tray_notification(self, title, message):
        if self.tray.notify(title, message):
            return
        self.root.after(0, lambda: messagebox.showinfo(title, message))

    def handle_close_request(self):
        choice = messagebox.askyesnocancel(
            "关闭窗口",
            "选择“是”将完全退出程序；选择“否”将隐藏到系统托盘；选择“取消”则不做任何操作。",
        )
        if choice is None:
            return
        if choice:
            self.close_app()
        else:
            self.minimize_to_tray()

    def close_app(self):
        self.exiting = True
        self.stop_event.set()
        self.is_running = False
        self.tray.stop()
        self.root.after(0, self.root.destroy)