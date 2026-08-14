"""Base safety test for all AI Assistant tools.

Every tool must pass 4 safety tests:
1. Read-only: Handler must not call any write method
   (CONTEXT.md § "Nội bộ AI Assistant", entry **Tool**).
2. Scope: Tool must respect data scope — org-wide for HR, employee-scoped for Employee.
3. Missing entity: Tool must return clear error (not crash) when entity not found.
4. Invalid input: Tool must handle invalid UUIDs, missing params, etc.

Usage:
    from tests.modules.assistant.test_base_safety import BaseToolSafetyTest

    class TestMyToolSafety(BaseToolSafetyTest):
        TOOL_NAME = "my_tool"
        HANDLER_CLASS = ToolRegistry
        HANDLER_METHOD = "_my_tool_handler"
        HAS_ENTITY_LOOKUP = True
        ENTITY_ID_PARAM = "my_entity_id"

        @pytest.fixture
        def registry(self, ...) -> ...:
            ...

        @pytest.fixture
        def valid_args(self) -> dict:
            return {"my_entity_id": str(uuid4())}

        @pytest.mark.asyncio
        async def test_tool_respects_scope(self, registry):
            ...

        @pytest.mark.asyncio
        async def test_tool_handles_missing_entity(self, registry):
            ...

        @pytest.mark.asyncio
        async def test_tool_handles_invalid_input(self, registry):
            ...
"""

from __future__ import annotations

import inspect
import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import pytest


class BaseToolSafetyTest(ABC):
    """Abstract base class for tool safety tests.

    Subclasses MUST define:
        TOOL_NAME:       The tool name (matches ToolDefinition.name).
        HANDLER_CLASS:   The registry class (ToolRegistry or EmployeeToolRegistry).
        HANDLER_METHOD:  The private handler method name (e.g. '_count_candidates_by_status').

    Subclasses SHOULD define:
        HAS_ENTITY_LOOKUP:  True if the tool looks up a database entity (default True).
        ENTITY_ID_PARAM:    Parameter name for the entity ID (e.g. 'candidate_id').
        NO_PARAMS:          True if tool accepts zero parameters (default False).

    Subclasses MUST override fixtures:
        registry     — returns a configured registry with mocked dependencies.
        valid_args   — returns valid arguments dict for the tool.

    Subclasses MUST implement:
        test_tool_respects_scope
        test_tool_handles_missing_entity  (can be a no-op if HAS_ENTITY_LOOKUP=False)
        test_tool_handles_invalid_input
    """

    # --- Configuration (override in subclass) ---
    TOOL_NAME: ClassVar[str]
    HANDLER_CLASS: ClassVar[type]
    HANDLER_METHOD: ClassVar[str]
    HAS_ENTITY_LOOKUP: ClassVar[bool] = True
    ENTITY_ID_PARAM: ClassVar[str | None] = None
    NO_PARAMS: ClassVar[bool] = False

    # --- Fixtures (override in subclass) ---

    @pytest.fixture
    def registry(self) -> Any:
        """Return a configured registry with mocked dependencies."""
        raise NotImplementedError

    @pytest.fixture
    def valid_args(self) -> dict[str, Any]:
        """Return valid arguments for the tool."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Test 1: Read-only — structural safety (CONTEXT.md § "Nội bộ AI Assistant")
    # ------------------------------------------------------------------

    def test_tool_is_read_only(self) -> None:
        """Verify the handler source code contains NO write method calls.

        Uses static source inspection (not runtime mocking) so this catches
        structural violations before they can ever execute.
        """
        source = inspect.getsource(self.HANDLER_CLASS)

        handler_start = source.find(f"async def {self.HANDLER_METHOD}")
        assert handler_start != -1, (
            f"Handler '{self.HANDLER_METHOD}' not found in {self.HANDLER_CLASS.__name__}"
        )

        # Find next method boundary
        next_method = source.find("\n    async def ", handler_start + 1)
        if next_method == -1:
            next_method = source.find("\n    def ", handler_start + 1)
        if next_method == -1:
            next_method = len(source)

        handler_section = source[handler_start:next_method]

        forbidden = [
            "session.commit(",
            "session.add(",
            "session.flush(",
            "session.delete(",
            ".create(",
            ".update(",
            ".delete(",
            ".soft_delete(",
            ".upsert(",
            ".save(",
        ]

        for pattern in forbidden:
            lines = [ln.strip() for ln in handler_section.split("\n") if pattern in ln]
            # Allow SELECT statements that happen to use .execute()
            lines = [ln for ln in lines if not ("select" in ln.lower() and "execute" in ln.lower())]
            assert not lines, (
                f"Handler '{self.HANDLER_METHOD}' in {self.HANDLER_CLASS.__name__} "
                f"contains forbidden write pattern '{pattern}': {lines}"
            )

    # ------------------------------------------------------------------
    # Test 2: Data scope — org-wide vs employee-scoped
    # ------------------------------------------------------------------

    @abstractmethod
    @pytest.mark.asyncio
    async def test_tool_respects_scope(self, registry: Any) -> None:
        """Verify tool respects its data scope boundary."""
        ...

    # ------------------------------------------------------------------
    # Test 3: Missing entity — clear error, not crash
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_tool_handles_missing_entity(self, registry: Any) -> None:
        """Verify tool returns a clear error (not crash) when entity not found.

        Subclasses with entity lookup MUST override this.
        Subclasses without entity lookup can skip by setting HAS_ENTITY_LOOKUP=False;
        the default implementation checks input validation instead.
        """
        if not self.HAS_ENTITY_LOOKUP:
            pytest.skip(f"{self.TOOL_NAME} has no entity lookup — test skipped")
        # Subclasses must override
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Test 4: Invalid input — graceful handling
    # ------------------------------------------------------------------

    @abstractmethod
    @pytest.mark.asyncio
    async def test_tool_handles_invalid_input(self, registry: Any) -> None:
        """Verify tool handles invalid input gracefully."""
        ...

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    async def execute_tool(self, registry: Any, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool and return parsed JSON result."""
        result_json = await registry.execute(self.TOOL_NAME, args)
        # Some registries return JSON string, handle both
        if isinstance(result_json, str):
            return json.loads(result_json)
        return result_json

    def assert_error(self, result: dict[str, Any], key: str = "error") -> None:
        """Assert the result contains an error message (not a crash/empty)."""
        assert key in result, (
            f"Expected error key '{key}' in result, got keys: {list(result.keys())}"
        )
        assert result[key], f"Error message is empty: {result}"
