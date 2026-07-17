"""
agents/base_observer.py — Sprint A1 Part 2

Abstract base class for JEPA-capable observer agents.

Relationship to the existing agent hierarchy
--------------------------------------------
BaseObserver is independent of agents.base.Agent.  It does NOT
use the Message / MessageType / Context protocol from agents/protocol.py
and is NOT registered in the agent registry.

agents.base.Agent is the existing Message-passing contract for the
decision-making pipeline.  BaseObserver is a parallel contract for
the JEPA sensory-encoding pipeline.  Integration between the two
hierarchies (e.g. an agent that is both a decision-maker and an
observer) is left to Sprint A2.

Usage pattern
-------------
Subclasses implement observe() only:

    class ToolResultObserver(BaseObserver):
        def observe(self) -> Observation:
            result = run_tool(...)
            return Observation(
                observation_id=str(uuid4()),
                episode_id=self.episode_id,
                source="tool-runner",
                observation_type="tool_result",
                content=result,
                timestamp=_now_iso(),
            )

The base class handles:
  - encoding  (observe() → LatentPacket via LatentEncoder)
  - publishing (LatentPacket → Kafka topic agent.latent_packets)

Publisher injection
-------------------
A publisher callable of the form (topic: str, packet: LatentPacket) → None
is accepted at construction time.  If omitted, a no-op publisher is used
and a DEBUG log is emitted.  Wiring to a real Kafka producer happens in
Sprint A2.
"""

from __future__ import annotations

import abc
import logging
from typing import Callable, Optional

from bus.topics import AGENT_LATENT_PACKETS
from babyai_shared.core.latent_packet import LatentPacket
from babyai_shared.core.observations import Observation
from services.latent_encoder import LatentEncoder

logger = logging.getLogger(__name__)

PublisherFn = Callable[[str, LatentPacket], None]


def _noop_publisher(topic: str, packet: LatentPacket) -> None:
    """Default publisher — used when no Kafka producer is wired yet.

    Logs at DEBUG so integration tests can observe the call without
    requiring a live Kafka broker.
    """
    logger.debug(
        "base_observer_publish_noop topic=%s packet_id=%s episode_id=%s",
        topic,
        packet.packet_id,
        packet.episode_id,
    )


class BaseObserver(abc.ABC):
    """Abstract base class for JEPA-capable observer agents.

    Subclasses must implement :meth:`observe`.
    :meth:`encode` and :meth:`publish` are provided by this base class
    and are not intended to be overridden.

    Parameters
    ----------
    encoder:
        A :class:`~services.latent_encoder.LatentEncoder` instance.
        Defaults to a new encoder pointed at the standard model-runner
        URL.  Inject a custom encoder (with a mock request_fn) in tests.
    publisher:
        Callable ``(topic: str, packet: LatentPacket) -> None``.
        Defaults to a no-op that logs at DEBUG.  Replace with a real
        Kafka producer in Sprint A2.
    """

    def __init__(
        self,
        *,
        encoder: Optional[LatentEncoder] = None,
        publisher: Optional[PublisherFn] = None,
    ) -> None:
        self._encoder: LatentEncoder = encoder if encoder is not None else LatentEncoder()
        self._publisher: PublisherFn = publisher if publisher is not None else _noop_publisher

    @abc.abstractmethod
    def observe(self) -> Observation:
        """Produce an Observation from the domain environment.

        Subclasses implement domain-specific I/O here (reading sensors,
        querying APIs, inspecting tool results, etc.).  The result is
        passed to :meth:`encode` by the caller.
        """

    def encode(self, obs: Observation) -> LatentPacket:
        """Encode *obs* into a LatentPacket.

        Delegates to :attr:`_encoder`.  Graceful degradation is handled
        inside :class:`~services.latent_encoder.LatentEncoder`; this
        method always returns a valid LatentPacket.
        """
        return self._encoder.encode(obs)

    def publish(self, packet: LatentPacket) -> None:
        """Publish *packet* to the ``agent.latent_packets`` Kafka topic.

        The topic constant is sourced from :data:`bus.topics.AGENT_LATENT_PACKETS`
        so the published topic is always in sync with the registry.
        If no publisher was injected, the no-op publisher fires and a
        DEBUG entry is written.
        """
        self._publisher(AGENT_LATENT_PACKETS, packet)
