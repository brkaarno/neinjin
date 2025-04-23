import subprocess
from pathlib import Path

import click

import repo_root


def check_call_uv(args: list[str]):
    # The args here should be kept in sync with the 10j script.
    localdir = repo_root.localdir()
    subprocess.check_call([localdir / "uv", "--config-file", localdir / "uv.toml", *args])


def opamroot(localdir: Path) -> Path:
    return localdir / "opamroot"


def run_opam(args: list[str], eval_env=True, check=False, **kwargs) -> subprocess.CompletedProcess:
    localdir = repo_root.localdir()
    localopam = localdir / "opam"
    if not eval_env:
        # When running command like opam init, there is no env to source!
        return subprocess.run(
            [localopam, *args, "--cli=2.3", "--root", opamroot(localdir)],
            check=check,
            shell=False,
            **kwargs,
        )
    else:
        # Once the root is set up, most subcommands need to run with a suite of
        # env vars configured by $(opam env).
        opam_env_cmd = (
            f"{localopam} env  --cli=2.3 --root {opamroot(localdir)} "
            + "--switch=tenjin --set-switch --set-root"
        )
        maincmd = " ".join(
            str(x)
            for x in [
                localopam,
                *args,
                "--cli=2.3",
                "--switch",
                "tenjin",
                "--root",
                opamroot(localdir),
            ]
        )

        return subprocess.run(
            f"eval $({opam_env_cmd}) && {maincmd}",
            check=check,
            shell=True,
            **kwargs,
        )


def check_call_opam(args: list[str], eval_env=True, **kwargs) -> subprocess.CompletedProcess:
    cp = run_opam(args, eval_env, check=False, **kwargs)
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
