import json
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import Tk, StringVar, BooleanVar, END
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: requests\nInstall with: pip install requests")

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "civitai_notifier_config.json"
STATE_PATH = APP_DIR / "civitai_model_state.json"
DEFAULT_INTERVAL_SECONDS = 300
NOTIFY_WINDOW_HOURS = 48
FIRST_RUN_NOTIFY_HOURS = 24


class NotifierCore:
    def __init__(self, logger):
        self.logger = logger
        self.session = requests.Session()

    def log(self, msg):
        self.logger(msg)

    def run_once(self, username, webhook, api_key="", proxy_url=""):
        if not username:
            self.log("Missing CIVITAI_USERNAME")
            return {"ok": False, "new_count": 0, "total_count": 0}
        if not webhook:
            self.log("Missing DISCORD_WEBHOOK_URL")
            return {"ok": False, "new_count": 0, "total_count": 0}

        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        else:
            self.session.proxies.clear()

        items = self.fetch_all_models(username, api_key)
        if items is None:
            return {"ok": False, "new_count": 0, "total_count": 0}

        state_key = f"models:{username}"
        saved = self.load_state().get(state_key)
        history = saved if isinstance(saved, dict) else {"models": {}, "versions": {}}

        if not isinstance(history.get("models"), dict):
            history["models"] = {}
        if not isinstance(history.get("versions"), dict):
            history["versions"] = {}

        current_records = []
        for model in items:
            record = build_model_record(model, username)
            if record and record.get("id") and record.get("latest_version_id"):
                current_records.append(record)

        if not history["models"] and not history["versions"]:
            init_models = {}
            init_versions = {}
            for record in current_records:
                init_models[str(record["id"])] = record
                init_versions[str(record["latest_version_id"])] = {
                    "model_id": record["id"],
                    "model_name": record["name"],
                    "version_id": record["latest_version_id"],
                    "version_name": record["latest_version_name"],
                    "created_at": record["latest_version_created_at"],
                    "notified_at": None,
                }

            latest_recent_record = self.pick_first_run_record(current_records)
            notified_version_ids = set()
            if latest_recent_record:
                payload = format_discord_payload(latest_recent_record)
                try:
                    resp = self.session.post(webhook, json=payload, timeout=30)
                except Exception as e:
                    self.log(
                        f"First run notification failed for model {latest_recent_record['id']}, version {latest_recent_record['latest_version_id']}: {e}"
                    )
                else:
                    if not resp.ok:
                        self.log(
                            f"First run notification failed for model {latest_recent_record['id']}, version {latest_recent_record['latest_version_id']}: {resp.status_code} {safe_read_text_response(resp)}"
                        )
                    else:
                        version_id = str(latest_recent_record["latest_version_id"])
                        notified_version_ids.add(version_id)
                        init_versions[version_id]["notified_at"] = datetime.now().isoformat()
                        self.log(
                            f"First run: notified latest version within {FIRST_RUN_NOTIFY_HOURS}h: {latest_recent_record['name']} ({latest_recent_record['id']}) "
                            f"v={latest_recent_record['latest_version_name']} versionId={latest_recent_record['latest_version_id']}"
                        )
            else:
                self.log(f"First run: no version found within {FIRST_RUN_NOTIFY_HOURS}h, state initialized only.")

            all_state = self.load_state()
            all_state[state_key] = {
                "models": init_models,
                "versions": init_versions,
                "initializedAt": datetime.now().isoformat(),
            }
            self.save_state(all_state)
            return {"ok": True, "new_count": len(notified_version_ids), "total_count": len(current_records), "initialized": True}

        new_records = []
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - NOTIFY_WINDOW_HOURS * 60 * 60 * 1000

        for record in current_records:
            version_id = str(record["latest_version_id"])
            if version_id in history["versions"]:
                continue

            published_at_ms = safe_parse_time(record.get("latest_version_created_at"))
            if published_at_ms is None or published_at_ms >= cutoff:
                new_records.append(record)
            else:
                self.log(
                    f"Skipped old version: {record['name']} ({record['id']}) "
                    f"v={record['latest_version_name']} versionId={record['latest_version_id']}, "
                    f"latest version created at {record['latest_version_created_at']}"
                )

        new_records.sort(
            key=lambda r: (
                -(safe_parse_time(r.get("latest_version_created_at")) or 0),
                -int(r.get("id") or 0),
            )
        )

        if not new_records:
            self.log("No new model versions found within 48h.")

        notified_version_ids = set()
        for record in new_records:
            payload = format_discord_payload(record)
            try:
                resp = self.session.post(webhook, json=payload, timeout=30)
            except Exception as e:
                self.log(
                    f"Discord webhook failed for model {record['id']}, version {record['latest_version_id']}: {e}"
                )
                continue

            if not resp.ok:
                self.log(
                    f"Discord webhook failed for model {record['id']}, version {record['latest_version_id']}: {resp.status_code} {safe_read_text_response(resp)}"
                )
            else:
                notified_version_ids.add(str(record["latest_version_id"]))
                self.log(
                    f"Notified model/version: {record['name']} ({record['id']}) "
                    f"v={record['latest_version_name']} versionId={record['latest_version_id']}"
                )

        merged_models = dict(history.get("models", {}))
        merged_versions = dict(history.get("versions", {}))
        now_iso = datetime.now().isoformat()

        for record in current_records:
            merged_models[str(record["id"])] = record
            version_id = str(record["latest_version_id"])
            previous = merged_versions.get(version_id, {})
            merged_versions[version_id] = {
                "model_id": record["id"],
                "model_name": record["name"],
                "version_id": record["latest_version_id"],
                "version_name": record["latest_version_name"],
                "created_at": record["latest_version_created_at"],
                "notified_at": now_iso if version_id in notified_version_ids else previous.get("notified_at"),
            }

        all_state = self.load_state()
        all_state[state_key] = {
            "models": merged_models,
            "versions": merged_versions,
            "updatedAt": now_iso,
            "lastCron": "local-loop",
        }
        self.save_state(all_state)

        return {
            "ok": True,
            "new_count": len(notified_version_ids),
            "total_count": len(current_records),
            "initialized": False,
        }

    def pick_first_run_record(self, current_records):
        if not current_records:
            return None

        now_ms = int(time.time() * 1000)
        cutoff = now_ms - FIRST_RUN_NOTIFY_HOURS * 60 * 60 * 1000
        recent_records = []

        for record in current_records:
            published_at_ms = safe_parse_time(record.get("latest_version_created_at"))
            if published_at_ms is not None and published_at_ms >= cutoff:
                recent_records.append(record)

        if not recent_records:
            return None

        recent_records.sort(
            key=lambda r: (
                -(safe_parse_time(r.get("latest_version_created_at")) or 0),
                -int(r.get("id") or 0),
            )
        )
        return recent_records[0]

    def fetch_all_models(self, username, api_key):
        all_items = []
        next_url = build_models_url(username, api_key)

        while next_url:
            try:
                resp = self.session.get(next_url, timeout=60)
            except Exception as e:
                self.log(f"Civitai API request failed: {e}")
                return None

            if not resp.ok:
                self.log(f"Civitai API failed: {resp.status_code} {safe_read_text_response(resp)}")
                return None

            try:
                data = resp.json()
            except Exception as e:
                self.log(f"Invalid JSON from Civitai API: {e}")
                return None

            items = data.get("items") if isinstance(data, dict) else []
            all_items.extend(items if isinstance(items, list) else [])

            next_page = data.get("metadata", {}).get("nextPage") if isinstance(data, dict) else None
            next_url = patch_next_page_url(next_page, api_key) if next_page else None

        return all_items

    def load_state(self):
        if not STATE_PATH.exists():
            return {}
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, data):
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Civitai Notifier")
        self.root.geometry("900x700")

        self.username_var = StringVar()
        self.webhook_var = StringVar()
        self.api_key_var = StringVar()
        self.proxy_var = StringVar()
        self.interval_var = StringVar(value="300")
        self.auto_start_var = BooleanVar(value=False)

        self.status_var = StringVar(value="Stopped")
        self.last_run_var = StringVar(value="-")
        self.next_run_var = StringVar(value="-")
        self.total_models_var = StringVar(value="0")
        self.last_new_var = StringVar(value="0")

        self.core = NotifierCore(self.thread_safe_log)
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.is_running = False

        self.build_ui()
        self.load_config()
        if self.auto_start_var.get():
            self.start_loop()

    def build_ui(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        config_frame = ttk.LabelFrame(frm, text="配置", padding=10)
        config_frame.pack(fill="x")

        self.add_labeled_entry(config_frame, "Civitai 用户名", self.username_var, 0)
        self.add_labeled_entry(config_frame, "Discord Webhook", self.webhook_var, 1, width=80)
        self.add_labeled_entry(config_frame, "Civitai API Key", self.api_key_var, 2, show="*")
        self.add_labeled_entry(config_frame, "代理 URL（可选）", self.proxy_var, 3)
        self.add_labeled_entry(config_frame, "轮询间隔（秒）", self.interval_var, 4)

        ttk.Checkbutton(config_frame, text="启动时自动开始", variable=self.auto_start_var).grid(row=5, column=1, sticky="w", pady=(6, 0))

        button_frame = ttk.Frame(frm)
        button_frame.pack(fill="x", pady=10)
        ttk.Button(button_frame, text="保存配置", command=self.save_config).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="立即执行一次", command=self.run_once_async).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="开始循环", command=self.start_loop).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="停止", command=self.stop_loop).pack(side="left", padx=(0, 8))

        status_frame = ttk.LabelFrame(frm, text="状态", padding=10)
        status_frame.pack(fill="x")

        self.add_status_row(status_frame, "当前状态", self.status_var, 0)
        self.add_status_row(status_frame, "上次运行", self.last_run_var, 1)
        self.add_status_row(status_frame, "下次运行", self.next_run_var, 2)
        self.add_status_row(status_frame, "最近一次发现新版本数", self.last_new_var, 3)
        self.add_status_row(status_frame, "当前抓取模型数", self.total_models_var, 4)

        log_frame = ttk.LabelFrame(frm, text="日志", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = ScrolledText(log_frame, wrap="word", height=25)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def add_labeled_entry(self, parent, label, var, row, width=50, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        entry = ttk.Entry(parent, textvariable=var, width=width, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)

    def add_status_row(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="w", pady=2)

    def load_config(self):
        if not CONFIG_PATH.exists():
            self.create_default_config_file()

        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.thread_safe_log(f"配置文件读取失败：{CONFIG_PATH}")
            return

        self.username_var.set(cfg.get("username") or cfg.get("CIVITAI_USERNAME", ""))
        self.webhook_var.set(cfg.get("webhook") or cfg.get("DISCORD_WEBHOOK_URL", ""))
        self.api_key_var.set(cfg.get("api_key") or cfg.get("CIVITAI_API_KEY", ""))
        self.proxy_var.set(cfg.get("proxy_url", ""))
        self.interval_var.set(str(cfg.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)))
        self.auto_start_var.set(bool(cfg.get("auto_start", False)))
        self.thread_safe_log(f"已加载配置文件：{CONFIG_PATH}")

    def create_default_config_file(self):
        default_cfg = {
            "username": "",
            "webhook": "",
            "api_key": "",
            "proxy_url": "",
            "interval_seconds": DEFAULT_INTERVAL_SECONDS,
            "auto_start": False,
            "_comment": "可直接编辑此文件来配置 Civitai 用户名、Discord Webhook、API Key。",
        }
        CONFIG_PATH.write_text(json.dumps(default_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.thread_safe_log(f"已创建默认配置文件：{CONFIG_PATH}")

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
            "_comment": "可直接编辑此文件来配置 Civitai 用户名、Discord Webhook、API Key。",
        }
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        self.thread_safe_log(f"配置已保存到 {CONFIG_PATH}")

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

    def run_once_async(self):
        def target():
            self.execute_once(manual=True)
        threading.Thread(target=target, daemon=True).start()

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

    def set_status(self, value):
        self.root.after(0, lambda: self.status_var.set(value))

    def thread_safe_log(self, msg):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}\n"

        def append():
            self.log_text.configure(state="normal")
            self.log_text.insert(END, line)
            self.log_text.see(END)
            self.log_text.configure(state="disabled")

        self.root.after(0, append)


def build_models_url(username, api_key):
    from urllib.parse import urlencode

    params = {
        "username": username,
        "limit": "100",
        "sort": "Newest",
        "nsfw": "true",
    }
    if api_key:
        params["token"] = api_key
    return f"https://civitai.com/api/v1/models?{urlencode(params)}"


def patch_next_page_url(next_page_url, api_key):
    from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

    parts = urlparse(next_page_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["nsfw"] = "true"
    if api_key:
        query["token"] = api_key
    else:
        query.pop("token", None)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(query), parts.fragment))


