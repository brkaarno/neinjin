from pathlib import Path
import platform
import os
import tarfile
import shutil
import subprocess
from urllib.parse import urlparse
from typing import Callable
import json
import enum
import sys
import textwrap

from packaging.version import Version
import click

import repo_root
import hermetic
from sha256sum import compute_sha256
from constants import WANT


class InstallationState(enum.Enum):
    NOT_INSTALLED = 0
    VERSION_OK = 1
    VERSION_TOO_OLD = 2


class CheckDepBy(enum.Enum):
    VERSION = 0
    SHA256 = 1


class TrackingWhatWeHave:
    def __init__(self):
        self.localdir = repo_root.localdir()
        try:
            with open(Path(self.localdir, "config.10j-HAVE.json"), "r", encoding="utf-8") as f:
                self._have = json.load(f)
        except OSError:
            self._have = {}

    def save(self):
        with open(Path(self.localdir, "config.10j-HAVE.json"), "w", encoding="utf-8") as f:
            json.dump(self._have, f, indent=2, sort_keys=True)

    def note_we_have(self, name: str, version: Version | None = None, sha256hex: str | None = None):
        match [version is None, sha256hex is None]:
            case [True, True]:
                raise ValueError(f"For '{name}' must provide either version or sha256hex")
            case [False, False]:
                raise ValueError(f"For '{name}' must provide either version or sha256hex, not both")
            case _:
                pass

        had = self._have.get(name)
        now = str(version) if version else sha256hex
        self._have[name] = now
        if had != now:
            self.save()

    def query(self, name: str) -> str | None:
        return self._have.get(name)

    def compatible(self, name: str, by: CheckDepBy) -> InstallationState:
        assert name in WANT
        wanted_spec: str = WANT[name]

        if name not in self._have:
            return InstallationState.NOT_INSTALLED

        match by:
            case CheckDepBy.VERSION:
                if Version(self._have[name]) >= Version(wanted_spec):
                    return InstallationState.VERSION_OK
            case CheckDepBy.SHA256:
                if self._have[name] == wanted_spec:
                    return InstallationState.VERSION_OK

        return InstallationState.VERSION_TOO_OLD


HAVE = TrackingWhatWeHave()


class ProvisioningError(Exception):
    pass


def sez(msg: str, ctx: str, err=False):
    click.echo("TENJIN SEZ: " + ctx + msg, err=err)


def download(url: str, filename: Path) -> None:
    # This import is relatively expensive (20 ms) and is rarely needed,
    # so it is imported here to avoid slowing down the common case.
    from urllib.request import urlretrieve  # noqa: PLC0415

    urlretrieve(url, filename)


# platform.system() in ["Linux", "Darwin"]
# platform.machine() in ["x86_64", "arm64"]


def provision_desires():
    require_rust_stuff()

    if HAVE.query("10j-dune") is None:

        def say(msg: str):
            sez(msg, ctx="(overall-provisioning) ")

        say(f"Provisioning local directory {HAVE.localdir}...")
        say("This involves downloading and extracting a few hundred megs of tarballs:")
        say("    Clang+LLVM, a sysroot, and misc build tools like CMake.")
        say("We'll also install Rust and OCaml, which will take a few minutes...")

    want_10j_deps()
    want_10j_llvm()
    want_cmake()
    want_dune()


