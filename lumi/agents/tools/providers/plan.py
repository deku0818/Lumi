"""Plan Mode 工具提供者 - 提供进入/退出计划模式的功能

该模块提供 EnterPlanMode 和 ExitPlanMode 工具。
EnterPlanMode 让 Agent 进入只读的探索和规划阶段；
ExitPlanMode 让 Agent 提交计划供用户审批，用户可批准或拒绝。
"""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command, interrupt

from lumi.agents.tools.config import _parse_md_file
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

_DEFAULT_ENTER_PLAN_MODE_DESCRIPTION = (
    "Use this tool proactively when you're about to start a non-trivial "
    "implementation task. Getting user sign-off on your approach before writing "
    "code prevents wasted effort and ensures alignment. This tool transitions you "
    "into plan mode where you can explore the codebase and design an implementation "
    "approach for user approval.\n\n"
    "## When to Use This Tool\n\n"
    "**Prefer using EnterPlanMode** for implementation tasks unless they're simple. "
    "Use it when ANY of these conditions apply:\n\n"
    "1. **New Feature Implementation**: Adding meaningful new functionality\n"
    '   - Example: "Add a logout button" - where should it go? What should happen on click?\n'
    '   - Example: "Add form validation" - what rules? What error messages?\n\n'
    "2. **Multiple Valid Approaches**: The task can be solved in several different ways\n"
    '   - Example: "Add caching to the API" - could use Redis, in-memory, file-based, etc.\n'
    '   - Example: "Improve performance" - many optimization strategies possible\n\n'
    "3. **Code Modifications**: Changes that affect existing behavior or structure\n"
    '   - Example: "Update the login flow" - what exactly should change?\n'
    '   - Example: "Refactor this component" - what\'s the target architecture?\n\n'
    "4. **Architectural Decisions**: The task requires choosing between patterns or technologies\n"
    '   - Example: "Add real-time updates" - WebSockets vs SSE vs polling\n'
    '   - Example: "Implement state management" - Redux vs Context vs custom solution\n\n'
    "5. **Multi-File Changes**: The task will likely touch more than 2-3 files\n"
    '   - Example: "Refactor the authentication system"\n'
    '   - Example: "Add a new API endpoint with tests"\n\n'
    "6. **Unclear Requirements**: You need to explore before understanding the full scope\n"
    '   - Example: "Make the app faster" - need to profile and identify bottlenecks\n'
    '   - Example: "Fix the bug in checkout" - need to investigate root cause\n\n'
    "7. **User Preferences Matter**: The implementation could reasonably go multiple ways\n"
    "   - If you would use `ask` to clarify the approach, use EnterPlanMode instead\n"
    "   - Plan mode lets you explore first, then present options with context\n\n"
    "## When NOT to Use This Tool\n\n"
    "Only skip EnterPlanMode for simple tasks:\n"
    "- Single-line or few-line fixes (typos, obvious bugs, small tweaks)\n"
    "- Adding a single function with clear requirements\n"
    "- Tasks where the user has given very specific, detailed instructions\n"
    "- Pure research/exploration tasks (use the Agent tool with explore agent instead)\n\n"
    "## What Happens in Plan Mode\n\n"
    "In plan mode, you'll:\n"
    "1. Thoroughly explore the codebase using Glob, Grep, and Read tools\n"
    "2. Understand existing patterns and architecture\n"
    "3. Design an implementation approach\n"
    "4. Present your plan to the user for approval\n"
    "5. Use `ask` if you need to clarify approaches\n"
    "6. Exit plan mode with ExitPlanMode when ready to implement\n\n"
    "## Examples\n\n"
    "### GOOD - Use EnterPlanMode:\n"
    'User: "Add user authentication to the app"\n'
    "- Requires architectural decisions (session vs JWT, where to store tokens, middleware structure)\n\n"
    'User: "Optimize the database queries"\n'
    "- Multiple approaches possible, need to profile first, significant impact\n\n"
    'User: "Implement dark mode"\n'
    "- Architectural decision on theme system, affects many components\n\n"
    'User: "Add a delete button to the user profile"\n'
    "- Seems simple but involves: where to place it, confirmation dialog, API call, error handling, state updates\n\n"
    'User: "Update the error handling in the API"\n'
    "- Affects multiple files, user should approve the approach\n\n"
    "### BAD - Don't use EnterPlanMode:\n"
    'User: "Fix the typo in the README"\n'
    "- Straightforward, no planning needed\n\n"
    'User: "Add a console.log to debug this function"\n'
    "- Simple, obvious implementation\n\n"
    'User: "What files handle routing?"\n'
    "- Research task, not implementation planning\n\n"
    "## Important Notes\n\n"
    "- This tool REQUIRES user approval - they must consent to entering plan mode\n"
    "- If unsure whether to use it, err on the side of planning - it's better to get "
    "alignment upfront than to redo work\n"
    "- Users appreciate being consulted before significant changes are made to their codebase"
)

