"""pytest 全局夹具：内存后端、受控工作区与测试配置。"""

from __future__ import annotations

import pytest

from capsule_system.storage.memory_backend import InMemoryBackend
from capsule_system.temporary.workspace import TaskWorkspace

from tests.helpers import make_config


@pytest.fixture()
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture()
def backend(config):
    """进程内内存后端：不落盘，测试间完全隔离。"""
    return InMemoryBackend()


@pytest.fixture()
def workspace(config) -> TaskWorkspace:
    return TaskWorkspace(config.workspace_dir)