def require_rust_stuff():
    def say(msg: str):
        sez(msg, ctx="(rust) ")

    # We don't run the installer ourselves because rustup pretty much requires
    # PATH modifications, and it's not our place to do that. In theory we could
    # have a hermetic copy of rustup + cargo etc but it seems silly because (A)
    # rustup is already hermetic enough, and (B) if someone is using Tenjin to
    # translate C to Rust, why on earth would they avoid having Rust installed?
    # Also one of TRACTOR's requirements is that translated Rust code works with
    # stable Rust, so pinning to a specific version of Rust would only result in
    # us not learning about bugs we really need to fix.
    #
    # HOWEVER: note that on a machine where only Tenjin's C compiler is available,
    # any cargo command that leads to compilation (cargo build, for many projects,
    # and also things like `rustup +nightly component add miri`, always) must be
    # run via 10j.
    def complain_about_tool_then_die(tool: str):
        say(f"{tool} is not installed, or is not available on your $PATH")
        match platform.system():
            case "Linux":
                say("Please install Rust using rustup (or via your package manager).")
            case "Darwin":
                say("Please install Rust using rustup (or via Homebrew).")
            case sysname:
                say(f"Tenjin doesn't yet support {sysname}, sorry!")
                sys.exit(1)

        download("https://sh.rustup.rs", "rustup-installer.sh")
        subprocess.check_call(["chmod", "+x", "rustup-installer.sh"])

        say("")
        say("For your convenience, I've downloaded the rustup installer script,")
        say("so you can just run")
        say("                  ./rustup-installer.sh")
        say("")
        say("It will interactively prompt you for the details of how and where")
        say("to install Rust. Most people choose the default options.")
        say("")
        say("Once you can run `cargo --version`,")
        say("   please re-run `10j provision`")
        sys.exit(1)

    if shutil.which("rustc") is None:
        complain_about_tool_then_die("Rust")
    if shutil.which("cargo") is None:
        complain_about_tool_then_die("cargo")
    if shutil.which("rustup") is None:
        complain_about_tool_then_die("rustup")


def want_generic(
    keyname: str,
    lowername: str,
    titlename: str,
    by: CheckDepBy,
    provisioner: Callable[[Path, str], None],
):
    match HAVE.compatible(keyname, by):
        case InstallationState.VERSION_OK:
            return
        case InstallationState.VERSION_TOO_OLD:
            sez(f"{titlename} version is outdated; re-provisioning...", ctx=f"({lowername}) ")
            provisioner(HAVE.localdir, version=WANT[keyname])
        case InstallationState.NOT_INSTALLED:
            provisioner(HAVE.localdir, version=WANT[keyname])


def want_version_generic(
    keyname: str, lowername: str, titlename: str, provisioner: Callable[[Path, str], None]
):
    want_generic(keyname, lowername, titlename, CheckDepBy.VERSION, provisioner)


def want_cmake():
    want_version_generic("10j-cmake", "cmake", "CMake", provision_cmake_into)
    out: bytes = hermetic.run_shell_cmd("cmake --version", check=True, capture_output=True).stdout
    outstr = out.decode("utf-8")
    lines = outstr.splitlines()
    if lines == []:
        raise ProvisioningError("CMake version command returned no output.")
    else:
        match lines[0].split():
            case ["cmake", "version", version]:
                HAVE.note_we_have("10j-cmake", version=Version(version))
            case _:
                raise ProvisioningError(f"Unexpected output from CMake version command:\n{outstr}")


def want_dune():
    want_version_generic("10j-dune", "dune", "Dune", provision_dune_into)


def want_opam():
    want_version_generic("10j-opam", "opam", "opam", provision_opam_into)


def want_ocaml():
    want_version_generic("10j-ocaml", "ocaml", "OCaml", provision_ocaml_into)


def want_10j_llvm():
    want_version_generic("10j-llvm", "llvm", "LLVM", provision_10j_llvm_into)
    out = subprocess.check_output([
        hermetic.xj_llvm_root(HAVE.localdir) / "bin" / "llvm-config",
        "--version",
    ])
    HAVE.note_we_have("10j-llvm", version=Version(out.decode("utf-8")))


def want_10j_deps():
    key = f"10j-build-deps_{platform.system()}-{platform.machine()}"
    want_generic(
        key,
        "10j-build-deps",
        "Tenjin build deps",
        CheckDepBy.SHA256,
        provision_10j_deps_into,
    )
    HAVE.note_we_have(key, sha256hex=WANT[key])


# Prerequisite: opam provisioned.
def grab_opam_version_str() -> str:
    cp = hermetic.run_opam(["--version"], check=True, capture_output=True)
    return cp.stdout.decode("utf-8")


