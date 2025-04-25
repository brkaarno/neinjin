from pathlib import Path
import platform
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


def sez(msg: str, ctx: str, err=False):
    click.echo("TENJIN SEZ: " + ctx + msg, err=err)


# platform.system() in ["Linux", "Darwin"]
# platform.machine() in ["x86_64", "arm64"]


def provision_debian_bullseye_sysroot_into(target_arch: str, dest_sysroot: Path):
    def say(msg: str):
        sez(msg, ctx="(sysroot) ")

    say("Downloading and unpacking sysroot tarball...")

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


def provision_opam_binary_into(opam_version: str, localdir: Path) -> None:
    """Returns the path to the installed binary"""

    def say(msg: str):
        sez(msg, ctx="(opam) ")

    cli_sh_dir = repo_root.find_repo_root_dir_Path() / "cli" / "sh"
    installer_sh = cli_sh_dir / f"install-opam-{opam_version}.sh"

    if not installer_sh.is_file():
        raise ProvisioningError(f"Did not find expected installer script for opam-{opam_version}.")

    # If the system happens to have a copy of a suitable version of opam, grab it.
    sys_opam = shutil.which("opam")
    if sys_opam is not None:
        sys_opam_version = subprocess.check_output([sys_opam, "--version"]).decode("utf-8")
        if Version(sys_opam_version) >= Version(opam_version):
            say("Symlinking to a suitable version of opam at", str(sys_opam))
            os.symlink(sys_opam, str(localdir / "opam"))
            return

    # Otherwise, we'll need to run the installer to get it.
    say("Downloading a local copy of opam...")
    subprocess.check_call(["sh", installer_sh, "--download-only"])
    tagged = list(Path(".").glob(f"opam-{opam_version}-*"))
    assert len(tagged) == 1
    tagged = tagged[0]
    subprocess.check_call(["chmod", "+x", tagged])
    tagged.replace(localdir / "opam")


def provision_opam_into(localdir: Path):
    def say(msg: str):
        sez(msg, ctx="(opam) ")

    opam_version = "2.3.0"
    ocaml_version = "5.3.0"

    provision_opam_binary_into(opam_version, localdir)

    opamroot = localdir / "opamroot"
    if opamroot.is_dir():
        shutil.rmtree(opamroot)

    # Bubblewrap does not work inside Docker containers, at least not without
    # heinous workarounds, if we're in Docker then we don't really need it anyway.
    # So we'll try running a trivial command with it; if it fails, we'll tell opam
    # not to use it.
    try:
        sandboxing_arg = []
        subprocess.check_call([hermetic.xj_build_deps(localdir) / "bin" / "bwrap", "--", "true"])
    except subprocess.CalledProcessError:
        say("Oh! No working bubblewrap. Assuming this is because we're in Docker. Disabling it...")
        sandboxing_arg = ["--disable-sandboxing"]

    say("================================================================")
    say("Initializing opam; this will take about half a minute...")
    say("      (subsequent output comes from `opam init --bare`)")
    say("----------------------------------------------------------------")
    say("")
    hermetic.check_call_opam(
        ["init", "--bare", "--no-setup", "--disable-completion", *sandboxing_arg],
        eval_opam_env=False,
    )
    say("")
    say("================================================================")
    say("Installing OCaml; this will take a few minutes to compile...")
    say("      (subsequent output comes from `opam switch create`)")
    say("----------------------------------------------------------------")

    try:
        hermetic.check_call_opam(
            ["switch", "create", "tenjin", ocaml_version, "--no-switch"],
            eval_opam_env=False,
            env_ext={"OPAMNOENVNOTICE": "1"},
        )
    except subprocess.CalledProcessError as e:
        with open('hi.c', 'w') as f:
            f.write("#include <stdio.h>\nint main() { printf(\"hi\"); return 0; }")
        subprocess.check_call(["gcc", "hi.c", "-o", "hi"])
        subprocess.check_call(["./hi"], shell=True)

        raise e

    opam_version_seen = hermetic.run_opam(
        ["--version"], check=True, capture_output=True
    ).stdout.decode("utf-8")
    say(f"opam version: {opam_version_seen}")


