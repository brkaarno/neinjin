import subprocess
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


def opamroot(localdir: Path) -> Path:
    return localdir / "opamroot"


def run_opam(
    args: list[str], eval_opam_env=True, with_tenjin_deps=True, check=False, env_ext=None, **kwargs
) -> subprocess.CompletedProcess:
    localdir = repo_root.localdir()
    localopam = localdir / "opam"

    def mk_shell_cmd():
        def shell_cmd(parts: list[str]) -> str:
            return " ".join(str(x) for x in parts)

        maincmd = shell_cmd([localopam, *args, "--cli=2.3", "--root", opamroot(localdir)])

        if eval_opam_env:
            opam_env_cmd = (
                f"{localopam} env  --cli=2.3 --root {opamroot(localdir)} "
                + "--switch=tenjin --set-switch --set-root"
            )
            return f"eval $({opam_env_cmd}) && {maincmd}"
        return maincmd

    def mk_env():
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

    return subprocess.run(
        mk_shell_cmd(),
        check=check,
        shell=True,
        env=mk_env(),
        **kwargs,
    )


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