# Prerequisite: opam and ocaml provisioned.
def grab_ocaml_version_str() -> str:
    cp = hermetic.run_opam(["exec", "--", "ocamlc", "--version"], check=True, capture_output=True)
    return cp.stdout.decode("utf-8")


# Prerequisite: opam and dune provisioned.
def grab_dune_version_str() -> str:
    cp = hermetic.run_opam(["exec", "--", "dune", "--version"], check=True, capture_output=True)
    return cp.stdout.decode("utf-8")


def provision_ocaml_into(_localdir: Path, version: str):
    provision_ocaml(version)

    click.echo("opam env (no eval env):")
    hermetic.run_opam(
        [
            "env",
        ],
        eval_opam_env=False,
        check=False,
    )
    hermetic.run_opam(["config", "report"], eval_opam_env=False, check=False)
    click.echo("opam env (w/ eval env):")
    hermetic.run_opam(
        [
            "env",
        ],
        eval_opam_env=True,
        check=False,
    )
    hermetic.run_opam(["config", "report"], eval_opam_env=True, check=False)
    click.echo("opam exec ocaml --version (no env):")
    hermetic.run_opam(["exec", "--", "ocaml", "--version"], eval_opam_env=False, check=False)
    click.echo("opam exec ocaml --version (w/ env):")
    hermetic.run_opam(["exec", "--", "ocaml", "--version"], eval_opam_env=True, check=False)
    HAVE.note_we_have("10j-ocaml", version=Version(grab_ocaml_version_str()))


def provision_ocaml(ocaml_version: str):
    want_opam()

    def say(msg: str):
        sez(msg, ctx="(ocaml) ")

    TENJIN_SWITCH = "tenjin"

    def install_ocaml(localdir: Path):
        if not hermetic.opam_non_hermetic():
            # For hermetic installations, we will simply bulldoze the existing
            # opam root and start fresh. For non-hermetic installations, we'll
            # try to reuse what's already there.
            opamroot = localdir / "opamroot"
            if opamroot.is_dir():
                shutil.rmtree(opamroot)

        # Bubblewrap does not work inside Docker containers, at least not without
        # heinous workarounds, if we're in Docker then we don't really need it anyway.
        # So we'll try running a trivial command with it; if it fails, we'll tell opam
        # not to use it.
        try:
            sandboxing_arg = []
            subprocess.check_call([
                hermetic.xj_build_deps(localdir) / "bin" / "bwrap",
                "--",
                "true",
            ])
        except subprocess.CalledProcessError:
            say("Oh! No working bubblewrap. We're in Docker, maybe? Disabling it...")
            sandboxing_arg = ["--disable-sandboxing"]

        cp = hermetic.run_opam(["config", "report"], eval_opam_env=False, capture_output=True)
        if b"please run `opam init'" in cp.stderr:
            say("================================================================")
            say("Initializing opam; this will take about half a minute...")
            say("      (subsequent output comes from `opam init --bare`)")
            say("----------------------------------------------------------------")
            say("")
            hermetic.check_call_opam(
                ["init", "--bare", "--no-setup", "--disable-completion", *sandboxing_arg],
                eval_opam_env=False,
            )

        cp = hermetic.run_opam(
            ["switch", "list"], eval_opam_env=False, check=True, capture_output=True
        )
        if TENJIN_SWITCH in cp.stdout.decode("utf-8"):
            if grab_ocaml_version_str() == ocaml_version:
                say("================================================================")
                say("Reusing cached OCaml, saving a few minutes of compiling...")
                say("----------------------------------------------------------------")
                return
            else:
                say("================================================================")
                say("Removing cached OCaml switch due to version mismatch...")
                say("----------------------------------------------------------------")
                hermetic.check_call_opam(["switch", "remove", TENJIN_SWITCH], eval_opam_env=False)

        say("")
        say("================================================================")
        say("Installing OCaml; this will take four-ish minutes to compile...")
        say("      (subsequent output comes from `opam switch create`)")
        say("----------------------------------------------------------------")

        hermetic.check_call_opam(
            ["switch", "create", TENJIN_SWITCH, ocaml_version, "--no-switch"],
            eval_opam_env=False,
            env_ext={
                "OPAMNOENVNOTICE": "1",
                "CC": str(hermetic.xj_llvm_root(localdir) / "bin" / "clang"),
                "CXX": str(hermetic.xj_llvm_root(localdir) / "bin" / "clang++"),
            },
        )

    install_ocaml(HAVE.localdir)


