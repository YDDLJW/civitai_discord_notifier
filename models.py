from datetime import datetime


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


def build_models_url(username, api_key, limit=100):
    from urllib.parse import urlencode

    params = {
        "username": username,
        "limit": str(limit),
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
    return urlunparse(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(query),
            parts.fragment,
        )
    )


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