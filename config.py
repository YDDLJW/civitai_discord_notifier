import json
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "civitai_notifier_config.json"
STATE_PATH = APP_DIR / "civitai_model_state.json"
LOG_PATH = APP_DIR / "civitai_notifier.log"

DEFAULT_INTERVAL_SECONDS = 300
NOTIFY_WINDOW_HOURS = 48
FIRST_RUN_NOTIFY_HOURS = 24
DEFAULT_ICON_NAME = "app.ico"


def safe_now_iso():
    return datetime.now().isoformat(timespec="seconds")


def ensure_default_config():
    if CONFIG_PATH.exists():
        return
    cfg = {
        "username": "",
        "webhook": "",
        "api_key": "",
        "proxy_url": "",
        "interval_seconds": DEFAULT_INTERVAL_SECONDS,
        "auto_start": False,
        "start_hidden_to_tray": False,
        "icon_path": DEFAULT_ICON_NAME,
    }
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config_file():
    ensure_default_config()
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config_file(data):
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(data):
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(line):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def resolve_icon_path(icon_path_value):
    raw = (icon_path_value or "").strip() or DEFAULT_ICON_NAME
    path = Path(raw)
    if not path.is_absolute():
        path = APP_DIR / path
    return path