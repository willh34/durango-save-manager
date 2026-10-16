import os
import shutil
import hashlib


def export_manifest(manifest, target_dir, export_azimuth=True, azimuth_user_id="User_9000000000000"):
    """
    Moves downloaded blobs from the manifest into their final layout.
    If export_azimuth is True, creates Azimuth-compatible revision directory structure and commit file.
    """
    from .storage import safe_component

    os.makedirs(target_dir, exist_ok=True)
    target_abs = os.path.abspath(target_dir)
    results = []

    for c in manifest:
        orig_name = c["name"]
        target_name = azimuth_user_id if (export_azimuth and orig_name.startswith("User_")) else orig_name
        container_dir = os.path.abspath(os.path.join(target_abs, safe_component(target_name, "container")))
        if os.path.commonpath([target_abs, container_dir]) != target_abs:
            raise RuntimeError(f"Refusing unsafe container name: {orig_name!r}")

        if export_azimuth:
            rev_dir = os.path.join(container_dir, "revisions", "0000000000000001")
            data_dir = os.path.join(rev_dir, "data")
        else:
            data_dir = container_dir
            rev_dir = None

        os.makedirs(data_dir, exist_ok=True)

        blobs = sorted(c["blobs"], key=lambda b: b["name"])
        commit_lines = ["Azimuth complete save revision v2", f"blobs {len(blobs)}"]

        container_size = 0
        for b in blobs:
            blob_path = os.path.join(data_dir, b["name"])
            size = os.path.getsize(b["path"])
            container_size += size
            if export_azimuth:
                h = hashlib.sha256()
                with open(b["path"], "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                commit_lines.append(f"{h.hexdigest()} {size} {b['name']}")
            shutil.move(b["path"], blob_path)

        if export_azimuth and rev_dir:
            with open(os.path.join(rev_dir, "commit"), "wb") as cf:
                cf.write(("\n".join(commit_lines) + "\n").encode("utf-8"))

        results.append({
            "name": orig_name,
            "target": target_name,
            "blob_count": len(blobs),
            "size_bytes": container_size
        })

    return results
