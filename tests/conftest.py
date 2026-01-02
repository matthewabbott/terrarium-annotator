"""Shared pytest fixtures for terrarium-annotator tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from terrarium_annotator.agent_client import AgentClient


@pytest.fixture(scope="session")
def agent_available() -> bool:
    """Check if terrarium-agent is running on port 8080.

    This fixture runs once per test session and caches the result.
    Used by integration tests to skip gracefully when agent unavailable.
    """
    client = AgentClient(base_url="http://localhost:8080", timeout=5)
    return client.health_check()


@pytest.fixture
def real_agent(agent_available: bool) -> AgentClient:
    """Get real AgentClient, skip if agent unavailable.

    Yields:
        AgentClient connected to localhost:8080 with 120s timeout.

    Skips:
        If terrarium-agent is not running.
    """
    if not agent_available:
        pytest.skip("terrarium-agent not running on localhost:8080")
    return AgentClient(base_url="http://localhost:8080", timeout=120)


@pytest.fixture
def temp_db_path() -> Path:
    """Create a temporary database file path.

    Returns:
        Path to a temporary .db file (file created but empty).
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)
