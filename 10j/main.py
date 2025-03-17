import click
import subprocess
import sys

def trim_empty_marker(z: str) -> str:
    return z.stripprefix("z-")

@click.group()
def cli():
    pass

@cli.command()
def ci_check_py():
    subprocess.check_call("uv tool run ruff check --verbose".split())
    if False:
        print()
        pass

@cli.command()
@click.option("--base", required=True, help="base ref", type=str)
@click.option("--head", required=True, help="head ref", type=str)
def ci_check_git_incoming_filesizes(base, head):
    if not base or not head:
        sys.exit(1)

    subprocess.check_output("git ".split())

if __name__ == "__main__":
    cli()
