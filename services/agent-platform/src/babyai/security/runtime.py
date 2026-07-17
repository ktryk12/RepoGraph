from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import threading
from typing import Any, Iterable

from babyai.security.injection_scanner import InjectionScanner
from babyai.security.event_store import EventStore
from babyai.security.l6_trend_detector.trend_detector import TrendDetector
from babyai.security.l7_governance.governance_agent import GovernanceAgent
from babyai.skills.crawler import SkillCrawler
from babyai.skills.fetchers.local_fetcher import LocalSkillFetcher
from babyai.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SecurityRuntime:
    def __init__(
        self,
        *,
        redis_client: Any,
        sqlite_path: str | Path,
        skill_paths: Iterable[Path] | None = None,
    ) -> None:
        self.redis = AsyncRedisAdapter(redis_client) if redis_client is not None else _NoopAsyncRedis()
        self.sqlite_path = Path(sqlite_path)
        self.event_store = EventStore(path=self.sqlite_path)
        self.skill_registry = SkillRegistry(redis_client=self.redis, state_manager=None)
        self.local_fetcher = LocalSkillFetcher()
        if skill_paths is not None:
            self.local_fetcher.BASE_PATHS = [Path(path) for path in skill_paths]
        self.trend_detector = TrendDetector(event_store=self.event_store, redis_client=self.redis)
        self.governance_agent = GovernanceAgent(redis_client=self.redis, skill_registry=self.skill_registry)
        self.skill_crawler = SkillCrawler(
            redis_client=self.redis,
            registry=self.skill_registry,
            expert_api=_NoopExpertApi(),
        )
        self.injection_scanner = InjectionScanner(redis_client=self.redis)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_local_skills(self) -> int:
        count = self.local_fetcher.discover(self.skill_registry)
        logger.info("security_startup skills_discovered=%s", int(count))
        return int(count)

    def start_background_tasks(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="security-runtime", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.create_task(self._guarded(self.trend_detector.run_loop(), name="trend_detector"))
        loop.create_task(self._guarded(self.governance_agent.start(), name="governance_agent"))
        loop.create_task(self._guarded(self.skill_crawler.listen(), name="skill_crawler"))
        loop.create_task(self._guarded(self.injection_scanner.start_policy_listener(), name="policy_listener"))
        loop.run_forever()

    async def _guarded(self, coro: Any, *, name: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("security_background_task_failed task=%s error=%s", name, exc)


class AsyncRedisAdapter:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> Any:
        return await asyncio.to_thread(self._redis.get, key)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> Any:
        return await asyncio.to_thread(self._redis.setex, key, int(ttl_seconds), value)

    async def sadd(self, key: str, *values: Any) -> Any:
        return await asyncio.to_thread(self._redis.sadd, key, *values)

    async def smembers(self, key: str) -> Any:
        return await asyncio.to_thread(self._redis.smembers, key)

    async def publish(self, channel: str, value: str) -> Any:
        return await asyncio.to_thread(self._redis.publish, channel, value)

    def pubsub(self) -> "AsyncRedisPubSubAdapter":
        return AsyncRedisPubSubAdapter(self._redis.pubsub())


class AsyncRedisPubSubAdapter:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub
        self._closed = False

    async def __aenter__(self) -> "AsyncRedisPubSubAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def subscribe(self, *channels: str) -> Any:
        return await asyncio.to_thread(self._pubsub.subscribe, *channels)

    async def get_message(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._pubsub.get_message, **kwargs)

    async def listen(self):
        while not self._closed:
            message = await asyncio.to_thread(
                self._pubsub.get_message,
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message is None:
                await asyncio.sleep(0.05)
                continue
            yield message

    async def close(self) -> None:
        self._closed = True
        close = getattr(self._pubsub, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


class _NoopAsyncRedis:
    async def get(self, _key: str) -> Any:
        return None

    async def setex(self, _key: str, _ttl_seconds: int, _value: str) -> bool:
        return True

    async def sadd(self, _key: str, *_values: Any) -> int:
        return 0

    async def smembers(self, _key: str) -> set[str]:
        return set()

    async def publish(self, _channel: str, _value: str) -> int:
        return 0

    def pubsub(self) -> "_NoopPubSub":
        return _NoopPubSub()


class _NoopPubSub:
    async def __aenter__(self) -> "_NoopPubSub":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def subscribe(self, *_channels: str) -> None:
        return None

    async def get_message(self, **_kwargs: Any) -> None:
        await asyncio.sleep(0.05)
        return None

    async def listen(self):
        while True:
            await asyncio.sleep(0.2)
            if False:
                yield None


class _NoopExpertApi:
    async def call_single(self, *, role: str, prompt: str) -> dict[str, float]:
        del role, prompt
        return {"score": 0.0}
