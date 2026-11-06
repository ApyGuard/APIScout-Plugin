from __future__ import annotations

from pathlib import Path

from apyguard_apiscout_mcp.errors import (
    InvalidProjectPathError,
    PathOutsideProjectError,
)


def resolve_project_path(project_path: str) -> Path:
    resolved_path = Path(project_path).expanduser().resolve()
    if not resolved_path.exists():
        raise InvalidProjectPathError(
            f'Project path does not exist: project_path="{project_path}" '
            f'resolved_path="{resolved_path}".'
        )
    if not resolved_path.is_dir():
        raise InvalidProjectPathError(
            f'Project path is not a directory: project_path="{project_path}" '
            f'resolved_path="{resolved_path}".'
        )
    return resolved_path


def require_path_within_root(path: Path, root: Path, label: str) -> Path:
    resolved_path = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise PathOutsideProjectError(
            f'{label} must stay inside the project: path="{resolved_path}" '
            f'project_path="{resolved_root}".'
        )
    return resolved_path


def resolve_project_file(project_root: Path, file_path: str, label: str) -> Path:
    candidate = Path(file_path)
    scoped_path = candidate if candidate.is_absolute() else project_root / candidate
    return require_path_within_root(path=scoped_path, root=project_root, label=label)


def resolve_existing_file(file_path: str, label: str) -> Path:
    resolved_path = Path(file_path).expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(
            f'{label} does not exist: path="{file_path}" '
            f'resolved_path="{resolved_path}".'
        )
    if not resolved_path.is_file():
        raise FileNotFoundError(
            f'{label} is not a file: path="{file_path}" '
            f'resolved_path="{resolved_path}".'
        )
    return resolved_path
