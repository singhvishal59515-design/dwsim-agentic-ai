"""Backup system: snapshot before save, list, rolling-prune, restore."""
import os
import time

from dwsim_bridge_v2 import list_backups, restore_backup, _BACKUP_KEEP


def test_save_creates_backup(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    # Small write so the file actually exists to back up.
    r = bridge.save_flowsheet(force=True)
    assert r["success"]
    # A backup should be present on subsequent saves.
    r2 = bridge.save_flowsheet(force=True)
    assert r2["success"]
    assert r2.get("backup"), "second save must produce a backup"
    assert os.path.exists(r2["backup"])


def test_list_backups_newest_first(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    bridge.save_flowsheet(force=True)
    time.sleep(1.1)  # backup filenames have 1-second resolution
    bridge.save_flowsheet(force=True)
    backups = list_backups(sample_flowsheet)
    assert len(backups) >= 1
    mtimes = [b["mtime"] for b in backups]
    assert mtimes == sorted(mtimes, reverse=True)


def test_backup_rolling_prune(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    for _ in range(_BACKUP_KEEP + 3):
        bridge.save_flowsheet(force=True)
        time.sleep(1.1)
    backups = list_backups(sample_flowsheet)
    assert len(backups) <= _BACKUP_KEEP, \
        f"expected ≤{_BACKUP_KEEP} backups, got {len(backups)}"


def test_restore_backup_roundtrip(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    bridge.save_flowsheet(force=True)
    time.sleep(1.1)
    r = bridge.save_flowsheet(force=True)
    assert r.get("backup"), "save must produce a backup path"
    backup_path = r["backup"]
    backup_size = os.path.getsize(backup_path)

    # Corrupt the live file.
    with open(sample_flowsheet, "wb") as f:
        f.write(b"CORRUPT")
    assert os.path.getsize(sample_flowsheet) < backup_size

    # Restore and confirm content matches backup exactly.
    # Do NOT compare against before_size: .dwxmz is a zip and DWSIM may produce
    # slightly different byte-counts on each re-save (non-deterministic timestamps
    # in zip metadata). The correct check is that the restored file matches the
    # backup file, not the re-saved live file.
    res = restore_backup(backup_path, sample_flowsheet)
    assert res["success"], f"restore failed: {res.get('error')}"
    restored_size = os.path.getsize(sample_flowsheet)
    assert restored_size == backup_size, (
        f"Restored file size {restored_size} != backup size {backup_size}. "
        "The restore copied a different file than the backup."
    )
    # File must no longer be the corrupt stub.
    with open(sample_flowsheet, "rb") as f:
        header = f.read(4)
    assert header != b"CORR", "Restored file still appears corrupted"
