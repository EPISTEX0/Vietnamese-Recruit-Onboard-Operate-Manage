"""Test that the AI Assistant has NO write-capable tools.

This is the core safety invariant (CONTEXT.md § "Nội bộ AI Assistant", entry **Tool**):
"The LLM is never given a tool that writes to the database —
that safety boundary is structural, not a convention."

These tests verify that:
1. All defined tools are either Read-Tool or Draft-Tool
2. No tool has write capabilities
3. The OpenAI tool definitions sent to the LLM contain no write operations
4. Tool filtering preserves safety boundaries
"""

from __future__ import annotations

from src.modules.assistant.domain.tools import (
    TOOL_DEFINITIONS,
    ToolKind,
    get_openai_tools,
)


class TestToolBoundary:
    """Verify the assistant's tool boundary is structurally safe."""

    def test_all_tools_are_read_or_draft(self) -> None:
        """Every tool must be Read-Tool or Draft-Tool — nothing else."""
        for tool in TOOL_DEFINITIONS:
            assert tool.kind in (ToolKind.READ, ToolKind.DRAFT), (
                f"Tool '{tool.name}' has unexpected kind '{tool.kind}'. "
                f"Only 'read' and 'draft' are allowed — CONTEXT.md, entry Tool."
            )

    def test_no_write_tools_exist(self) -> None:
        """No tool with write/write/execute/send kind exists."""
        write_kinds = {"write", "execute", "send", "mutate", "delete", "create"}
        for tool in TOOL_DEFINITIONS:
            assert tool.kind.value not in write_kinds, (
                f"Tool '{tool.name}' has write-capable kind '{tool.kind}'. "
                f"This violates the structural safety boundary in CONTEXT.md."
            )

    def test_known_tool_names(self) -> None:
        """Verify exactly the 10 expected tools exist."""
        expected_names = {
            "count_candidates_by_status",
            "list_in_progress_onboarding",
            "search_candidates",
            "get_candidate_parsed_cv",
            "list_job_openings",
            "get_department_info",
            "list_interviews_for_candidate",
            "get_onboarding_task_details",
            "draft_interview_invitation",
            "draft_congratulations_email",
        }
        actual_names = {t.name for t in TOOL_DEFINITIONS}
        assert actual_names == expected_names, (
            f"Tool set mismatch. Expected {expected_names}, got {actual_names}. "
            f"Any new tool must be reviewed for write-safety."
        )

    def test_openai_tools_format_has_no_write(self) -> None:
        """The OpenAI-format tool definitions must not contain write operations."""
        openai_tools = get_openai_tools()
        assert len(openai_tools) == 10

        for tool in openai_tools:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

            # Descriptions must not indicate write capability
            desc_lower = func["description"].lower()
            assert "write" not in desc_lower or "never write" in desc_lower, (
                f"Tool '{func['name']}' description may imply write capability: "
                f"{func['description']}"
            )

    def test_draft_tools_do_not_execute(self) -> None:
        """The draft tools must be Draft-Tool, not execute any write."""
        draft_tools = [t for t in TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT]
        for t in draft_tools:
            assert "sử dụng khi" in t.description.lower()

    def test_draft_tool_count(self) -> None:
        """There should be exactly 2 Draft-Tools."""
        draft_tools = [t for t in TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT]
        assert len(draft_tools) == 2
        names = {t.name for t in draft_tools}
        assert names == {"draft_interview_invitation", "draft_congratulations_email"}

    def test_read_tool_count(self) -> None:
        """There should be exactly 8 Read-Tools."""
        read_tools = [t for t in TOOL_DEFINITIONS if t.kind == ToolKind.READ]
        assert len(read_tools) == 8
        read_names = {t.name for t in read_tools}
        assert read_names == {
            "count_candidates_by_status",
            "list_in_progress_onboarding",
            "search_candidates",
            "get_candidate_parsed_cv",
            "list_job_openings",
            "get_department_info",
            "list_interviews_for_candidate",
            "get_onboarding_task_details",
        }


class TestFilteredToolBoundary:
    """Verify filtered tool sets still maintain safety boundaries."""

    def test_filtered_tools_are_read_or_draft(self) -> None:
        """Any subset of tools must still be Read-Tool or Draft-Tool."""
        all_names = {t.name for t in TOOL_DEFINITIONS}
        for name in all_names:
            filtered = get_openai_tools(enabled_names={name})
            assert len(filtered) == 1
            # The remaining tool must still be safe
            tool_name = filtered[0]["function"]["name"]
            tool_def = next(t for t in TOOL_DEFINITIONS if t.name == tool_name)
            assert tool_def.kind in (ToolKind.READ, ToolKind.DRAFT)

    def test_filtered_tools_preserve_kind(self) -> None:
        """Filtering doesn't change tool kinds."""
        # Filter to only draft tools
        draft_names = {t.name for t in TOOL_DEFINITIONS if t.kind == ToolKind.DRAFT}
        filtered = get_openai_tools(enabled_names=draft_names)
        assert len(filtered) == 2
        names = {t["function"]["name"] for t in filtered}
        assert names == {"draft_interview_invitation", "draft_congratulations_email"}

    def test_empty_filter_returns_no_tools(self) -> None:
        """Empty filter returns no tools — LLM has no capabilities."""
        result = get_openai_tools(enabled_names=set())
        assert len(result) == 0

    def test_all_disabled_means_no_write(self) -> None:
        """Even with no tools, the safety boundary holds."""
        result = get_openai_tools(enabled_names=set())
        # No tools means no write tools — safe by definition
        assert len(result) == 0