def safe_read_text_response(resp):
    try:
        return resp.text
    except Exception:
        return ""


def safe_parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def pick_published_time(model, latest_version):
    return (
        (latest_version or {}).get("publishedAt")
        or (latest_version or {}).get("createdAt")
        or (model or {}).get("publishedAt")
        or (model or {}).get("createdAt")
        or None
    )


def pick_latest_version(model):
    versions = model.get("modelVersions") if isinstance(model, dict) else []
    versions = versions if isinstance(versions, list) else []
    if not versions:
        return None

    def sort_key(v):
        t = safe_parse_time(v.get("publishedAt")) or safe_parse_time(v.get("createdAt")) or 0
        vid = int(v.get("id") or 0)
        return (t, vid)

    return sorted(versions, key=sort_key, reverse=True)[0]


def extract_preview_image(version):
    images = version.get("images") if isinstance(version, dict) else []
    images = images if isinstance(images, list) else []
    for image in images:
        if isinstance(image, dict) and image.get("url"):
            return image["url"]
    return None


def build_model_record(model, fallback_username):
    latest_version = pick_latest_version(model)
    creator = model.get("creator") if isinstance(model, dict) else {}
    creator = creator if isinstance(creator, dict) else {}
    published_at = pick_published_time(model, latest_version)
    mid = int(model.get("id") or 0) if isinstance(model, dict) else 0

    return {
        "id": mid,
        "name": model.get("name", "Untitled Model") if isinstance(model, dict) else "Untitled Model",
        "type": model.get("type", "Unknown") if isinstance(model, dict) else "Unknown",
        "creator": creator.get("username") or fallback_username,
        "model_url": f"https://civitai.com/models/{mid}",
        "latest_version_id": latest_version.get("id") if isinstance(latest_version, dict) else None,
        "latest_version_name": latest_version.get("name", "Unknown") if isinstance(latest_version, dict) else "Unknown",
        "latest_version_created_at": published_at,
        "preview_image": extract_preview_image(latest_version),
    }


