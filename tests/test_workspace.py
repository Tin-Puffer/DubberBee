"""Configurable workspace (job caches) and logs folders — and cleaning stays inside ours."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from dubber.core.utils import paths
from dubber.core.utils.settings import load_settings, save_settings


def _set(**kw):
    st = load_settings(); st.update(kw); save_settings(st)


def test_default_and_custom_roots(tmp_path):
    assert paths.jobs_root() == paths.default_jobs_root()
    assert paths.logs_root() == paths.default_logs_root()
    _set(workspace_dir=str(tmp_path / "Desk"), logs_dir=str(tmp_path / "L"))
    assert paths.jobs_root() == tmp_path / "Desk" / "Dubber workspace" / "jobs"
    assert paths.logs_root() == tmp_path / "L" / "Dubber logs"
    v = tmp_path / "v.mp4"; v.write_bytes(b"x")
    assert paths.job_dir_for(v).parent == paths.jobs_root()
    from dubber.core.utils.error_report import errors_dir
    assert errors_dir() == tmp_path / "L" / "Dubber logs" / "errors"


def test_cleaning_never_touches_other_files(tmp_path):
    """Even if the user's own folders end up next to job dirs, only job dirs go."""
    _set(workspace_dir=str(tmp_path / "Desk"))
    root = paths.jobs_root()
    job = root / "0123456789ab"; job.mkdir(parents=True); (job / "asr.json").write_text("[]")
    mine = root / "Ảnh của tôi"; mine.mkdir(); (mine / "a.jpg").write_bytes(b"jpg")
    (root / "note.txt").write_text("keep")
    (tmp_path / "Desk" / "other.docx").write_text("keep")
    old = root / "abcdef012345"; old.mkdir(); (old / "input.json").write_text("{}")
    os.utime(old / "input.json", (1000, 1000))
    paths.clear_jobs()
    assert not old.exists()
    assert not job.exists()
    assert (mine / "a.jpg").exists() and (root / "note.txt").exists()
    assert (tmp_path / "Desk" / "other.docx").exists()


def test_move_jobs(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "0123456789ab").mkdir(parents=True); (old / "0123456789ab" / "asr.json").write_text("[]")
    (old / "keep-me").mkdir()
    assert paths.move_jobs(old, new) == 1
    assert (new / "0123456789ab" / "asr.json").exists()
    assert (old / "keep-me").exists() and not (old / "0123456789ab").exists()


# ── dialog ────────────────────────────────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def dlg_cls():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app import workspace_dialog
    return workspace_dialog


def test_dialog_change_workspace_moves_jobs(tmp_path, monkeypatch, dlg_cls):
    import dubber.core.utils.paths as p
    monkeypatch.setattr(p, "_app_data_dir", lambda: tmp_path / "default")
    job = p.default_jobs_root() / "0123456789ab"; job.mkdir(parents=True)
    (job / "asr.json").write_text("[]")
    monkeypatch.setattr(dlg_cls.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path / "Desk"))
    monkeypatch.setattr(dlg_cls.QMessageBox, "question", lambda *a, **k: dlg_cls.QMessageBox.Yes)
    d = dlg_cls.WorkspaceDialog()
    assert d._edits["workspace"].text() == str(p.default_jobs_root())
    d._change("workspace", "workspace_dir")
    new_root = tmp_path / "Desk" / "Dubber workspace" / "jobs"
    assert d._edits["workspace"].text() == str(new_root)
    assert (new_root / "0123456789ab" / "asr.json").exists() and not job.exists()
    d._set("workspace", "workspace_dir", None)                # "Mặc định"
    assert p.jobs_root() == p.default_jobs_root()


def test_dialog_change_logs_swaps_log_file(tmp_path, monkeypatch, dlg_cls):
    calls = []
    monkeypatch.setattr(dlg_cls.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path / "L"))
    d = dlg_cls.WorkspaceDialog(on_logs_changed=lambda: calls.append(1))
    d._change("logs", "logs_dir")
    assert calls == [1] and paths.logs_root() == tmp_path / "L" / "Dubber logs"
    assert paths.logs_root().is_dir()


def test_dialog_busy_disables_changes_and_open(tmp_path, monkeypatch, dlg_cls):
    from PySide6.QtWidgets import QPushButton
    opened = []
    monkeypatch.setattr(dlg_cls, "open_folder", lambda p: opened.append(p))
    d = dlg_cls.WorkspaceDialog(busy=True)
    btns = {b.text(): b for b in d.findChildren(QPushButton) if b.text() in ("Đổi…", "Mặc định")}
    assert btns and not any(b.isEnabled() for b in btns.values())
    opener = next(b for b in d.findChildren(QPushButton) if b.text() == "Báo cáo lỗi")
    opener.click()
    assert opened == [paths.logs_root() / "errors"]