def provision_cmake_into(localdir: Path, version: str):
    def fmt_url(tag: str) -> str:
        return f"https://github.com/Kitware/CMake/releases/download/v{version}/cmake-{version}-{tag}.tar.gz"

    def mk_url() -> str:
        match [platform.system(), platform.machine()]:
            case ["Linux", "x86_64"]:
                return fmt_url("linux-x86_64")
            case ["Linux", "arm64"]:
                return fmt_url("linux-aarch64")
            case ["Darwin", "x86_64"]:
                return fmt_url("macos-universal")
            case sys_mach:
                raise ProvisioningError(
                    f"Tenjin does not yet support {sys_mach} for acquiring CMake."
                )

    download_and_extract_tarball(mk_url(), localdir, ctx="(cmake) ", time_estimate="a minute")


def provision_10j_llvm_into(localdir: Path):
    if Path("LLVM-18.1.8-Linux-x86_64.tar.xz").is_file():
        extract_tarball(
            Path("LLVM-18.1.8-Linux-x86_64.tar.xz"),
            hermetic.xj_llvm_root(localdir),
            ctx="(llvm) ",
            time_estimate="a minute",
        )
    else:
        url = "https://images.aarno-labs.com/amp/ben/LLVM-18.1.8-Linux-x86_64.tar.xz"
        download_and_extract_tarball(
            url, hermetic.xj_llvm_root(localdir), ctx="(llvm) ", time_estimate="a minute"
        )

    sysroot_name = "sysroot"
    provision_debian_bullseye_sysroot_into(
        platform.machine(), hermetic.xj_llvm_root(localdir) / sysroot_name
    )

    # Write config files to make sure that the sysroot is used by default.
    for name in ("clang", "clang++", "cc", "c++"):
        with open(
            hermetic.xj_llvm_root(localdir) / "bin" / f"{name}.cfg", "w", encoding="utf-8"
        ) as f:
            f.write(f"--sysroot <CFGDIR>/../{sysroot_name}\n")

    # Add symbolic links for the binutils-alike tools.
    # Tools not provided by LLVM: ranlib, size
    binutils_names = ["ar", "as", "nm", "objcopy", "objdump", "readelf", "strings", "strip"]
    for name in binutils_names:
        src = hermetic.xj_llvm_root(localdir) / "bin" / f"llvm-{name}"
        dst = hermetic.xj_llvm_root(localdir) / "bin" / f"{name}"
        if not dst.is_symlink():
            os.symlink(src, dst)

    for src, dst in [("clang", "cc"), ("clang++", "c++"), ("lld", "ld")]:
        src = hermetic.xj_llvm_root(localdir) / "bin" / src
        dst = hermetic.xj_llvm_root(localdir) / "bin" / dst
        if not dst.is_symlink():
            os.symlink(src, dst)


def provision_10j_deps_into(localdir: Path):
    url = "https://images.aarno-labs.com/amp/ben/xj-build-deps_linux-x86_64.tar.xz"
    download_and_extract_tarball(
        url, hermetic.xj_build_deps(localdir), ctx="(builddeps) ", time_estimate="a jiffy"
    )


def provision():
    def say(msg: str):
        sez(msg, ctx="(overall-provisioning) ")

    localdir = repo_root.localdir()

    say(f"Provisioning local directory {localdir}...")
    say("This involves downloading and extracting a few large tarballs:")
    say("    Clang+LLVM, opam/OCaml, a sysroot, and misc build tools like CMake.")
    say("This will take a few minutes...")

    provision_10j_deps_into(localdir)
    provision_10j_llvm_into(localdir)
    provision_cmake_into(localdir, version="3.31.7")
    provision_opam_into(localdir)