_DEFAULT_PLAN_MODE_RESPONSE = (
    "Entered plan mode. You should now focus on exploring the codebase "
    "and designing an implementation approach.\n\n"
    "In plan mode, you should:\n"
    "1. Thoroughly explore the codebase to understand existing patterns\n"
    "2. Identify similar features and architectural approaches\n"
    "3. Consider multiple approaches and their trade-offs\n"
    "4. Use `ask` if you need to clarify the approach\n"
    "5. Design a concrete implementation strategy\n"
    "6. When ready, use ExitPlanMode to present your plan for approval\n\n"
    "Remember: DO NOT write or edit any files yet. "
    "This is a read-only exploration and planning phase.\n\n"
    "<system-reminder>\n"
    "Plan mode is active. The user indicated that they do not want you to execute yet "
    "-- you MUST NOT make any edits (with the exception of the plan file mentioned below), "
    "run any non-readonly tools (including changing configs or making commits), "
    "or otherwise make any changes to the system. This supercedes any other instructions "
    "you have received.\n\n"
    "## Plan File Info:\n"
    "No plan file exists yet. You should create your plan at "
    "/root/.claude/plans/{file_name}.md using the write tool.\n"
    "You should build your plan incrementally by writing to or editing this file. "
    "NOTE that this is the only file you are allowed to edit - other than this you are "
    "only allowed to take READ-ONLY actions.\n\n"
    "## Plan Workflow\n\n"
    "### Phase 1: Initial Understanding\n"
    "Goal: Gain a comprehensive understanding of the user's request by reading through "
    "code and asking them questions. Critical: In this phase you should only use the "
    "Explore subagent type.\n\n"
    "1. Focus on understanding the user's request and the code associated with their request. "
    "Actively search for existing functions, utilities, and patterns that can be reused — "
    "avoid proposing new code when suitable implementations already exist.\n\n"
    "2. **Launch up to 3 Explore agents IN PARALLEL** (single message, multiple tool calls) "
    "to efficiently explore the codebase.\n"
    "   - Use 1 agent when the task is isolated to known files, the user provided specific "
    "file paths, or you're making a small targeted change.\n"
    "   - Use multiple agents when: the scope is uncertain, multiple areas of the codebase "
    "are involved, or you need to understand existing patterns before planning.\n"
    "   - Quality over quantity - 3 agents maximum, but you should try to use the minimum "
    "number of agents necessary (usually just 1)\n"
    "   - If using multiple agents: Provide each agent with a specific search focus or area "
    "to explore. Example: One agent searches for existing implementations, another explores "
    "related components, a third investigating testing patterns\n\n"
    "### Phase 2: Design\n"
    "Goal: Design an implementation approach.\n\n"
    "Launch Plan agent(s) to design the implementation based on the user's intent and your "
    "exploration results from Phase 1.\n\n"
    "You can launch up to 1 agent(s) in parallel.\n\n"
    "**Guidelines:**\n"
    "- **Default**: Launch at least 1 Plan agent for most tasks - it helps validate your "
    "understanding and consider alternatives\n"
    "- **Skip agents**: Only for truly trivial tasks (typo fixes, single-line changes, "
    "simple renames)\n\n"
    "In the agent prompt:\n"
    "- Provide comprehensive background context from Phase 1 exploration including filenames "
    "and code path traces\n"
    "- Describe requirements and constraints\n"
    "- Request a detailed implementation plan\n\n"
    "### Phase 3: Review\n"
    "Goal: Review the plan(s) from Phase 2 and ensure alignment with the user's intentions.\n"
    "1. Read the critical files identified by agents to deepen your understanding\n"
    "2. Ensure that the plans align with the user's original request\n"
    "3. Use `ask` to clarify any remaining questions with the user\n\n"
    "### Phase 4: Final Plan\n"
    "Goal: Write your final plan to the plan file (the only file you can edit).\n"
    "- Begin with a **Context** section: explain why this change is being made — the problem "
    "or need it addresses, what prompted it, and the intended outcome\n"
    "- Include only your recommended approach, not all alternatives\n"
    "- Ensure that the plan file is concise enough to scan quickly, but detailed enough to "
    "execute effectively\n"
    "- Include the paths of critical files to be modified\n"
    "- Reference existing functions and utilities you found that should be reused, with their "
    "file paths\n"
    "- Include a verification section describing how to test the changes end-to-end (run the "
    "code, use MCP tools, run tests)\n\n"
    "### Phase 5: Call ExitPlanMode\n"
    "At the very end of your turn, once you have asked the user questions and are happy with "
    "your final plan file - you should always call ExitPlanMode to indicate to the user that "
    "you are done planning.\n"
    "This is critical - your turn should only end with either using the `ask` tool "
    "OR calling ExitPlanMode. Do not stop unless it's for these 2 reasons\n\n"
    "**Important:** Use `ask` ONLY to clarify requirements or choose between "
    "approaches. Use ExitPlanMode to request plan approval. Do NOT ask about plan approval "
    'in any other way - no text questions, no `ask`. Phrases like "Is this plan '
    'okay?", "Should I proceed?", "How does this plan look?", "Any changes before we '
    'start?", or similar MUST use ExitPlanMode.\n\n'
    "NOTE: At any point in time through this workflow you should feel free to ask the user "
    "questions or clarifications using the `ask` tool. Don't make large assumptions "
    "about user intent. The goal is to present a well researched plan to the user, and tie "
    "any loose ends before implementation begins.\n"
    "</system-reminder>"
)


