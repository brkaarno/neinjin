import click
import subprocess
import sys
import os
import re
import platform
from packaging.version import Version

import repo_root


def check_call_uv(args: list[str]):
    # The args here should be kept in sync with the 10j script.
    localdir = repo_root.localdir()
    subprocess.check_call([localdir / "uv", "--config-file", localdir / "uv.toml", *args])


def run_output_git(args: list[str], check=False):
    jjdir = repo_root.find_repo_root_dir_Path() / ".jj"
    if jjdir.is_dir():
        gitroot = subprocess.check_output(["jj", "git", "root"]).decode("utf-8")
        cp = subprocess.run(["git", "--git-root", gitroot, *args], check=False, capture_output=True)
    else:
        cp = subprocess.run(["git", *args], check=False, capture_output=True)

    if cp.stderr:
        click.echo(cp.stderr, err=True)
    if check:
        cp.check_returncode()
    return cp.stdout


def check_output_git(args: list[str]):
    return run_output_git(args, check=True)


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
        check_call_uv("tool list".split())


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
    check_call_uv("tool run -vv ruff format".split())


def do_check_py_fmt():
    check_call_uv("tool run -vv ruff format --check".split())


def do_check_py():
    check_call_uv("tool run -vv ruff check --quiet".split())
    do_check_py_fmt()


def parse_git_name_status_line(bs: bytes) -> tuple[str, bytes]:
    """
    >>> parse_git_name_status_line(b'A       .gitignore')
    ("A", b'.gitignore')
    """
    status = bs[0:1].decode("utf-8")
    path = bs.split(b"\t", 1)[-1]
    return (status, path)


def do_check_repo_file_sizes() -> bool:
    """Returns True if the check passed, False otherwise"""

    max_file_size = 987654

    rootdir = repo_root.find_repo_root_dir_Path()
    # fmt: off
    exclusions = [
        "-path", rootdir / ".git", "-o",
        "-path", rootdir / ".jj", "-o",
        "-path", rootdir / "cli" / ".venv", "-o",
        "-path", rootdir / "_local",
    ]
    cmd = [
        "find", rootdir, "(", *exclusions, ")", "-prune", "-o",
            "-type", "f", "-size", f"+{max_file_size}c", "-print",
    ]
    # fmt: on
    lines = subprocess.check_output(cmd, stderr=subprocess.PIPE).split(b"\n")
    lines = [line for line in lines if line != b""]
    if not lines:
        return True

    # See https://git-scm.org/docs/git-check-ignore for details of the output format.
    # We don't check the return value because it is non-zero when no path is ignored,
    # which is not an error case in this context.
    lines = run_output_git(["check-ignore", "--verbose", "--non-matching", *lines]).split(b"\n")
    non_ignored = []
    for line in lines:
        if line == b"":
            continue

        fields, pathname = line.split(b"\t")
        if fields == b"::":
            # Fields are source COLON linenum COLON pattern
            # If all fields are empty, the pathname did not match any pattern,
            # which is to say: it was not ignored.
            non_ignored.append(pathname)

    if not non_ignored:
        return True

    click.echo("ERROR: Unexpected large files:", err=True)
    for line in non_ignored:
        click.echo("\t" + path_bytes_to_str(line), err=True)
    return False


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
def check_star():
    # The Click documentation discourages invoking one command from
    # another, and doing so is quite awkward.
    # We instead implement functionality in the do_*() functions
    # and then make each command be a thin wrapper to invoke the fn.
    try:
        do_check_py()
    except subprocess.CalledProcessError:
        sys.exit(1)


@cli.command()
def check_repo_file_sizes():
    if not do_check_repo_file_sizes():
        sys.exit(1)


if __name__ == "__main__":
    cli()
