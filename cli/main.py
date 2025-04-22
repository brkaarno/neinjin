import click
import subprocess
import sys
import os
import re
import platform
from packaging.version import Version

import repo_root


def check_uv(args: list[str]):
    # The args here should be kept in sync with the 10j script.
    localdir = repo_root.localdir()
    subprocess.check_call([localdir / "uv", "--config-file", localdir / "uv.toml", *args])


def check_lib_deps_gmp():
    def consider(path):
        pass

    match platform.system():
        case "Darwin":
            consider("/opt/homebrew/opt/gmp/lib/libgmp.10.dylib")
        case "Linux":
            consider("/usr/x86_64-linux-gnu/libgmp.so.10")


def do_check_deps(report: bool):
    def find_git_version() -> str:
        # 'git version 2.43.0'
        # 'git version 2.37.1 (Apple Git-137.1)'
        git_version_full = subprocess.check_output(["git", "version"]).decode("utf-8")
        git_version_mid = git_version_full.removeprefix("git version ")
        return git_version_mid.split(" ")[0]

    def find_clang_version() -> str:
        # '''
        # Ubuntu clang version 18.1.3 (1ubuntu1)
        # Target: x86_64-pc-linux-gnu
        # Thread model: posix
        # InstalledDir: /usr/bin
        # '''
        #
        # '''
        # Apple clang version 14.0.0 (clang-1400.0.29.202)
        # Target: arm64-apple-darwin22.6.0
        # Thread model: posix
        # InstalledDir: /Applications/Xcode.app/[...]/XcodeDefault.xctoolchain/usr/bin
        # '''
        clang_version_full = subprocess.check_output(["clang", "--version"]).decode("utf-8")
        clang_version_m = re.search(r"clang version ([^ ]+)", clang_version_full)
        assert clang_version_m is not None
        return clang_version_m.group(1)

    def find_opam_version() -> str:
        return subprocess.check_output(["opam", "--version"]).decode("utf-8").rstrip()

    git_version = find_git_version()
    clang_version = find_clang_version()

    if Version(git_version) < Version("2.36"):
        click.echo("Note: git version 2.36 or later is required")

    if Version(clang_version) < Version("18"):
        click.echo("Note: clang version 18 or later is required")

    if report:
        click.echo(f"{git_version=}")
        click.echo(f"{clang_version=}")


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
    check_uv("tool run ruff format".split())


def do_check_py_fmt():
    check_uv("tool run ruff format --check".split())


def do_check_py():
    check_uv("tool run ruff check --quiet".split())
    do_check_py_fmt()


def parse_git_name_status_line(bs: bytes) -> tuple[str, bytes]:
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

    # This is intended to run in CI, so we assume a standard git repo,
    # without accommodating jj users (yet). This shouldn't be a big deal
    # because jj already implements file size checks.
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


def do_check_for_git_merges(base, head) -> None:
    if not base or not head:
        click.echo("base or head missing/empty", err=True)
        sys.exit(1)

    # Alternative construction: given a merge commit `merge` and
    # assuming that `head` is the parent from the feature branch,
    # then `base` should be equal to $(git merge-base head merge).

    merges = subprocess.check_output(["git", "rev-list", "--merges", f"{base}..{head}"])
    # To exclude certain commits from the above, use --invert-grep
    # with additional flags to search author/committer/etc.
    if merges:
        click.echo("Please rebase your branch by running `git rebase main`", err=True)
        sys.exit(1)


@click.group()
def cli():
    pass


@cli.command()
def status():
    click.echo(f"{repo_root.find_repo_root_dir_Path()=}")
    click.echo(f"{sys.argv[0]=}")
    do_check_deps(report=True)


@cli.command()
def fmt_py():
    do_fmt_py()


@cli.command()
def check_py():
    try:
        do_check_py()
    except subprocess.CalledProcessError:
        sys.exit(1)


@cli.command()
def check_deps():
    do_check_deps(report=True)


@cli.command()
def check_all():
    # The Click documentation discourages invoking one command from
    # another, and doing so is quite awkward.
    # We instead implement functionality in the do_*() functions
    # and then make each command be a thin wrapper to invoke the fn.
    try:
        do_check_py()
    except subprocess.CalledProcessError:
        sys.exit(1)


@cli.command()
@click.option("--base", required=True, help="base ref", type=str)
@click.option("--head", required=True, help="head ref", type=str)
def check_git_incoming_filesizes(base, head):
    do_check_git_incoming_filesizes(base, head)


@cli.command()
@click.option("--base", required=True, help="base ref", type=str)
@click.option("--head", required=True, help="head ref", type=str)
def check_for_git_merges(base, head):
    do_check_for_git_merges(base, head)


if __name__ == "__main__":
    cli()
