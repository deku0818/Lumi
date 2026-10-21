"""Subagent observations and cancellation; dispatch remains in the Agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lumi.agents.runtime.subagents import get_subagent_service
from lumi.gateway.protocol import ServerEvent, event_frame

if TYPE_CHECKING:
    from lumi.gateway.session import GatewaySession


async def list_subagents(session: GatewaySession, params: dict) -> dict:
    service = get_subagent_service()
    return {"agents": service.list_agents(session.current_thread_id)}


async def subagent_history(session: GatewaySession, params: dict) -> dict:
    return get_subagent_service().history(
        session.current_thread_id,
        params["agent_id"],
        after=int(params.get("after", 0)),
        limit=int(params.get("limit", 100)),
    )


async def observe_subagent(session: GatewaySession, params: dict) -> dict:
    service = get_subagent_service()
    agent_id = params["agent_id"]
    service.store.owned_agent(session.current_thread_id, agent_id)
    # Register before reading: committed events racing the snapshot are duplicates,
    # not gaps. Clients deduplicate by seq and paginate any missing history.
    session._hub.add_observer(agent_id, session._channel)
    return await subagent_history(session, params)


async def unobserve_subagent(session: GatewaySession, params: dict) -> dict:
    agent_id = params["agent_id"]
    get_subagent_service().store.owned_agent(session.current_thread_id, agent_id)
    session._hub.remove_observer(agent_id, session._channel)
    return {"agent_id": agent_id}


async def stop_subagent(session: GatewaySession, params: dict) -> dict:
    return {
        "stopped": await get_subagent_service().stop(
            session.current_thread_id, params["agent_id"]
        )
    }


def publish_subagent_event(hub, agent_id: str, event: dict) -> None:
    if hub.has_observers(agent_id):
        hub.publish_thread_event(
            agent_id,
            event_frame(
                ServerEvent.SUBAGENT_EVENT,
                agent_id,
                {
                    "agent_id": agent_id,
                    "turn_id": event["turn_id"],
                    "seq": event["seq"],
                    "event_type": event["type"],
                    "data": event["payload"],
                },
            ),
        )
