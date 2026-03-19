import time

import requests

from config import FIRST_RUN_NOTIFY_HOURS, NOTIFY_WINDOW_HOURS, load_state, save_state, safe_now_iso
from models import (
    build_model_record,
    build_models_url,
    format_discord_payload,
    patch_next_page_url,
    safe_parse_time,
    safe_read_text_response,
)


class NotifierCore:
    def __init__(self, logger):
        self.logger = logger
        self.session = requests.Session()

    def log(self, msg):
        self.logger(msg)

    def fetch_all_models(self, username, api_key, limit=100):
        all_items = []
        next_url = build_models_url(username, api_key, limit=limit)

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

            if limit != 100:
                break

            next_page = data.get("metadata", {}).get("nextPage") if isinstance(data, dict) else None
            next_url = patch_next_page_url(next_page, api_key) if next_page else None

        return all_items

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

    def run_once(self, username, webhook, api_key="", proxy_url=""):
        if not username:
            self.log("Missing CIVITAI_USERNAME")
            return {
                "ok": False,
                "new_count": 0,
                "total_count": 0,
                "initialized": False,
                "first_run_notified_record": None,
                "notified_records": [],
            }

        if not webhook:
            self.log("Missing DISCORD_WEBHOOK_URL")
            return {
                "ok": False,
                "new_count": 0,
                "total_count": 0,
                "initialized": False,
                "first_run_notified_record": None,
                "notified_records": [],
            }

        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        else:
            self.session.proxies.clear()

        state_key = f"models:{username}"
        saved = load_state().get(state_key)
        history = saved if isinstance(saved, dict) else {"models": {}, "versions": {}}

        if not isinstance(history.get("models"), dict):
            history["models"] = {}
        if not isinstance(history.get("versions"), dict):
            history["versions"] = {}

        is_first_run = not history["models"] and not history["versions"]
        limit = 100 if is_first_run else 10

        items = self.fetch_all_models(username, api_key, limit=limit)
        if items is None:
            return {
                "ok": False,
                "new_count": 0,
                "total_count": 0,
                "initialized": False,
                "first_run_notified_record": None,
                "notified_records": [],
            }

        current_records = []
        for model in items:
            record = build_model_record(model, username)
            if record and record.get("id") and record.get("latest_version_id"):
                current_records.append(record)

        if is_first_run:
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
                            f"First run notification failed for model {latest_recent_record['id']}, version {latest_recent_record['latest_version_id']}: "
                            f"{resp.status_code} {safe_read_text_response(resp)}"
                        )
                    else:
                        version_id = str(latest_recent_record["latest_version_id"])
                        notified_version_ids.add(version_id)
                        init_versions[version_id]["notified_at"] = safe_now_iso()
                        self.log(
                            f"First run: notified latest version within {FIRST_RUN_NOTIFY_HOURS}h: "
                            f"{latest_recent_record['name']} ({latest_recent_record['id']}) "
                            f"v={latest_recent_record['latest_version_name']} versionId={latest_recent_record['latest_version_id']}"
                        )
            else:
                self.log(f"First run: no version found within {FIRST_RUN_NOTIFY_HOURS}h, state initialized only.")

            all_state = load_state()
            all_state[state_key] = {
                "models": init_models,
                "versions": init_versions,
                "initializedAt": safe_now_iso(),
            }
            save_state(all_state)

            return {
                "ok": True,
                "new_count": len(notified_version_ids),
                "total_count": len(current_records),
                "initialized": True,
                "first_run_notified_record": latest_recent_record if notified_version_ids else None,
                "notified_records": [latest_recent_record] if notified_version_ids else [],
            }

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
        notified_records = []

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
                    f"Discord webhook failed for model {record['id']}, version {record['latest_version_id']}: "
                    f"{resp.status_code} {safe_read_text_response(resp)}"
                )
            else:
                notified_version_ids.add(str(record["latest_version_id"]))
                notified_records.append(record)
                self.log(
                    f"Notified model/version: {record['name']} ({record['id']}) "
                    f"v={record['latest_version_name']} versionId={record['latest_version_id']}"
                )

        merged_models = dict(history.get("models", {}))
        merged_versions = dict(history.get("versions", {}))
        now_iso = safe_now_iso()

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

        all_state = load_state()
        all_state[state_key] = {
            "models": merged_models,
            "versions": merged_versions,
            "updatedAt": now_iso,
            "lastCron": "local-loop",
        }
        save_state(all_state)

        return {
            "ok": True,
            "new_count": len(notified_version_ids),
            "total_count": len(current_records),
            "initialized": False,
            "first_run_notified_record": None,
            "notified_records": notified_records,
        }