def _load_enter_plan_mode_prompt() -> tuple[str, str]:
    """从外部 MD 文件加载 EnterPlanMode 的 description 和 response。

    返回 (description, response)，文件不存在或解析失败时回退到默认值。
    """
    try:
        prompts_dir = get_config().prompts_dir
        md_path = prompts_dir / "tools" / "EnterPlanMode.md"
        if md_path.exists():
            result = _parse_md_file(str(md_path))
            if result is not None:
                desc = result.get("description", "").strip()
                resp = result.get("prompt", "").strip()
                if desc and resp:
                    return desc, resp
                if desc:
                    logger.warning(
                        "EnterPlanMode.md 缺少 body（response），使用默认 response"
                    )
                    return desc, _DEFAULT_PLAN_MODE_RESPONSE
                if resp:
                    logger.warning(
                        "EnterPlanMode.md 缺少 description，使用默认 description"
                    )
                    return _DEFAULT_ENTER_PLAN_MODE_DESCRIPTION, resp
    except Exception as e:
        logger.warning(f"加载 EnterPlanMode.md 失败，使用默认值: {e}")

    return _DEFAULT_ENTER_PLAN_MODE_DESCRIPTION, _DEFAULT_PLAN_MODE_RESPONSE


_enter_description, _enter_response = _load_enter_plan_mode_prompt()


@tool(description=_enter_description)
def EnterPlanMode() -> str:  # noqa: N802
    """进入计划模式，开始只读的代码探索和方案设计阶段"""
    return _enter_response


# ── exit_plan_mode ──

PLAN_REJECTED = "__plan_rejected__"

EXIT_PLAN_MODE_DESCRIPTION = (
    "Use this tool when you have finished planning and your plan is ready for "
    "user review. This signals the end of the planning phase and presents your "
    "plan to the user for approval.\n\n"
    "The user can either:\n"
    "- **Approve**: You exit plan mode and begin implementation\n"
    "- **Reject**: You stay in plan mode and continue refining the plan\n\n"
    "## When to Use\n"
    "- After writing your final plan to the plan file\n"
    "- When you have no remaining questions about the approach\n"
    "- As the last action of your planning turn\n\n"
    "## When NOT to Use\n"
    "- If you still have unresolved questions — use `ask` first\n"
    "- For research-only tasks that don't require implementation planning\n\n"
    "**Important:** Do NOT use `ask` to request plan approval. "
    "That is exactly what this tool does."
)

PLAN_APPROVED_RESPONSE = (
    "Plan approved by user. You should now exit plan mode and begin "
    "implementation according to the plan.\n\n"
    "<system-reminder>\n"
    "Plan mode has ended. The user has approved your plan. "
    "You may now make edits, run tools, and take actions to implement the plan. "
    "The plan file remains available for reference.\n"
    "</system-reminder>"
)

PLAN_REJECTED_RESPONSE = (
    "Plan not approved. The user wants you to continue refining the plan. "
    "Stay in plan mode, review the feedback, and update your plan accordingly.\n\n"
    "Remember: DO NOT write or edit any project files yet. "
    "This is still a read-only exploration and planning phase."
)


@tool(description=EXIT_PLAN_MODE_DESCRIPTION)
def ExitPlanMode(  # noqa: N802
    plan_file_path: Annotated[
        str,
        "The path to the plan file you wrote during the planning phase.",
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """退出计划模式，提交计划供用户审批"""
    user_response = interrupt(
        {
            "type": "ExitPlanMode",
            "tool_call_id": tool_call_id,
            "plan_file_path": plan_file_path,
        }
    )

    if user_response == PLAN_REJECTED:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=PLAN_REJECTED_RESPONSE,
                        tool_call_id=tool_call_id,
                    )
                ],
            },
        )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=PLAN_APPROVED_RESPONSE,
                    tool_call_id=tool_call_id,
                )
            ],
        },
    )