def provision_debian_bullseye_sysroot_into(target_arch: str, dest_sysroot: Path):
    def say(msg: str):
        sez(msg, ctx="(sysroot) ")

    say("Downloading and unpacking sysroot tarball...")

    CHROME_LINUX_SYSROOT_URL = "https://commondatastorage.googleapis.com/chrome-linux-sysroot"

    # These don't go in WANT because they're quite stable;
    # we don't expect to need a new version, ever.
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

    download(url, tarball)
    sha256sum = compute_sha256(tarball)
    if sha256sum != tarball_sha256sum:
        raise ProvisioningError("Sysroot hash verification failed!")
    shutil.unpack_archive(tarball, dest_sysroot, filter="tar")
    tarball.unlink()


def provision_opam_binary_into(opam_version: str, localdir: Path) -> None:
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
            say(f"Symlinking to a suitable version of opam at {sys_opam}")
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

    if hermetic.running_in_ci():
        dotlocalbin = Path.home() / ".local" / "bin"
        if str(dotlocalbin) in os.environ["PATH"]:
            if not dotlocalbin.is_dir():
                dotlocalbin.mkdir(parents=True)

            # XREF:ci-opam-paths
            # We are in CI, but didn't have opam on the path already.
            # Install it where (A) nrsr.yaml will cache it and (B) it'll be on PATH.
            # Then our next CI run will be faster.
            shutil.copy(Path(localdir, "opam"), dotlocalbin / "opam")
        else:
            click.echo("WARNING: ~/.local/bin not on PATH anymore?!? OCaml cache won't work.")


def provision_dune_into(_localdir: Path, version: str):
    provision_dune(version)

    HAVE.note_we_have("10j-dune", version=Version(grab_dune_version_str()))


# Precondition: not installed, or version too old.
def provision_dune(dune_version: str):
    want_ocaml()

    def say(msg: str):
        sez(msg, ctx="(opam) ")

    cp = hermetic.run_opam(["exec", "--", "dune", "--version"], check=False, capture_output=True)
    if cp.returncode == 0:
        actual_version = Version(cp.stdout.decode("utf-8"))
        if actual_version >= Version(dune_version):
            # We only get here when the HAVE cache is incorrect: it thinks dune
            # is not installed or is out of date, but dune is in fact installed
            # with a new enough version.
            say(f"Dune {actual_version} is already installed.")
            return

        say(f"Found dune version {actual_version}, but we need {dune_version}.")

    say("")
    say("================================================================")
    say("Installing Dune; this will take a minute to compile...")
    say("      (subsequent output comes from `opam install dune`)")
    say("----------------------------------------------------------------")
    hermetic.check_call_opam(["install", f"dune.{dune_version}"])


def provision_opam_into(localdir: Path, version: str):
    def say(msg: str):
        sez(msg, ctx="(opam) ")

    provision_opam_binary_into(version, localdir)

    opam_version_seen = grab_opam_version_str()
    say(f"opam version: {opam_version_seen}")
    HAVE.note_we_have("10j-opam", version=Version(opam_version_seen))


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

    download_and_extract_tarball(
        mk_url(), localdir / "cmake", ctx="(cmake) ", time_estimate="a minute"
    )


