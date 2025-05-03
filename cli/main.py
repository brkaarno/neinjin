import subprocess
import sys
import os
import re
import platform

import click
from packaging.version import Version

import repo_root
import provisioning
import hermetic


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
        return git_version_mid.split(" ")[0].strip()

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
        hermetic.check_call_uv("run ruff version".split())
        hermetic.check_call_uv("tool dir".split())
        hermetic.check_call_uv("tool list".split())


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
    hermetic.check_call_uv("run ruff format".split())


def do_check_py_fmt():
    hermetic.check_call_uv("run ruff format --check".split())


def do_check_py():
    hermetic.check_call_uv("run ruff check --quiet".split())
    do_check_py_fmt()


def do_fmt_rs():
    root = repo_root.find_repo_root_dir_Path()
    hermetic.run_shell_cmd(f"cd {root / 'c2rust'} && cargo +stable fmt", check=True)


def do_check_rs_fmt():
    root = repo_root.find_repo_root_dir_Path()
    hermetic.run_shell_cmd(f"cd {root / 'c2rust'} && cargo +stable fmt -- --check", check=True)


def do_check_rs():
    root = repo_root.find_repo_root_dir_Path()
    hermetic.run_shell_cmd(
        f"cd {root / 'c2rust'} && cargo +stable clippy --locked"
        " -p c2rust -p c2rust-transpile"
        " -- -Aclippy::needless_lifetimes",
        check=True,
    )
    # do_check_rs_fmt()  # c2rust is not yet fmt-clean, will tackle later


def do_test_unit_rs():
    root = repo_root.find_repo_root_dir_Path()
    hermetic.run_shell_cmd(
        f"cd {root / 'c2rust'} && cargo +stable test --locked -p c2rust -p c2rust-transpile",
        check=True,
    )


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
    lines = hermetic.run_output_git(["check-ignore", "--verbose", "--non-matching", *lines]).split(
        b"\n"
    )
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
def fmt_rs():
    do_fmt_rs()


@cli.command()
def check_rs():
    try:
        do_check_rs()
    except subprocess.CalledProcessError:
        sys.exit(1)


@cli.command()
def test_unit_rs():
    try:
        do_test_unit_rs()
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
        do_check_rs()
    except subprocess.CalledProcessError:
        sys.exit(1)


@cli.command()
def check_repo_file_sizes():
    if not do_check_repo_file_sizes():
        sys.exit(1)


@cli.command()
def opam():
    "Run opam (with 10j's switch, etc)"
    pass  # placeholder command


@cli.command()
def dune():
    "Run dune (with 10j's switch, etc)"
    pass  # placeholder command


@cli.command()
def cargo():
    "Alias for `10j exec cargo`"
    pass  # placeholder command


# placeholder command
@cli.command()
def exec():
    "Run a command with 10j's PATH etc"
    pass


@cli.command()
def provision():
    provisioning.provision_desires()


if __name__ == "__main__":
    # Per its own documentation, Click does not support losslessly forwarding
    # command line arguments. So when we want to do that, we bypass Click.
    # This especially matters for commands akin to `opam exec -- dune --help`.
    # Here, `--` separator ensures that opam passes the `--help` argument to
    # dune. But Click unconditionally consumes the double-dash, resulting in
    # the `--help` argument being unhelpfully consumed by opam itself.
    if len(sys.argv) > 1:
        if sys.argv[1] == "opam":
            sys.exit(hermetic.run_opam(sys.argv[2:]).returncode)
        if sys.argv[1] == "dune":
            sys.exit(hermetic.run_opam(["exec", "--", "dune", *sys.argv[2:]]).returncode)
        if sys.argv[1] == "cargo":
            sys.exit(hermetic.run_shell_cmd(sys.argv[1:]).returncode)
        if sys.argv[1] == "exec":
            sys.exit(hermetic.run_shell_cmd(sys.argv[2:]).returncode)
        if sys.argv[1] == "true":
            sys.exit(0)

    cli()
