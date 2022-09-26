import subprocess
import sys

def run(*cmd):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


def fmt():
    run("black", "cratere", "tests")


def check():
    run("mypy", "cratere", "tests")
    run("pytest", "tests")
    run("black", "--check", "--diff", "cratere", "tests")