def provision_10j_llvm_into(localdir: Path, version: str):
    tarball_name = f"LLVM-{version}-Linux-x86_64.tar.xz"
    if Path(tarball_name).is_file():
        extract_tarball(
            Path(tarball_name),
            hermetic.xj_llvm_root(localdir),
            ctx="(llvm) ",
            time_estimate="twenty seconds or so",
        )
    else:
        url = f"https://images.aarno-labs.com/amp/ben/{tarball_name}"
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
            f.write(
                textwrap.dedent(f"""\
                    --sysroot <CFGDIR>/../{sysroot_name}

                    # This one's unfortunate. LLD defaults to --no-allow-shlib-undefined
                    # but the libgcc_s.so.1 shipped with Ubuntu 22.04 has an undefined
                    # symbol for _dl_find_object@GLIBC_2.35, and it seems like OCaml
                    # explicitly links against the system library, via -L, rather than
                    #  letting the compiler find it automatically in the sysroot.
                    -Wl,--allow-shlib-undefined
                    """)
            )

    # Add symbolic links for the binutils-alike tools.
    # Tools not provided by LLVM: ranlib, size
    binutils_names = ["ar", "as", "nm", "objcopy", "objdump", "readelf", "strings", "strip"]
    for name in binutils_names:
        src = hermetic.xj_llvm_root(localdir) / "bin" / f"llvm-{name}"
        dst = hermetic.xj_llvm_root(localdir) / "bin" / f"{name}"
        if not dst.is_symlink():
            os.symlink(src, dst)

    # These symbolic links follow a different naming pattern.
    for src, dst in [("clang", "cc"), ("clang++", "c++"), ("lld", "ld")]:
        src = hermetic.xj_llvm_root(localdir) / "bin" / src
        dst = hermetic.xj_llvm_root(localdir) / "bin" / dst
        if not dst.is_symlink():
            os.symlink(src, dst)

    #                   COMMENTARY(goblint-cil-gcc-wrapper)
    # Okay, this one is unfortunate. We generally only care about software that
    # builds with Clang. But CodeHawk depends on goblint-cil, which uses C code
    # in its config step that has GCC extensions which Clang doesn't support, &
    # thus goblint-cil looks specifically for a GCC binary. So what we're gonna
    # do here is write out a wrapper script for goblint-cil to find, which will
    # intercept the GCC-specific stuff in the code it compiles and patch it out
    # before passing it on to Clang. Hurk!
    sadness = hermetic.xj_llvm_root(localdir) / "goblint-sadness"
    sadness.mkdir(exist_ok=True)
    gcc_wrapper_path = sadness / "gcc"
    with open(gcc_wrapper_path, "w", encoding="utf-8") as f:
        f.write(
            textwrap.dedent("""\
                #!/bin/sh

                # See COMMENTARY(goblint-cil-gcc-wrapper) in cli/provisioning.py

                if [ "$1" = "--version" ]; then
                    echo "gcc (GCC) 7.999.999"
                elif [ "$*" = "-D_GNUCC machdep-ml.c -o machdep-ml.exe" ]; then

                    CFILE=machdep-ml-clangcompat.c
                    # Remove references to Clang-unsupported type _Float128.
                    cat machdep-ml.c \
                            | sed 's/_Float128 _Complex/struct { char _[32]; }/g' \
                            | sed 's/_Float128/struct { char _[16]; }/g' > "$CFILE" || {
                        rm -f "$CFILE"
                        exit 1
                    }

                    exec clang -D_GNUCC "$CFILE" -o machdep-ml.exe
                    rm -f "$CFILE"
                else
                    exec clang "$@"
                fi
                """)
        )
    gcc_wrapper_path.chmod(0o755)


def provision_10j_deps_into(localdir: Path, version: str):
    assert platform.system() == "Linux"
    assert platform.machine() == "x86_64"

    url = "https://images.aarno-labs.com/amp/ben/xj-build-deps_linux-x86_64.tar.xz"
    download_and_extract_tarball(
        url,
        hermetic.xj_build_deps(localdir),
        ctx="(builddeps) ",
        time_estimate="a jiffy",
        required_sha256sum=version,
    )

    cook_pkg_config_within(localdir)


