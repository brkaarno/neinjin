import click
import subprocess
import sys
from typing import Tuple
import os


def trim_empty_marker(z: str) -> str:
    return z.stripprefix("z-")


def path_bytes_to_str(b: bytes) -> str:
    """Convert a file path byte string to printable text."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        # Fall back to alternative encoding or filesystem encoding
        return b.decode(os.fsdecode.encoding, errors="replace")


def do_fmt_py():
    subprocess.check_call("uv tool run ruff format".split())


def do_check_py():
    subprocess.check_call("uv tool run ruff check".split())
    subprocess.check_call("uv tool run ruff format --check".split())


def parse_git_name_status_line(bs: bytes) -> Tuple[str, bytes]:
    """
    >>> parse_git_name_status_line(b'A       .gitignore')
    ("A", b'.gitignore')
    """
    status = bs[0:1].decode("utf-8")
    path = bs.split(b"\t", 1)[-1]
    return (status, path)


def check_sizes_via_git_name_status(bslines, max_file_size: int, repo_root: bytes) -> None:
    for line in bslines:
        status, localpath = parse_git_name_status_line(line)
        if status in ("A", "M"):
            path = os.path.join(repo_root, localpath)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                if size > max_file_size:
                    click.echo(
                        " ".join([
                            "File exceeds maximum permitted size:",
                            path_bytes_to_str(path),
                            "\t",
                            f"size was {size} > {max_file_size}",
                        ]),
                        err=True,
                    )
                    sys.exit(1)
            else:
                print("non-file path ", path)


def do_check_git_incoming_filesizes(base, head) -> None:
    if not base or not head:
        click.echo("base or head missing/empty", err=True)
        sys.exit(1)

    max_file_size = 987654

    repo_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).rstrip()

    # The trailing -- ensures git parses the inputs as revisions not paths.
    lines = subprocess.check_output(["git", "diff", "--name-status", head, base, "--"]).split(b"\n")
    check_sizes_via_git_name_status(lines, max_file_size, repo_root)

    # Check staged changes for convenience. In CI this will be a no-op.
    # We assume here that the on-disk size matches the staged size.
    # This might be wrong, but it doesn't matter, since it will be
    # caught by CI even if a file is missed before being pushed to CI.
    lines = subprocess.check_output(["git", "diff", "--name-status", "--cached"]).split(b"\n")
    check_sizes_via_git_name_status(lines, max_file_size, repo_root)


@click.group()
def cli():
    pass


@cli.command()
def fmt_py():
    do_fmt_py()


@cli.command()
def check_py():
    do_check_py()


@cli.command()
def check_all():
    # The Click documentation discourages invoking one command from
    # another, and doing so is quite awkward.
    # We instead implement functionality in the do_*() functions
    # and then make each command be a thin wrapper to invoke the fn.
    do_check_py()


@cli.command()
@click.option("--base", required=True, help="base ref", type=str)
@click.option("--head", required=True, help="head ref", type=str)
def check_git_incoming_filesizes(base, head):
    do_check_git_incoming_filesizes(base, head)


if __name__ == "__main__":
    cli()
