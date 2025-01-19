#! /usr/bin/env python3

"""A caching website uploader using FTPS.

The current state of the site is recorded in a statefile. Thus, after a minor
change to the site, only the changed files have to be uploaded.
The statefile contains a checksum (sha1 digest) for each file. Thus,
mere touching a file, or regenerating it with identical contents won't
trigger an upload.
"""

import argparse
import ftplib
import hashlib
import io
import json
import netrc
import os
import os.path
import sys
from pathlib import Path


def file_hash(filepath: Path) -> str:
    """Calculate the he sha256 hash for the file at filepath."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def folder_hashes() -> dict[str, str]:
    """Calculate hashes for all files in the current directory.

    Return a dict mapping the filenames to their hashes.
    """
    result = {}
    for dirpath, _, filenames in Path().walk():
        for name in filenames:
            path = dirpath / name
            result[str(path)] = file_hash(path)
    return result


def new_files(new: dict[str, str], old: dict[str, str]) -> list[str]:
    """Determine the paths of all new and updated files.

    new and old are folder hashes representing the new state (i.e. the local copy)
    and old state (i.e. what is currently on the web config.server)
    """
    return sorted([f for (f, h) in new.items() if h != old.get(f)])


def deleted_files(new: dict[str, str], old: dict[str, str]) -> list[str]:
    """Determine the paths of all deleted files.

    new and old are folder hashes representing the new state (i.e. the local copy)
    and old state (i.e. what is currently on the web config.server)
    """
    return sorted([f for f in old if f not in new])

def normalize_paths(hashes: dict[str, str]) -> dict[str, str]:
    """Strip leading './' from the paths in hashes.

    Old versions of ftpsync used to store paths with this prefix.
    While the paths with and without prefix are logically equivalent,
    we must normalize them, otherwise we would first upload all files
    and them delete them.
    This would effectively wipe out all content.
    Worse, because the new hashfile indicates that the files are present,
    further runs of ftpsync wouldn't even restore the data.
    """
    return {k.removeprefix("./"): v for k, v in hashes.items()}

def get_folders(hashes: dict[str, str]) -> set[str]:
    """Get the set of folders containing from the paths in hashes."""
    return set(*[(str(folder)for p in hashes for folder in Path(p).parents)])

class FtpSynchronizer:
    """Class to manage synchronization of a local directory to a remote FTP server."""

    ftp: ftplib.FTP_TLS
    config: argparse.Namespace
    already_created_folders: set[str]

    def __init__(self, ftp: ftplib.FTP_TLS, config: argparse.Namespace) -> None:
        """Create a synchronizer with the given FTP client and configuration."""
        self.ftp = ftp
        self.config = config
        self.already_created_folders = set()

    def load_hashes(self) -> dict[str, str] | None:
        """Load the hashes from the hashfile on the server."""
        f = io.StringIO()
        try:
            self.ftp.retrlines("RETR " + self.config.hashfile, f.write)
        except ftplib.error_perm:
            return None
        f.seek(0)
        try:
            return normalize_paths(json.load(f))
        except json.JSONDecodeError:
            return None

    def save_hashes(self, hashes: dict[str, str]) -> None:
        """Save the hashes to the hashfile on the server."""
        f = io.BytesIO(json.dumps(hashes, indent=2).encode("utf8"))
        self.ftp.storlines("STOR " + self.config.hashfile, f)

    def delete_contents(self, path: str) -> None:
        """Delete the contents of the directory at path."""
        for name, facts in self.ftp.mlsd(path, facts=["type"]):
            subpath = str(Path(path) / name)
            if facts["type"] == "file":
                print("Delete:", subpath)
                self.ftp.delete(subpath)
            elif facts["type"] == "dir":
                self.delete_contents(subpath)
                print("Delete dir:", subpath)
                self.ftp.rmd(subpath)

    def create_parent_folder(self, path: str) -> None:
        """Create the parent folder for path, if it doesn't exist yet."""
        folder, _ = os.path.split(path)
        if folder and folder not in self.already_created_folders:
            try:
                self.create_parent_folder(folder)
                self.already_created_folders.add(folder)
                self.ftp.mkd(folder)
            except ftplib.error_perm:
                pass

    def upload_files(self, paths: list[str]) -> None:
        """Upload the given files to the server."""
        for path in paths:
            self.create_parent_folder(path)
            with Path(path).open("rb") as file:
                print("Upload:", path)
                self.ftp.storbinary("STOR " + path, file)

    def upload_all(self) -> None:
        """Upload all files to the server, ignoring the hashes.

        **Warning**: this deletes the previous content!
        """
        self.delete_contents("")
        new_hashes = folder_hashes()
        paths = new_files(new_hashes, {})
        self.upload_files(paths)
        self.save_hashes(new_hashes)

    def upload_changed(self, old_hashes: dict[str, str]) -> None:
        """Upload all changes to the server, mirroring the local content."""
        self.ftp.delete(self.config.hashfile)
        self.already_created_folders = get_folders(old_hashes)
        new_hashes = folder_hashes()
        paths = new_files(new_hashes, old_hashes)
        self.upload_files(paths)
        for path in deleted_files(new_hashes, old_hashes):
            print("Delete:", path)
            self.ftp.delete(path)
        self.save_hashes(new_hashes)

    def run(self) -> None:
        """Run the synchronization."""
        self.ftp.connect(self.config.server)
        self.ftp.login(self.config.user, self.config.password)
        self.ftp.set_debuglevel(self.config.verbosity)

        self.ftp.prot_p()

        self.ftp.cwd(self.config.destination)
        old_hashes = self.load_hashes()
        if old_hashes is None:
            print("No hash file found. Performing full upload")
            self.upload_all()
        else:
            print("Synchronizing changes")
            self.upload_changed(old_hashes)


def load_configuration(args: list[str]) -> argparse.Namespace:
    """Load the configuration settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", help="The FTP host name")
    parser.add_argument("--user", "-u", help="The FTP username")
    parser.add_argument("--password", "-p", help="The FTP login password")
    parser.add_argument(
        "--source",
        "-s",
        default="public",
        help="the local directory to be synchronized with the server",
    )
    parser.add_argument(
        "--destination",
        "-d",
        default="html",
        help="the remote directory",
    )
    parser.add_argument(
        "--hashfile",
        default=".hashes.json",
        help="name of the hash file",
    )
    parser.add_argument(
        "--netrc",
        action="store_true",
        help="read credentials from .netrc file",
    )
    parser.add_argument(
        "-v",
        "--verbosity",
        action="count",
        default=0,
        help="increase output verbosity",
    )
    config = parser.parse_args(args=args)

    if config.netrc:
        content = netrc.netrc()
        authenticators = content.authenticators(config.server)
        if authenticators is None:
            parser.error(f"Found no credentials for {config.server} in .netrc")
        (user, _, password) = authenticators
        if config.user and user != config.user:
            parser.error(
                f"Command line user name and.netrc username for {config.server}"
                " do not match",
            )
        config.user = user
        config.password = password
    return config


def main(args: list[str]) -> None:
    """Execute the main program."""
    config = load_configuration(args)
    os.chdir(config.source)
    with ftplib.FTP_TLS() as ftp:  # noqa: S321 (ftp considered insecure. We use TLS, so should be OK.)
        sychronizer = FtpSynchronizer(ftp, config)
        sychronizer.run()


if __name__ == "__main__":
    main(sys.argv[1:])
