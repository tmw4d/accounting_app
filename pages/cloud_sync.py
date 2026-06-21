import streamlit as st

import cloud_sync


def _format_bytes(size):
    if size is None:
        return "N/A"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"


def _load_config():
    try:
        return cloud_sync.get_config(st.secrets)
    except cloud_sync.CloudSyncError as exc:
        st.error(str(exc))
        st.info("Create `.streamlit/secrets.toml` from `.streamlit/secrets.example.toml`, or add the same TOML values to Streamlit Community Cloud secrets.")
        return None


def render():
    st.title("Cloud Sync")
    st.write("Manually sync the local SQLite database with an S3 copy. Use this as backup/sync storage, not as a live multi-user database.")

    config = _load_config()
    if not config:
        return

    st.write("### Configuration")
    c1, c2, c3 = st.columns(3)
    c1.metric("Bucket", config["bucket"])
    c2.metric("Database Key", config["db_key"])
    c3.metric("Backup Prefix", config["backup_prefix"])

    st.write("### Database Status")
    local_info = cloud_sync.local_db_info()
    remote_info = None
    remote_error = None

    try:
        remote_info = cloud_sync.get_remote_db_info(config)
    except cloud_sync.CloudSyncError as exc:
        remote_error = str(exc)

    local_col, remote_col = st.columns(2)
    with local_col:
        st.write("#### Local")
        if local_info:
            st.metric("Size", _format_bytes(local_info["size"]))
            st.write(f"Modified: `{local_info['modified']}`")
            try:
                is_ok = cloud_sync.sqlite_integrity_check()
                st.success("SQLite integrity check passed." if is_ok else "SQLite integrity check failed.")
            except cloud_sync.CloudSyncError as exc:
                st.error(str(exc))
        else:
            st.warning("No local database found at `data/ledger.db`.")

    with remote_col:
        st.write("#### S3")
        if remote_info:
            st.metric("Size", _format_bytes(remote_info["size"]))
            st.write(f"Last modified: `{remote_info['last_modified']}`")
            st.write(f"ETag: `{remote_info['etag']}`")
        else:
            st.warning(remote_error or "Remote database metadata unavailable.")

    with st.expander("Last Sync State", expanded=False):
        state = cloud_sync.load_sync_state()
        if state:
            st.json(state)
        else:
            st.info("No local sync state recorded yet.")

    st.divider()
    st.write("### Actions")
    st.warning("Download replaces the local database after making a local backup. Upload creates an S3 backup before replacing the main S3 database object.")

    a1, a2, a3 = st.columns(3)

    if a1.button("Create Local Backup", width="stretch"):
        try:
            backup_path = cloud_sync.backup_local_db_to_disk()
            st.success(f"Created local backup: `{backup_path}`")
        except cloud_sync.CloudSyncError as exc:
            st.error(str(exc))

    if a2.button("Download From S3", width="stretch"):
        try:
            backup_path, new_remote_info = cloud_sync.download_db_from_s3(config)
            if backup_path:
                st.success(f"Downloaded S3 database. Local backup created first: `{backup_path}`")
            else:
                st.success("Downloaded S3 database.")
            st.json(new_remote_info)
            st.rerun()
        except cloud_sync.CloudSyncError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Download failed: {exc}")

    if a3.button("Upload To S3", width="stretch"):
        try:
            backup_key, new_remote_info = cloud_sync.upload_db_to_s3(config)
            st.success(f"Uploaded local database to S3. Backup created first: `s3://{config['bucket']}/{backup_key}`")
            st.json(new_remote_info)
            st.rerun()
        except cloud_sync.CloudSyncError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Upload failed: {exc}")
