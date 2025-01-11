#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    A caching website uploader using FTPS.
    
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
import itertools
import json
import netrc
import os
import os.path


def file_hash(filepath):
    """The sha256 hash for the file at filepath"""
    with open(filepath, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def folder_hashes():
    """Calculate hashes for all files in the current directory
    
    Return a dict mapping the filenames to their hashes."""
    result = {}
    for dirpath, _, filenames in os.walk("."):
        for name in filenames:
            path = os.path.join(dirpath, name)
            result[path] = file_hash(path)
    return result


def new_files(new, old):
    """The paths of all new and updated files.
    
    new and old are folder hashes representing the new state (i.e. the local copy)
    and old state (i.e. what is currently on the web config.server)"""
    return [f for (f, h) in new.items() if h != old.get(f)]


def deleted_files(new, old):
    """The paths of all deleted files.
    
    new and old are folder hashes representing the new state (i.e. the local copy)
    and old state (i.e. what is currently on the web config.server)"""
    return [f for f in old.keys() if f not in new]


def load_hashes(ftp):
    f = io.StringIO()
    try:
        ftp.retrlines("RETR " + config.hashfile, f.write)
    except ftplib.error_perm:
        return None
    f.seek(0)
    try:
        return json.load(f)
    except json.JSONDecodeError:
        return None


def save_hashes(ftp, hashes):
    f = io.BytesIO(json.dumps(hashes, indent=2).encode("utf8"))
    ftp.storlines("STOR " + config.hashfile, f)


def files_on_server(ftp, path=""):
    content = list(ftp.mlsd(path, facts=["type"]))
    files = [path + "/" + name for (name, facts) in content if facts["type"] == "file"]
    folders = [path + "/" + name for (name, facts) in content if facts["type"] == "dir"]
    subfiles = [files_on_server(ftp, path) for path in folders]
    return list(itertools.chain(files, *subfiles))


def delete_contents(ftp, path):
    """Delete the contents of the directory at path."""
    for name, facts in ftp.mlsd(path, facts=["type"]):
        subpath = os.path.join(path, name)
        if facts["type"] == "file":
            print("Delete:", subpath)
            ftp.delete(subpath)
        elif facts["type"] == "dir":
            delete_contents(ftp, subpath)
            print("Delete dir:", subpath)
            ftp.rmd(subpath)


def create_parent_folder(ftp, path):
    folder, _ = os.path.split(path)
    if folder:
        try:
            create_parent_folder(ftp, folder)
            ftp.mkd(folder)
        except ftplib.error_perm:
            pass


def upload_files(ftp, paths):
    for path in paths:
        create_parent_folder(ftp, path)
        with open(path, "rb") as file:
            print("Upload:", path)
            ftp.storbinary("STOR " + path, file)


def upload_all(ftp):
    delete_contents(ftp, "")
    new_hashes = folder_hashes()
    paths = new_files(new_hashes, {})
    upload_files(ftp, paths)
    save_hashes(ftp, new_hashes)


def upload_changed(ftp, old_hashes):
    ftp.delete(config.hashfile)
    new_hashes = folder_hashes()
    paths = new_files(new_hashes, old_hashes)
    upload_files(ftp, paths)
    for path in deleted_files(new_hashes, old_hashes):
        print("Delete:", path)
        ftp.delete(path)
    save_hashes(ftp, new_hashes)


def load_configuration():
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
        "--destination", "-d", default="html", help="the remote directory"
    )
    parser.add_argument(
        "--hashfile", default=".hashes.json", help="name of the hash file"
    )
    parser.add_argument(
        "--netrc", action="store_true", help="read credentials from .netrc file"
    )
    global config
    config = parser.parse_args()

    if config.netrc:
        content = netrc.netrc()
        authenticators = content.authenticators(config.server)
        if authenticators is None:
            parser.error(f"Found no credentials for {config.server} in .netrc")
        (user, _, password) = authenticators
        if config.user and user != config.user:
            parser.error(f"Command line user name and.netrc username for {config.server} do not match")
        config.user = user
        config.password = password


def main():
    load_configuration()
    os.chdir(config.source)
    with ftplib.FTP_TLS() as ftp:
        ftp.connect(config.server)
        ftp.login(config.user, config.password)
        # ftp.set_debuglevel(1)

        ftp.prot_p()
        
        ftp.cwd(config.destination)
        old_hashes = load_hashes(ftp)
        if old_hashes is None:
            print("No hash file found. Performing full upload")
            upload_all(ftp)
        else:
            print("Synchronizing changes")
            upload_changed(ftp, old_hashes)


main()
