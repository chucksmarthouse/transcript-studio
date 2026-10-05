"""tools/release_check.sh must catch what must never ship, and pass a clean tree."""

import subprocess

import pytest

from tests.test_export import ROOT

SCRIPT = ROOT / "tools" / "release_check.sh"


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "LICENSE").write_text("GPL\n")
    (tmp_path / "NOTICE").write_text("credits\n")
    (tmp_path / "app.py").write_text("print('hi')\n")
    return tmp_path


def check(tree):
    return subprocess.run(["bash", str(SCRIPT), str(tree)], capture_output=True, text=True)


def test_clean_tree_passes(tree):
    r = check(tree)
    assert r.returncode == 0, r.stdout + r.stderr


def test_personal_path_in_code_fails(tree):
    (tree / "x.py").write_text("p = '/home/chuck/x'\n")
    r = check(tree)
    assert r.returncode != 0 and "FAIL" in r.stdout and "x.py" in r.stdout


def test_personal_path_in_json_sample_fails(tree):
    (tree / "samples").mkdir()
    (tree / "samples" / "p.tsproj.json").write_text('{"audio_path": "/home/chuck/a.mp3"}\n')
    r = check(tree)
    assert r.returncode != 0 and "p.tsproj.json" in r.stdout


def test_removed_sample_name_fails(tree):
    (tree / "phone-2026-x.m4a").write_bytes(b"x")
    r = check(tree)
    assert r.returncode != 0 and "phone-2026-x.m4a" in r.stdout


def test_oversized_file_fails(tree):
    with open(tree / "big.bin", "wb") as f:
        f.truncate(21 * 1024 * 1024)
    r = check(tree)
    assert r.returncode != 0 and "big.bin" in r.stdout


def test_missing_license_fails(tree):
    (tree / "LICENSE").unlink()
    r = check(tree)
    assert r.returncode != 0 and "LICENSE" in r.stdout


def test_in_a_git_repo_only_committable_files_count(tree):
    def git(*a):
        subprocess.run(["git", "-C", str(tree), *a], check=True, capture_output=True)

    git("init", "-q")
    (tree / ".gitignore").write_text("*.ts.flac\n")
    (tree / "clip.ts.flac").write_bytes(b"x")  # ignored playback copy: would never be committed
    assert check(tree).returncode == 0
    (tree / "clip.boosted.flac").write_bytes(b"x")  # not ignored: would be committed
    r = check(tree)
    assert r.returncode != 0 and "clip.boosted.flac" in r.stdout


@pytest.mark.parametrize("text", ["Jaza", "Talon", "Edmonton", "cwduby", "/home/someone/audio.mp3"])
def test_other_personal_strings_fail(tree, text):
    (tree / "notes.md").write_text(f"see {text}\n")
    r = check(tree)
    assert r.returncode != 0 and "notes.md" in r.stdout