def format_display_time(value):
    return value or "Unknown"


def format_discord_payload(record):
    display_time = format_display_time(record.get("latest_version_created_at"))
    content = (
        "📢 **Civitai 新模型/新版本发布通知 | New Model / Version Release Notification**\n\n"
        f"**作者 | Creator:** {record.get('creator')}\n"
        f"**模型名称 | Model Name:** {record.get('name')}\n"
        f"**模型类型 | Model Type:** {record.get('type')}\n"
        f"**最新版本 | Latest Version:** {record.get('latest_version_name')}\n"
        f"**发布时间 | Published At:** {display_time}\n"
        f"**模型链接 | Model Link:** {record.get('model_url')}"
    )

    payload = {"content": content}
    preview_image = record.get("preview_image")
    if preview_image:
        title = record.get("name") or "Untitled"
        if len(title) > 100:
            title = title[:97] + "..."
        payload["embeds"] = [
            {
                "title": title,
                "url": record.get("model_url"),
                "image": {"url": preview_image},
                "fields": [
                    {"name": "作者 | Creator", "value": str(record.get("creator")), "inline": True},
                    {"name": "类型 | Type", "value": str(record.get("type")), "inline": True},
                    {"name": "版本 | Version", "value": str(record.get("latest_version_name")), "inline": False},
                    {"name": "发布时间 | Published At", "value": str(display_time), "inline": False},
                ],
                "footer": {"text": "Civitai 自动通知 | Automatic Notification"},
            }
        ]
    return payload


if __name__ == "__main__":
    root = Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    app = App(root)
    root.mainloop()
