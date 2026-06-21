from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3


DB_PATH = Path("data/ledger.db")
SYNC_STATE_PATH = Path("data/s3_sync_state.json")
LOCAL_BACKUP_DIR = Path("data/backups")


class CloudSyncError(Exception):
    pass


def get_config(secrets):
    required_keys = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "S3_BUCKET",
        "S3_DB_KEY",
    ]
    missing = [key for key in required_keys if not secrets.get(key)]
    if missing:
        raise CloudSyncError(f"Missing Streamlit secrets: {', '.join(missing)}")

    return {
        "aws_access_key_id": secrets["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": secrets["AWS_SECRET_ACCESS_KEY"],
        "region_name": secrets["AWS_DEFAULT_REGION"],
        "bucket": secrets["S3_BUCKET"],
        "db_key": secrets["S3_DB_KEY"],
        "backup_prefix": secrets.get("S3_BACKUP_PREFIX", "accounting/backups/"),
    }


def get_s3_client(config):
    try:
        import boto3
    except ImportError as exc:
        raise CloudSyncError("boto3 is required for S3 sync. Install dependencies from requirements.txt.") from exc

    return boto3.client(
        "s3",
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region_name"],
    )


def utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def format_remote_info(head_response):
    return {
        "etag": (head_response.get("ETag") or "").strip('"'),
        "last_modified": head_response.get("LastModified").isoformat()
        if head_response.get("LastModified")
        else None,
        "size": head_response.get("ContentLength", 0),
    }


def load_sync_state():
    if not SYNC_STATE_PATH.exists():
        return {}
    return json.loads(SYNC_STATE_PATH.read_text())


def save_sync_state(state):
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def local_db_info():
    if not DB_PATH.exists():
        return None
    stat = DB_PATH.stat()
    return {
        "path": str(DB_PATH),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def get_remote_db_info(config):
    s3 = get_s3_client(config)
    try:
        response = s3.head_object(Bucket=config["bucket"], Key=config["db_key"])
    except Exception as exc:
        raise CloudSyncError(f"Could not read remote database metadata: {exc}") from exc
    return format_remote_info(response)


def sqlite_integrity_check(path=DB_PATH):
    if not path.exists():
        raise CloudSyncError(f"SQLite database not found: {path}")

    with sqlite3.connect(path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    return result and result[0] == "ok"


def backup_local_db_to_disk():
    if not DB_PATH.exists():
        raise CloudSyncError("Local database does not exist.")

    LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = LOCAL_BACKUP_DIR / f"ledger-{utc_timestamp()}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def remote_backup_key(config):
    prefix = config["backup_prefix"].rstrip("/")
    return f"{prefix}/ledger-{utc_timestamp()}.db"


def backup_local_db_to_s3(config):
    if not DB_PATH.exists():
        raise CloudSyncError("Local database does not exist.")

    s3 = get_s3_client(config)
    key = remote_backup_key(config)
    s3.upload_file(str(DB_PATH), config["bucket"], key)
    return key


def download_db_from_s3(config):
    backup_path = backup_local_db_to_disk() if DB_PATH.exists() else None
    s3 = get_s3_client(config)
    s3.download_file(config["bucket"], config["db_key"], str(DB_PATH))

    if not sqlite_integrity_check(DB_PATH):
        if backup_path:
            shutil.copy2(backup_path, DB_PATH)
        raise CloudSyncError("Downloaded database failed SQLite integrity check. Restored local backup.")

    remote_info = get_remote_db_info(config)
    state = {
        "bucket": config["bucket"],
        "key": config["db_key"],
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "remote_info": remote_info,
    }
    save_sync_state(state)
    return backup_path, remote_info


def upload_db_to_s3(config):
    if not sqlite_integrity_check(DB_PATH):
        raise CloudSyncError("Local database failed SQLite integrity check. Upload cancelled.")

    backup_key = backup_local_db_to_s3(config)
    s3 = get_s3_client(config)
    s3.upload_file(str(DB_PATH), config["bucket"], config["db_key"])
    remote_info = get_remote_db_info(config)
    state = {
        "bucket": config["bucket"],
        "key": config["db_key"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "remote_info": remote_info,
        "backup_key": backup_key,
    }
    save_sync_state(state)
    return backup_key, remote_info