def download_and_extract_tarball(
    tarball_url: str, target_dir: Path, ctx: str, time_estimate="a few seconds"
) -> None:
    """
    Downloads a compressed tar file from the given URL and extracts it to the target directory.

    Args:
        tarball_url (str): URL of the tarball file to download
        target_dir (str): Directory to extract contents to.
    """

    def say(msg: str):
        sez(msg, ctx)

    say(f"This will take {time_estimate}...")

    try:
        # Create a temporary file name for the download
        temp_file = os.path.basename(urlparse(tarball_url).path)

        say(f"Downloading {tarball_url}...")
        # Download the file
        urllib.request.urlretrieve(tarball_url, temp_file)

        extract_tarball(Path(temp_file), target_dir, ctx, None)

        # Clean up the temporary file
        os.remove(temp_file)

        say(f"Download and extraction of {temp_file} completed successfully!")
    except Exception:
        # Clean up any temporary files if they exist
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise


# The extraction process is about twice as slow on macOS
# for clang+llvm versus the native bsdtar utility, but
# since this is a one-time cost it seems better to just
# avoid non-Python dependencies as much as we can.
def extract_tarball(
    tarball_path: Path, initial_target_dir: Path, ctx: str, time_estimate="a few seconds"
) -> Path:
    """
    Extracts the given tarball into (or within) the target directory.

    If the tarball unpacks a single directory with the same name as the tarball
    (minus the suffix), the contents of that directory will be moved up a level,
    and the empty directory will be removed.

    Returns the path to the directory that contains the unpacked contents.
    """

    def say(msg: str):
        sez(msg, ctx)

    def is_empty_dir(path: Path) -> bool:
        if not path.is_dir():
            return False

        is_empty = True
        for item in path.iterdir():
            is_empty = False
            break
        return is_empty

    def choose_target_dir(initial_target_dir: Path) -> tuple[Path, str]:
        # Check if the tarball unpacks a single directory with the same name as the tarball
        def select_tarball_suffix(filename: str) -> str:
            if filename.endswith(".tar.xz"):
                return ".tar.xz"
            elif filename.endswith(".tar.gz"):
                return ".tar.gz"
            elif filename.endswith(".tgz"):
                return ".tgz"
            elif filename.endswith(".tar.bz2"):
                return ".tar.bz2"
            raise ValueError(f"Unknown tarball suffix for URL: {filename}")

        suffix = select_tarball_suffix(tarball_path.name)
        tarball_basename = tarball_path.name.removesuffix(suffix)

        target_dir_preexisted = initial_target_dir.is_dir()
        if target_dir_preexisted and not is_empty_dir(initial_target_dir):
            # If the target directory already existed, and is not empty,
            # we'll unpack the tarball into a new directory inside it.

            final_target_dir = initial_target_dir / tarball_basename
            final_target_dir.mkdir(parents=True, exist_ok=True)
        else:
            final_target_dir = initial_target_dir

        return final_target_dir, tarball_basename

    if time_estimate is not None:
        say(f"This will take {time_estimate}...")

    final_target_dir, tarball_basename = choose_target_dir(initial_target_dir)

    if final_target_dir != initial_target_dir:
        say(f"Extracting to subdirectory {final_target_dir}...")
    else:
        say(f"Extracting to {initial_target_dir}...")

    # Create target/parent directory if it doesn't exist
    initial_target_dir.mkdir(parents=True, exist_ok=True)

    # Extract the compressed tar file
    with tarfile.open(str(tarball_path), "r:*") as tar:
        tar.extractall(path=final_target_dir)

    if time_estimate is not None:
        say(f"Extraction of {tarball_path.name} completed successfully!")

    # For example, we have foo-bar.tar.gz, and unpack it into blah/;
    #   then if we find blah/foo-bar/, we trim out the foo-bar part.

    if list(final_target_dir.iterdir()) == [final_target_dir / tarball_basename]:
        extracted_path = final_target_dir / tarball_basename
        # If the tarball unpacks a single directory, move its contents up a level
        for item in extracted_path.iterdir():
            shutil.move(str(item), str(final_target_dir))

        # Remove the now-empty directory
        extracted_path.rmdir()

    return final_target_dir
