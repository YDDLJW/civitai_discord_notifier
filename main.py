import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
CIVITAI_USERNAME = os.getenv("CIVITAI_USERNAME", "").strip()
CIVITAI_API_KEY = os.getenv("CIVITAI_API_KEY", "").strip()
CHECK_LIMIT = int(os.getenv("CHECK_LIMIT", "100"))

STATE_FILE = Path("state.json")
API_BASE = "https://civitai.com/api/v1"


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("models", {})
                return data
        except Exception:
            pass
    return {"models": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if CIVITAI_API_KEY:
        headers["Authorization"] = f"Bearer {CIVITAI_API_KEY}"
    return headers


def get_latest_models() -> list[dict[str, Any]]:
    url = f"{API_BASE}/models"
    params = {
        "username": CIVITAI_USERNAME,
        "sort": "Newest",
        "limit": CHECK_LIMIT,
    }

    resp = requests.get(url, params=params, headers=get_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def build_model_url(model_id: int) -> str:
    return f"https://civitai.com/models/{model_id}"


def pick_latest_version(model: dict[str, Any]) -> dict[str, Any] | None:
    versions = model.get("modelVersions", [])
    if not isinstance(versions, list) or not versions:
        return None

    def sort_key(v: dict[str, Any]) -> tuple[str, int]:
        created_at = str(v.get("createdAt", ""))
        version_id = int(v.get("id", 0) or 0)
        return created_at, version_id

    return sorted(versions, key=sort_key, reverse=True)[0]


def extract_preview_image(version: dict[str, Any] | None) -> str | None:
    if not version:
        return None
    images = version.get("images", [])
    if not isinstance(images, list):
        return None
    for image in images:
        url = image.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def build_model_record(model: dict[str, Any]) -> dict[str, Any]:
    model_id = int(model.get("id", 0) or 0)
    latest_version = pick_latest_version(model)

    latest_version_id = None
    latest_version_name = None
    latest_version_time = None

    if latest_version:
        raw_version_id = latest_version.get("id")
        if raw_version_id is not None:
            latest_version_id = int(raw_version_id)
        latest_version_name = latest_version.get("name")
        latest_version_time = latest_version.get("createdAt")

    stats = model.get("stats", {})
    creator = model.get("creator", {}) if isinstance(model.get("creator"), dict) else {}

    return {
        "id": model_id,
        "name": model.get("name", "Untitled Model"),
        "type": model.get("type", "Unknown"),
        "creator": creator.get("username", CIVITAI_USERNAME),
        "model_url": build_model_url(model_id),
        "download_count": int(stats.get("downloadCount", 0) or 0),
        "favorite_count": int(stats.get("favoriteCount", 0) or 0),
        "latest_version_id": latest_version_id,
        "latest_version_name": latest_version_name or "Unknown",
        "latest_version_created_at": latest_version_time or "Unknown",
        "updated_at": model.get("updatedAt", ""),
        "published_at": model.get("publishedAt", ""),
        "preview_image": extract_preview_image(latest_version),
    }


def format_discord_payload(model_record: dict[str, Any]) -> dict[str, Any]:
    model_name = model_record["name"]
    creator = model_record["creator"]
    model_type = model_record["type"]
    latest_version_name = model_record["latest_version_name"]
    latest_version_time = model_record["latest_version_created_at"]
    download_count = model_record["download_count"]
    favorite_count = model_record["favorite_count"]
    model_url = model_record["model_url"]
    preview_image = model_record["preview_image"]

    content = (
        f"📢 **Civitai 新模型发布通知 | New Model Release Notification**\n\n"
        f"**作者 | Creator:** {creator}\n"
        f"**模型名称 | Model Name:** {model_name}\n"
        f"**模型类型 | Model Type:** {model_type}\n"
        f"**最新版本 | Latest Version:** {latest_version_name}\n"
        f"**发布时间 | Published At:** {latest_version_time}\n"
        f"**下载次数 | Downloads:** {download_count}\n"
        f"**收藏次数 | Favorites:** {favorite_count}\n"
        f"**模型链接 | Model Link:** {model_url}"
    )

    payload: dict[str, Any] = {
        "content": content
    }

    if preview_image:
        payload["embeds"] = [
            {
                "title": model_name,
                "url": model_url,
                "image": {"url": preview_image},
                "fields": [
                    {"name": "作者 | Creator", "value": str(creator), "inline": True},
                    {"name": "类型 | Type", "value": str(model_type), "inline": True},
                    {"name": "版本 | Version", "value": str(latest_version_name), "inline": False},
                    {"name": "发布时间 | Published At", "value": str(latest_version_time), "inline": False},
                    {"name": "下载次数 | Downloads", "value": str(download_count), "inline": True},
                    {"name": "收藏次数 | Favorites", "value": str(favorite_count), "inline": True},
                ],
                "footer": {"text": "Civitai 自动通知 | Automatic Notification"}
            }
        ]

    return payload


def post_to_discord(payload: dict[str, Any]) -> None:
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    resp.raise_for_status()


def main() -> None:
    if not DISCORD_WEBHOOK_URL:
        raise ValueError("缺少 DISCORD_WEBHOOK_URL")
    if not CIVITAI_USERNAME:
        raise ValueError("缺少 CIVITAI_USERNAME")

    state = load_state()
    history: dict[str, Any] = state.get("models", {})

    models = get_latest_models()
    if not models:
        print("没有获取到模型")
        return

    current_records = []
    for model in models:
        model_id = model.get("id")
        if model_id is None:
            continue
        record = build_model_record(model)
        if record["id"] > 0:
            current_records.append(record)

    if not history:
        for record in current_records:
            history[str(record["id"])] = record
        state["models"] = history
        save_state(state)
        print("首次运行：已记录当前模型历史，不发送历史通知。")
        return

    new_records = []
    for record in current_records:
        model_id_str = str(record["id"])
        if model_id_str not in history:
            new_records.append(record)

    new_records.sort(key=lambda x: x["id"])

    for record in new_records:
        payload = format_discord_payload(record)
        post_to_discord(payload)
        print(f"已发送通知: {record['name']} ({record['id']})")

    for record in current_records:
        history[str(record["id"])] = record

    state["models"] = history
    save_state(state)


if __name__ == "__main__":
    main()