#                COMMENTARY(pkg-config-paths)
# pkg-config embeds various configured paths into the binary.
# In particular, it embeds, via compiler flags during compilation,
# LIBDIR (via --prefix), PKG_CONFIG_PC_PATH, PKG_CONFIG_SYSTEM_INCLUDE_PATH,
# and PKG_CONFIG_SYSTEM_LIBRARY_PATH.
#
# So what we do, and ugh this leaves me feeling a little queasy, hurk, is...
# we embed very large fake paths into the pkg-config binary, and we call that
# binary "uncooked". Then we make a copy of the binary, and make in-place edits
# to have the embedded paths match those on the user's system. (The fake paths
# are large enough that they should accommodate whatever path the user has.)
def cook_pkg_config_within(localdir: Path):
    def say(msg: str):
        sez(msg, ctx="(pkg-config) ")

    fifty = b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    rilly = b"thisverylongpathistogiveusroomtooverwriteitlaterok"
    twohundredfifty = fifty + fifty + fifty + fifty + rilly
    path_of_unusual_size = b"/tmp/" + twohundredfifty + b"/" + twohundredfifty
    libdir = path_of_unusual_size + b"/prefix/lib"
    sysinc = path_of_unusual_size + b"/sysinc:/usr/include"
    syslib = path_of_unusual_size + b"/syslib:/usr/lib:/lib"
    pcpath = path_of_unusual_size + b"/lib/pkgconfig:" + path_of_unusual_size + b"/share/pkgconfig"
    nullbyte = b"\0"

    bindir = hermetic.xj_build_deps(localdir) / "bin"

    uncooked = bindir / "pkg-config.uncooked"
    assert uncooked.is_file()
    cooked = bindir / "pkg-config"
    shutil.copy(uncooked, cooked)

    def replace_null_terminated_needle_in(haystack: bytes, needle: bytes, newstuff: bytes) -> bytes:
        # Make sure is has the embedded path/data we are expecting it to have.
        assert (needle + nullbyte) in haystack

        assert len(newstuff) <= len(needle)
        if len(newstuff) < len(needle):
            # Pad the newstuff with null bytes to match the length of needle.
            newstuff += b"\0" * (len(needle) - len(newstuff))

        assert len(newstuff) == len(needle)
        return haystack.replace(needle, newstuff)

    say("Cooking pkg-config...")
    with open(cooked, "r+b") as f:
        # Read the file into memory
        data = f.read()

        sysroot_usr = hermetic.xj_llvm_root(localdir) / "sysroot" / "usr"
        newpcpath_lib = sysroot_usr / "lib" / "pkgconfig"
        newpcpath_shr = sysroot_usr / "share" / "pkgconfig"

        # Replace the placeholder strings with the actual paths.
        # Note that we set up the paths to include pkg-config's standard paths as backups,
        # in case the user is trying to compile against a library that isn't in the sysroot.
        data = replace_null_terminated_needle_in(
            data,
            sysinc,
            bytes(sysroot_usr / "include") + b":/usr/include",
        )
        data = replace_null_terminated_needle_in(
            data,
            syslib,
            bytes(sysroot_usr / "lib") + b":/usr/lib:/lib",
        )
        data = replace_null_terminated_needle_in(
            data, pcpath, bytes(newpcpath_lib) + b":" + bytes(newpcpath_shr) + b":/usr/lib:/lib"
        )
        data = replace_null_terminated_needle_in(
            data, libdir, bytes(hermetic.xj_build_deps(localdir) / "lib")
        )

        # Write the modified data back to the file
        f.seek(0)
        f.write(data)
        f.truncate()
    say("... done cooking pkg-config.")

    assert path_of_unusual_size not in data, "Oops, pkg-config was left undercooked!"


def download_and_extract_tarball(
    tarball_url: str,
    target_dir: Path,
    ctx: str,
    time_estimate="a few seconds",
    required_sha256sum: str | None = None,
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
        download(tarball_url, temp_file)
    except Exception:
        # Clean up any temporary files if they exist
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise

    if required_sha256sum is not None:
        # Verify the SHA256 checksum
        sha256sum = compute_sha256(temp_file)
        if sha256sum != required_sha256sum:
            raise ProvisioningError(f"SHA256 checksum verification failed for {temp_file}!")

    extract_tarball(Path(temp_file), target_dir, ctx, None)

    # Clean up the temporary file
    os.remove(temp_file)

    say(f"Download and extraction of {temp_file} completed successfully!")


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
