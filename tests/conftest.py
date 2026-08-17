import re
import shutil
import subprocess
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / 'fixtures'
GOLDEN_DIR = Path(__file__).parent / 'golden'

_ISO_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def run_cofr(args, cwd=None, stdin=None, **kwargs):
    '''Shared subprocess runner for the `cofr` CLI.

    Accepts the union of every test's needs: positional args list, optional cwd,
    optional stdin (aliased to subprocess `input=`), and any kwargs subprocess.run
    accepts. Defaults capture_output=True, text=True, env=None.
    '''
    if stdin is not None and 'input' not in kwargs:
        kwargs['input'] = stdin
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    kwargs.setdefault('env', None)
    if cwd is not None:
        kwargs.setdefault('cwd', cwd)
    return subprocess.run(['cofr', *args], **kwargs)


def normalize_for_golden(text, project_path=None):
    '''Redact ISO 8601 timestamps and absolute project paths for stable comparison.

    Replaces the resolved (longer) path first so a /private symlink prefix
    doesn't survive after the shorter str(project_path) replace consumes its tail.
    '''
    out = _ISO_RE.sub('<TIMESTAMP>', text)
    if project_path is not None:
        resolved = str(Path(project_path).resolve())
        if resolved != str(project_path):
            out = out.replace(resolved, '<PROJECT_PATH>')
        out = out.replace(str(project_path), '<PROJECT_PATH>')
    return out


@pytest.fixture
def clean_project_path(tmp_path):
    dest = tmp_path / 'clean_project'
    shutil.copytree(FIXTURES_DIR / 'clean_project', dest)
    return dest


@pytest.fixture
def error_project_path(tmp_path):
    dest = tmp_path / 'error_project'
    shutil.copytree(FIXTURES_DIR / 'error_project', dest)
    return dest


@pytest.fixture
def broken_refs_path(tmp_path):
    dest = tmp_path / 'broken_refs'
    shutil.copytree(FIXTURES_DIR / 'broken_refs', dest)
    return dest


@pytest.fixture
def contradictions_project_path(tmp_path):
    dest = tmp_path / 'contradictions_project'
    shutil.copytree(FIXTURES_DIR / 'contradictions_project', dest)
    return dest
