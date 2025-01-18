"""Unit tests."""

import ftplib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import ANY, call, patch

import pytest

from ftpsync import deleted_files, file_hash, folder_hashes, main, new_files

# ruff: noqa: S101 (use of assert detected), expected in test cases.

# Using the license file for this test, as it rarely changes.
# If it changes, recalculate using `shasum -a 256 LICENSE`.
LICENSE_HASH = "2f3a98ffc7e14d7476db1fcc4f2ed041c8d9050f1ced14355f0d653cd5d5d24c"

TEST_DATA_FOLDER = "test-data"
TEST_DATA_HASHES = {
    "./a": "a5b2de337a986e7b9c1d178b7ed171b6cf912d05ab69c4aeca4e7e7665bfacb2",
    "./b": "c5e1064872056c435c0aa2239a3c11bdbb32536615cef6a5a412fdb7e08c5f76",
    "./folder/c": "92b735e707afcfa9ac4ba9017a2a633f72d3f8901f309adf1052eeed375bb1b5",
    "./folder/sub/d": "6b5c8a22ec39c76fb504f5b377d43ed67008ef3407df29b111b5dcbb15a8c986",
}


def test_filehash() -> None:
    """Test that filehash returns the correct SHA256 hash."""
    testfile = Path(__file__).parent / "LICENSE"
    assert file_hash(testfile) == LICENSE_HASH


