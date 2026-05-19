from __future__ import annotations

from typing import Any, Dict


def list_projects_use_case(project_repository) -> list[Dict[str, Any]]:
    return project_repository.list()


def create_project_use_case(project_repository, project_data: Dict[str, Any]) -> Dict[str, Any]:
    return project_repository.create(project_data)


def get_project_use_case(project_repository, project_id: str) -> Dict[str, Any] | None:
    return project_repository.get(project_id)


def update_project_use_case(
    project_repository,
    project_id: str,
    project_data: Dict[str, Any],
) -> Dict[str, Any] | None:
    return project_repository.update(project_id, project_data)


def delete_project_use_case(project_repository, project_id: str) -> Dict[str, Any] | None:
    return project_repository.delete(project_id)


def apply_parsed_project_data_use_case(
    project_repository,
    project_id: str,
    parsed_data: Dict[str, Any],
) -> Dict[str, Any] | None:
    return project_repository.apply_parsed_data(project_id, parsed_data)
