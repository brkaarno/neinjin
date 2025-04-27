import subprocess
import shutil
import time
from pathlib import Path
import os

import click

import repo_root


def check_call_uv(args: list[str]):
    # The args here should be kept in sync with the 10j script.
    localdir = repo_root.localdir()
    subprocess.check_call([localdir / "uv", "--config-file", localdir / "uv.toml", *args])


def xj_build_deps(localdir: Path) -> Path:
    return localdir / "xj-build-deps"


def xj_llvm_root(localdir: Path) -> Path:
    return localdir / "xj-llvm"


def mk_env_for(localdir: Path, with_tenjin_deps=True, env_ext=None, **kwargs) -> dict[str, str]:
    if "env" in kwargs:
        env = kwargs["env"]
        del kwargs["env"]  # we'll pass it explicitly, so not via kwargs
    else:
        env = os.environ.copy()

    if env_ext is not None:
        env = {**env, **env_ext}

    if with_tenjin_deps:
        env["PATH"] = os.pathsep.join([
            str(xj_build_deps(localdir) / "bin"),
            str(xj_llvm_root(localdir) / "bin"),
            env["PATH"],
        ])

    return env


def run_command_with_progress(command, stdout_file, stderr_file, shell=False) -> None:
    """
    Run a command, redirecting stdout/stderr to files, and print dots while waiting.
    """
    with open(stdout_file, "wb") as out_f, open(stderr_file, "wb") as err_f:
        proc = subprocess.Popen(
            command,
            stdout=out_f,
            stderr=err_f,
            bufsize=1,
            shell=shell,
            env=mk_env_for(repo_root.localdir(), with_tenjin_deps=True, env_ext=None),
        )

        while proc.poll() is None:
            # Process is still running
            print(".", end="", flush=True)
            time.sleep(0.3)

        # Final newline after progress dots
        print()
        assert proc.returncode == 0, f"Command failed with return code {proc.returncode}"


def run_shell_cmd(
    cmd: str | list[object], check=False, with_tenjin_deps=True, env_ext=None, **kwargs
) -> subprocess.CompletedProcess:
    if not isinstance(cmd, str):
        cmd = " ".join(str(x) for x in cmd)

    return subprocess.run(
        cmd, check=check, shell=True, env=mk_env_for(repo_root.localdir()), **kwargs
    )


def opamroot(localdir: Path) -> Path:
    return localdir / "opamroot"


def opam_non_hermetic() -> bool:
    running_in_ci = "GITHUB_WORKSPACE" in os.environ
    return running_in_ci and shutil.which("opam") is not None


def run_opam(
    args: list[str], eval_opam_env=True, with_tenjin_deps=True, check=False, env_ext=None, **kwargs
) -> subprocess.CompletedProcess:
    localdir = repo_root.localdir()
    localopam = localdir / "opam"

    def insert_opam_subcmd_args(args: list[str], subcmd_args: list[str]) -> list[str]:
        match args:
            case []:
                return subcmd_args
            case [subcmd, *rest]:
                # If args is something like ["exec", "--", "dune"], we need to make
                # sure the subcmd args come before the double dash, otherwise we'll
                # pass them to `dune` instead of `opam exec`!
                return [subcmd, *subcmd_args, *rest]

    def mk_shell_cmd() -> str:
        def shell_cmd(parts: list[str]) -> str:
            return " ".join(str(x) for x in parts)

        hermetic = not opam_non_hermetic()

        opam_subcmd_args = ["--cli=2.3"]
        if hermetic:
            opam_subcmd_args += ["--root", opamroot(localdir)]
        else:
            # We save about four minutes per run in CI by using the system opam.
            # If it appears to be installed, use it.
            pass

        maincmd = shell_cmd([localopam, *insert_opam_subcmd_args(args, opam_subcmd_args)])

        if eval_opam_env:
            opam_env_cmd = f"{localopam} env {shell_cmd(opam_subcmd_args)}"
            # If we're using the system opam, we use the default switch.
            if hermetic:
                opam_env_cmd += " --switch=tenjin --set-switch --set-root"

            return f"eval $({opam_env_cmd}) && {maincmd}"
        else:
            return maincmd

    return run_shell_cmd(mk_shell_cmd(), check, with_tenjin_deps, env_ext, **kwargs)


def check_call_opam(
    args: list[str], eval_opam_env=True, with_tenjin_deps=True, **kwargs
) -> subprocess.CompletedProcess:
    cp = run_opam(args, eval_opam_env, with_tenjin_deps, check=False, **kwargs)
    if cp.stderr:
        click.echo(cp.stderr, err=True)
    cp.check_returncode()
    return cp


def run_output_git(args: list[str], check=False) -> bytes:
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