def test_filehash_raises() -> None:
    """Test that filehash with a nonexisting file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        file_hash("This file doesn't exist")


def test_folder_hashes() -> None:
    """Test that folder_hashes returns the correct result."""
    os.chdir(Path(__file__).parent / TEST_DATA_FOLDER)
    hashes = folder_hashes()
    assert hashes == TEST_DATA_HASHES


FAKE_DIR = {
    "./a": "1234",
    "./b": "4567",
    "./folder/c": "8901",
    "./folder/subfolder/d": "2345",
    "./folder/subfolder/e": "XYZ",
}


def test_new_files_unchanged_is_empty() -> None:
    """Test that new_files returns an empty list if new and old are the same."""
    assert len(new_files(FAKE_DIR, FAKE_DIR)) == 0


def test_new_files_ignores_deletions() -> None:
    """Test that new_files is unaffected by deletions."""
    assert len(new_files(new={}, old=FAKE_DIR)) == 0


def test_new_files_finds_added_files() -> None:
    """Test that new_files returns added files."""
    added_items = {
        "./f": "ABCD",
        "./folder/g": "EFGH",
        "./folder/subfolder/h": "IJKL",
    }
    new_dir = FAKE_DIR | added_items
    assert set(new_files(new=new_dir, old=FAKE_DIR)) == set(added_items.keys())


def test_new_files_finds_changed_files() -> None:
    """Test that new_files returns changed files."""
    changed_items = {
        "./a": "ABCD",
        "./folder/c": "EFGH",
        "./folder/subfolder/e": "IJKL",
    }
    new_dir = FAKE_DIR | changed_items
    assert set(new_files(new=new_dir, old=FAKE_DIR)) == set(changed_items.keys())


def test_deleted_files_unchanged_is_empty() -> None:
    """Test that deleted_files returns an empty list if new and old are the same."""
    assert len(deleted_files(FAKE_DIR, FAKE_DIR)) == 0


def test_deleted_files_finds_all() -> None:
    """Test that deleted_files works correctly if all files are deleted."""
    assert set(deleted_files(new={}, old=FAKE_DIR)) == set(FAKE_DIR.keys())


def test_deleted_files_finds_deleted() -> None:
    """Test that deleted_files returns deleted files."""
    deleted = {
        "./a",
        "./folder/subfolder/e",
    }
    new_dir = FAKE_DIR.copy()
    for item in deleted:
        del new_dir[item]
    assert set(deleted_files(new=new_dir, old=FAKE_DIR)) == deleted


def test_deleted_files_ignores_changed_files() -> None:
    """Test that deleted_files ignores changed files."""
    changed_items = {
        "./a": "ABCD",
        "./folder/c": "EFGH",
        "./folder/subfolder/e": "IJKL",
    }
    new_dir = FAKE_DIR | changed_items
    assert len(deleted_files(new=new_dir, old=FAKE_DIR)) == 0


FAKE_SERVER = "ftp.example.com"
FAKE_USER = "USER"
FAKE_PASSWORD = "SUPER SECRET"  # noqa: S105 possible secret

FULL_UPLOAD_OPERATIONS = [
    call.mkd("."),
    call.storbinary("STOR ./a", ANY),
    call.mkd("."),
    call.storbinary("STOR ./b", ANY),
    call.mkd("."),
    call.mkd("./folder"),
    call.storbinary("STOR ./folder/c", ANY),
    call.mkd("."),
    call.mkd("./folder"),
    call.mkd("./folder/sub"),
    call.storbinary("STOR ./folder/sub/d", ANY),
]


@pytest.mark.parametrize(
    ("hashes", "operations"),
    [
        pytest.param(TEST_DATA_HASHES, [], id="unchanged"),
        pytest.param({}, FULL_UPLOAD_OPERATIONS, id="blank"),
        pytest.param(
            TEST_DATA_HASHES | {"./other_folder/x": "0000"},
            [
                call.delete("./other_folder/x"),
            ],
            id="delete",
        ),
        pytest.param(
            TEST_DATA_HASHES | {"./folder/c": "CCCC"},
            [
                call.mkd("."),
                call.mkd("./folder"),
                call.storbinary("STOR ./folder/c", ANY),
            ],
            id="modified",
        ),
        pytest.param(
            {k: v for k, v in TEST_DATA_HASHES.items() if k != "./folder/c"},
            [
                call.mkd("."),
                call.mkd("./folder"),
                call.storbinary("STOR ./folder/c", ANY),
            ],
            id="added",
        ),
    ],
)
def test_incremental_mocked(hashes: dict[str, str], operations: list[Any]) -> None:
    """Test incremental upload (hash file exists and is valid).

    Uses a mocked FTP class.
    """
    args = [
        "--user",
        FAKE_USER,
        "--password",
        FAKE_PASSWORD,
        "--source",
        str(Path(__file__).parent / TEST_DATA_FOLDER),
        FAKE_SERVER,
    ]
    with (
        patch("ftplib.FTP_TLS") as mock_ftp_class,
        patch("io.StringIO") as mock_string_io_class,
    ):
        ftp = mock_ftp_class.return_value
        ftp.__enter__.return_value = ftp
        io = mock_string_io_class.return_value
        io.read.return_value = json.dumps(hashes)
        main(args)

        assert ftp.mock_calls == [
            call.__enter__(),
            call.connect(FAKE_SERVER),
            call.login(FAKE_USER, FAKE_PASSWORD),
            call.set_debuglevel(0),
            call.prot_p(),
            call.cwd("html"),
            call.retrlines("RETR .hashes.json", io.write),
            call.delete(".hashes.json"),
            *operations,
            call.storlines("STOR .hashes.json", ANY),
            call.__exit__(None, None, None),
        ]


def test_full_upload_mocked() -> None:
    """Test full upload (hash file absent).

    Uses a mocked FTP class.
    """
    args = [
        "--user",
        FAKE_USER,
        "--password",
        FAKE_PASSWORD,
        "--source",
        str(Path(__file__).parent / TEST_DATA_FOLDER),
        FAKE_SERVER,
    ]
    with (
        patch("ftplib.FTP_TLS") as mock_ftp_class,
        patch("io.StringIO") as mock_string_io_class,
    ):
        ftp = mock_ftp_class.return_value
        ftp.__enter__.return_value = ftp
        io = mock_string_io_class.return_value
        ftp.retrlines.side_effect = ftplib.error_perm
        ftp.mlsd.side_effect = [
            [
                ("file1", {"type": "file"}),
                ("file2", {"type": "file"}),
                ("folder", {"type": "dir"}),
            ],
            [
                ("child", {"type": "file"}),
            ],
        ]
        main(args)

        assert ftp.mock_calls == [
            call.__enter__(),
            call.connect(FAKE_SERVER),
            call.login(FAKE_USER, FAKE_PASSWORD),
            call.set_debuglevel(0),
            call.prot_p(),
            call.cwd("html"),
            call.retrlines("RETR .hashes.json", io.write),
            call.mlsd("", facts=["type"]),
            call.delete("file1"),
            call.delete("file2"),
            call.mlsd("folder", facts=["type"]),
            call.delete("folder/child"),
            call.rmd("folder"),
            *FULL_UPLOAD_OPERATIONS,
            call.storlines("STOR .hashes.json", ANY),
            call.__exit__(None, None, None),
        ]
