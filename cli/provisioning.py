from pathlib import Path
import platform
import sys
import os
from urllib.parse import urlparse
import urllib.request
import tarfile
import shutil
import subprocess

from packaging.version import Version
import click

import repo_root
import hermetic
from sha256sum import compute_sha256


class ProvisioningError(Exception):
    pass


# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# sysroot = Path(SCRIPT_DIR) / "sysroot-tenjin"
def provision_debian_bullseye_sysroot(target_arch: str, dest_sysroot: Path):
    print("Downloading and unpacking sysroot tarball...")

    CHROME_LINUX_SYSROOT_URL = "https://commondatastorage.googleapis.com/chrome-linux-sysroot"

    DEBIAN_BULLSEYE_SYSROOT_SHA256SUMS = {
        "x86_64": "36a164623d03f525e3dfb783a5e9b8a00e98e1ddd2b5cff4e449bd016dd27e50",
        "arm64": "2f915d821eec27515c0c6d21b69898e23762908d8d7ccc1aa2a8f5f25e8b7e18",
        "armhf": "47b3a0b161ca011b2b33d4fc1ef6ef269b8208a0b7e4c900700c345acdfd1814",
    }
    tarball_sha256sum = DEBIAN_BULLSEYE_SYSROOT_SHA256SUMS[target_arch]

    url = CHROME_LINUX_SYSROOT_URL + "/" + tarball_sha256sum

    if dest_sysroot.is_dir():
        shutil.rmtree(dest_sysroot)
    dest_sysroot.mkdir()
    tarball = dest_sysroot / "tenjin-sysroot.tar.xz"

    _localfilename, _headers = urllib.request.urlretrieve(url, tarball)
    sha256sum = compute_sha256(tarball)
    if sha256sum != tarball_sha256sum:
        raise ProvisioningError("Sysroot hash verification failed!")
    shutil.unpack_archive(tarball, dest_sysroot, filter="tar")
    tarball.unlink()


def provision_opam_binary_into(opam_version: str, localdir: Path, opamroot: Path) -> None:
    """Returns the path to the installed binary"""

    cli_sh_dir = repo_root.find_repo_root_dir_Path() / "cli" / "sh"
    installer_sh = cli_sh_dir / f"install-opam-{opam_version}.sh"

    if not installer_sh.is_file():
        raise ProvisioningError(f"Did not find expected installer script for opam-{opam_version}.")

    # If the system happens to have a copy of a suitable version of opam, grab it.
    sys_opam = shutil.which("opam")
    if sys_opam is not None:
        sys_opam_version = subprocess.check_output([sys_opam, "--version"]).decode("utf-8")
        if Version(sys_opam_version) >= Version(opam_version):
            print("Found a suitable version of opam at", str(sys_opam))
            shutil.copy(sys_opam, localdir / "opam")
            return

    # Otherwise, we'll need to run the installer to get it.
    print("Downloading a local copy of opam...")
    subprocess.check_call(["sh", installer_sh, "--download-only"])
    tagged = list(Path(".").glob(f"opam-{opam_version}-*"))
    assert len(tagged) == 1
    tagged = tagged[0]
    subprocess.check_call(["chmod", "+x", tagged])
    tagged.replace(localdir / "opam")
    hermetic.check_call_opam(["--version"])


def provision_opam_into(localdir: Path):
    opam_version = "2.3.0"
    ocaml_version = "5.3.0"

    opamroot = localdir / "opamroot"
    provision_opam_binary_into(opam_version, localdir, opamroot)

    if opamroot.is_dir():
        shutil.rmtree(opamroot)

    click.echo("================================================================")
    click.echo("Initializing opam; this will take about half a minute...")
    click.echo("      (subsequent output comes from `opam init --bare`)")
    click.echo("----------------------------------------------------------------")
    click.echo("")
    hermetic.check_call_opam(
        ["init", "--bare", "--no-setup", "--disable-completion"], eval_env=False
    )
    click.echo("")
    click.echo("================================================================")
    click.echo("Installing OCaml; this will take a few minutes to compile...")
    click.echo("      (subsequent output comes from `opam switch create`)")
    click.echo("----------------------------------------------------------------")

    hermetic.check_call_opam(
        ["switch", "create", "tenjin", ocaml_version, "--no-switch"],
        eval_env=False,
        env={**os.environ, "OPAMNOENVNOTICE": "1"},
    )

    print(
        "opam version:",
        hermetic.run_opam(["--version"], check=True, capture_output=True).stdout.decode("utf-8"),
    )


def provision():
    provision_opam_into(repo_root.localdir())


def clang_plus_llvm_url(version: str, clangplatform: str) -> str:
    urlbase = "https://github.com/llvm/llvm-project/releases/download"
    archivename = f"clang+llvm-{version}-{clangplatform}.tar.xz"
    return f"{urlbase}/llvmorg-{version}/{archivename}"


def print_debian_compat_warning(distro: str):
    print(
        f"""
        Warning: you appear to have a non-debian distro '{distro}'.
        Proceeding with download of Ubuntu-compatible Clang+LLVM
          but we haven't tested this situation so caveat emptor.
    """,
        file=sys.stderr,
    )


def introspect_clang_platform(version: str) -> str:
    if platform.system() == "Linux":
        idlike = platform.freedesktop_os_release()["ID_LIKE"]
        if idlike != "debian":
            print_debian_compat_warning(idlike)

    match (version, platform.machine(), platform.system()):
        case (_, "arm64", "Darwin"):
            return "arm64-apple-macos11"

        case (_, "arm64", "Linux"):
            return "aarch64-linux-gnu"

        case (_, "x86_64", "Linux"):
            return "x86_64-linux-gnu-ubuntu-18.04"

        case _:
            print(
                """
                LLVM does not have precompiled binaries for your system.
                Please install Clang and LLVM via your platform's package
                manager.
            """,
                file=sys.stderr,
            )
            return None


# The extraction process is about twice as slow on macOS
# for clang+llvm versus the native bsdtar utility, but
# since this is a one-time cost it seems better to just
# avoid all non-Python dependencies.
def download_and_extract_clang(clangurl: str, target_dir: Path) -> Path | None:
    """
    Downloads a .tar.xz file from the given URL and extracts it to the target directory.

    Args:
        clangurl (str): URL of the .tar.xz file to download
        target_dir (str): Directory to extract contents to.

    Returns:
        None if something went wrong
        Path to the extracted directory otherwise
    """
    assert clangurl.endswith(".tar.xz")

    print("This will take a few minutes...")

    try:
        # Create a temporary file name for the download
        temp_file = os.path.basename(urlparse(clangurl).path)

        print(f"Downloading {clangurl}...")
        # Download the file
        urllib.request.urlretrieve(clangurl, temp_file)

        # Create target directory if it doesn't exist
        target_dir.mkdir(parents=True, exist_ok=True)

        print(f"Extracting to {target_dir}...")
        # Extract the tar.xz file
        with tarfile.open(temp_file, "r:xz") as tar:
            tar.extractall(path=target_dir)

        # Clean up the temporary file
        os.remove(temp_file)

        print("Download and extraction completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
        # Clean up any temporary files if they exist
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None

    return target_dir / (temp_file.removesuffix(".tar.xz"))
