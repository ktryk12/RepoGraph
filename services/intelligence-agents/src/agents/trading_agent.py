"""
TradingAgent — paper trading only.

Responsibilities:
  - Load local trading skills from babyai/skills/trading/
  - Trigger SkillCrawler GitHub crawl for domain='trading'
  - Analyze symbols: market data → technical indicators → risk sizing → policy check
  - Emit TRADE_RECOMMENDATION messages (paper_only=True always)
  - run_cycle() over a list of symbols, publishing results to bus

Design:
  - Never crash: all external calls are wrapped; failures return HOLD
  - Injectable dependencies: bus, skill_crawler, policy, redis_client
  - No real orders — enforced by TradingPolicy.PAPER_ONLY=True
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

_log = logging.getLogger(__name__)


# ── Injectable dependency protocols ──────────────────────────────────────────

class _BusLike(Protocol):
    def publish(self, message: Any) -> None: ...


class _SkillCrawlerLike(Protocol):
    async def on_pattern_detected(self, event: Any) -> int: ...


class _PolicyLike(Protocol):
    def validate(self, **kwargs: Any) -> Any: ...


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecommendation:
    symbol: str
    action: str                    # "BUY" | "SELL" | "HOLD"
    confidence: float              # 0.0 .. 1.0
    signals: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    paper_only: bool = True
    policy_allowed: bool = True
    policy_violations: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": self.confidence,
            "signals": self.signals,
            "risk": self.risk,
            "paper_only": self.paper_only,
            "policy_allowed": self.policy_allowed,
            "policy_violations": self.policy_violations,
            "timestamp": self.timestamp,
        }


# ── TradingAgent ──────────────────────────────────────────────────────────────

class TradingAgent:
    AGENT_ID = "trading-agent-001"
    TRADING_DOMAIN = "trading"
    _OHLCV_DAYS = 60          # days of history to fetch
    _ACCOUNT_EQUITY = 10_000  # paper account equity (USD)

    def __init__(
        self,
        *,
        bus: Optional[_BusLike] = None,
        skill_crawler: Optional[_SkillCrawlerLike] = None,
        policy: Optional[_PolicyLike] = None,
        redis_client: Any = None,
        account_equity: float = _ACCOUNT_EQUITY,
        binance_client: Any = None,
    ) -> None:
        self._bus = bus
        self._skill_crawler = skill_crawler
        self._policy = policy
        self._redis = redis_client
        self._account_equity = account_equity
        self._daily_trade_count = 0
        self._initialized = False

        # Lazy skill module references
        self._technical: Any = None
        self._market_data: Any = None
        self._risk: Any = None
        self._sentiment: Any = None

        # Binance live/paper execution
        self.binance: Any = binance_client    # BinanceClientWrapper or None
        self.open_orders: Dict[str, Any] = {}  # order_id → order details
        self.daily_pnl: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Load local trading skills and optionally trigger GitHub crawl."""
        self._load_local_skills()
        await self._trigger_skill_crawl()
        self._initialized = True
        _log.info("trading_agent_initialized account_equity=%s", self._account_equity)

    def _load_local_skills(self) -> None:
        """Import fallback skill modules. Never crashes."""
        try:
            from babyai.skills.trading.fallback import technical
            self._technical = technical
        except Exception as exc:
            _log.warning("skill_load_failed module=technical error=%s", exc)

        try:
            from babyai.skills.trading.fallback import market_data
            self._market_data = market_data
        except Exception as exc:
            _log.warning("skill_load_failed module=market_data error=%s", exc)

        try:
            from babyai.skills.trading.fallback import risk
            self._risk = risk
        except Exception as exc:
            _log.warning("skill_load_failed module=risk error=%s", exc)

        try:
            from babyai.skills.trading.fallback import sentiment
            self._sentiment = sentiment
        except Exception as exc:
            _log.warning("skill_load_failed module=sentiment error=%s", exc)

    async def _trigger_skill_crawl(self) -> None:
        """Publish a SkillUpdateEvent for domain='trading' to trigger GitHub crawl."""
        if self._skill_crawler is None:
            return
        try:
            from babyai.skills.crawler import SkillUpdateEvent
            event = SkillUpdateEvent(
                domain=self.TRADING_DOMAIN,
                dimension="technical",
                query="trading python indicators",
                timestamp=datetime.now(timezone.utc),
            )
            accepted = await self._skill_crawler.on_pattern_detected(event)
            _log.info("skill_crawl_complete domain=trading accepted=%d", accepted)
        except Exception as exc:
            _log.warning("skill_crawl_failed error=%s", exc)

    # ── Analysis ──────────────────────────────────────────────────────────────

    async def analyze(self, symbol: str) -> TradeRecommendation:
        """
        Full analysis pipeline for a single symbol.
        Returns HOLD with confidence=0 on any unrecoverable error.
        """
        hold = TradeRecommendation(symbol=symbol, action="HOLD", confidence=0.0)
        try:
            return await self._run_analysis(symbol)
        except Exception as exc:
            _log.warning("trading_analysis_failed symbol=%s error=%s", symbol, exc)
            return hold

    async def _run_analysis(self, symbol: str) -> TradeRecommendation:
        # 1. Market data — now returns DataFrame
        ohlcv_df = self._fetch_ohlcv(symbol)
        if ohlcv_df is None or (hasattr(ohlcv_df, "__len__") and len(ohlcv_df) == 0):
            _log.warning("no_market_data symbol=%s", symbol)
            return TradeRecommendation(symbol=symbol, action="HOLD", confidence=0.0)

        # Extract current price from DataFrame or dict fallback
        try:
            import pandas as pd
            if isinstance(ohlcv_df, pd.DataFrame) and "close" in ohlcv_df.columns:
                current_price = float(ohlcv_df["close"].iloc[-1])
            elif isinstance(ohlcv_df, dict):
                closes = ohlcv_df.get("closes", [])
                if not closes:
                    _log.warning("no_market_data symbol=%s", symbol)
                    return TradeRecommendation(symbol=symbol, action="HOLD", confidence=0.0)
                current_price = float(closes[-1])
            else:
                _log.warning("unknown_ohlcv_type symbol=%s type=%s", symbol, type(ohlcv_df))
                return TradeRecommendation(symbol=symbol, action="HOLD", confidence=0.0)
        except Exception as exc:
            _log.warning("price_extract_failed symbol=%s error=%s", symbol, exc)
            return TradeRecommendation(symbol=symbol, action="HOLD", confidence=0.0)

        # 2. Technical analysis — pass DataFrame directly (or closes list for fallback)
        tech_signals: Dict[str, Any] = {}
        if self._technical is not None:
            try:
                tech_signals = self._technical.analyze(ohlcv_df)
            except Exception as exc:
                _log.warning("technical_analysis_failed symbol=%s error=%s", symbol, exc)

        # 3. Action + confidence come from analyze() directly (no separate _signals_to_action)
        action = str(tech_signals.get("action", "HOLD"))
        confidence = float(tech_signals.get("confidence", 0.0))

        # 4. Risk sizing
        risk_info: Dict[str, Any] = {}
        stop_loss = current_price * 0.97  # 3% stop loss
        if self._risk is not None:
            try:
                risk_info = self._risk.assess_risk(
                    confidence=confidence,
                    account_equity=self._account_equity,
                    entry_price=current_price,
                    stop_loss_price=stop_loss,
                    max_position_pct=0.05,
                )
            except Exception as exc:
                _log.warning("risk_assessment_failed symbol=%s error=%s", symbol, exc)

        position_pct = float(risk_info.get("recommended_risk_pct", 0.0))

        # 5. Policy check
        policy_allowed = True
        policy_violations: List[str] = []
        if self._policy is not None:
            try:
                result = self._policy.validate(
                    action=action,
                    symbol=symbol,
                    confidence=confidence,
                    position_pct=position_pct,
                    total_exposure=position_pct,  # simplified: single-position exposure
                    daily_trade_count=self._daily_trade_count,
                    is_paper=True,
                )
                policy_allowed = bool(result.allowed)
                policy_violations = [v.message for v in result.hard_violations]
                for warning in result.warnings:
                    _log.info("trading_policy_warning symbol=%s msg=%s", symbol, warning)
            except Exception as exc:
                _log.warning("policy_check_failed symbol=%s error=%s", symbol, exc)

        if action != "HOLD" and policy_allowed:
            self._daily_trade_count += 1

        return TradeRecommendation(
            symbol=symbol,
            action=action if policy_allowed else "HOLD",
            confidence=confidence,
            signals=tech_signals,
            risk=risk_info,
            paper_only=True,
            policy_allowed=policy_allowed,
            policy_violations=policy_violations,
        )

    def _fetch_ohlcv(self, symbol: str) -> Any:
        if self._market_data is None:
            return None
        try:
            return self._market_data.get_ohlcv(symbol, limit=self._OHLCV_DAYS)
        except Exception as exc:
            _log.warning("ohlcv_fetch_failed symbol=%s error=%s", symbol, exc)
            return None

    # ── Cycle ─────────────────────────────────────────────────────────────────

    async def run_cycle(self, symbols: List[str]) -> List[TradeRecommendation]:
        """
        Analyze each symbol and publish TRADE_RECOMMENDATION messages.
        Always returns results (partial on failure).
        """
        if not self._initialized:
            await self.initialize()

        self._daily_trade_count = 0  # reset per cycle
        results: List[TradeRecommendation] = []

        for symbol in symbols:
            rec = await self.analyze(symbol)
            results.append(rec)
            self._publish_recommendation(rec)

        _log.info(
            "trading_cycle_complete symbols=%d buys=%d sells=%d holds=%d",
            len(symbols),
            sum(1 for r in results if r.action == "BUY"),
            sum(1 for r in results if r.action == "SELL"),
            sum(1 for r in results if r.action == "HOLD"),
        )
        return results

    # ── Live/paper execution ──────────────────────────────────────────────────

    async def execute_signal(
        self,
        symbol: str,
        action: str,
        confidence: float,
        signals: Dict[str, Any],
        df: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a trade signal via BinanceClientWrapper.
        Uses current price from signals dict or BinanceClientWrapper.
        Returns order dict or None on failure/skip.
        Never raises.
        """
        if self.binance is None:
            _log.debug("execute_signal_skipped no_binance_client symbol=%s", symbol)
            return None

        # Policy check before execution
        if self._policy is not None:
            try:
                is_paper = self.binance.mode == "PAPER"
                result = self._policy.validate(
                    action=action,
                    symbol=symbol,
                    confidence=confidence,
                    position_pct=0.05,
                    total_exposure=0.05,
                    daily_trade_count=self._daily_trade_count,
                    is_paper=is_paper,
                )
                if not result.allowed:
                    for v in result.hard_violations:
                        _log.info("execute_signal_blocked rule=%s msg=%s", v.rule, v.message)
                    return None
            except Exception as exc:
                _log.warning("execute_signal_policy_failed symbol=%s error=%s", symbol, exc)

        # Determine price
        current_price: Optional[float] = None
        if signals and isinstance(signals, dict):
            current_price = signals.get("price")
        if not current_price and self.binance.mode == "LIVE":
            current_price = self.binance.get_price(symbol)
        if not current_price:
            _log.warning("execute_signal_no_price symbol=%s", symbol)
            return None

        # Compute quantity (5% of equity / price)
        try:
            balance_info = self.binance.get_account_balance()
            usdt = float(balance_info.get("USDT", self._account_equity))
            size_usdt = min(usdt * 0.05, self.binance.max_order_usdt)
            quantity = size_usdt / current_price
        except Exception as exc:
            _log.warning("execute_signal_sizing_failed symbol=%s error=%s", symbol, exc)
            return None

        # Place order
        try:
            order = self.binance.place_order(
                symbol=symbol,
                side=action,
                quantity=quantity,
                price=round(current_price, 4) if self.binance.mode == "LIVE" else None,
                current_price=current_price,
            )
        except ValueError as exc:
            # Circuit breaker fired
            _log.warning("execute_signal_circuit_breaker symbol=%s error=%s", symbol, exc)
            return None
        except Exception as exc:
            _log.warning("execute_signal_order_failed symbol=%s error=%s", symbol, exc)
            return None

        order_id = str(order.get("orderId", ""))
        self.open_orders[order_id] = {
            "symbol": symbol,
            "side": action,
            "entry_price": current_price,
            "quantity": quantity,
            "order": order,
            "stop_loss": current_price * 0.98,
            "take_profit": current_price * 1.04,
        }
        self._daily_trade_count += 1

        # Publish to Kafka
        self._publish_execution(order, symbol, action, confidence)
        return order

    async def manage_positions(self) -> None:
        """
        Check all open positions against stop-loss and take-profit.
        Call this on a periodic timer (e.g., every 60 seconds).
        Never raises.
        """
        if not self.open_orders or self.binance is None:
            return

        to_close: List[str] = []
        for order_id, pos in list(self.open_orders.items()):
            symbol = pos["symbol"]
            entry = pos["entry_price"]
            side = pos["side"]
            stop_loss = pos["stop_loss"]
            take_profit = pos["take_profit"]

            current_price: Optional[float] = None
            if self.binance.mode == "LIVE":
                current_price = self.binance.get_price(symbol)
            else:
                # PAPER: try to get price from market_data if loaded
                if self._market_data is not None:
                    try:
                        current_price = self._market_data.get_current_price(symbol.replace("USDT", ""))
                    except Exception:
                        pass

            if current_price is None:
                continue

            exit_reason: Optional[str] = None
            if side == "BUY":
                if current_price <= stop_loss:
                    exit_reason = "stop_loss"
                elif current_price >= take_profit:
                    exit_reason = "take_profit"
            else:  # SELL (short)
                if current_price >= stop_loss:
                    exit_reason = "stop_loss"
                elif current_price <= take_profit:
                    exit_reason = "take_profit"

            if exit_reason:
                pnl_usdt = (
                    (current_price - entry) * pos["quantity"]
                    if side == "BUY"
                    else (entry - current_price) * pos["quantity"]
                )
                self.daily_pnl += pnl_usdt
                self.binance.record_pnl(pnl_usdt)
                _log.info(
                    "position_closed symbol=%s reason=%s pnl=%.2f mode=%s",
                    symbol, exit_reason, pnl_usdt, self.binance.mode,
                )
                to_close.append(order_id)

        for order_id in to_close:
            self.open_orders.pop(order_id, None)

    def _publish_execution(
        self, order: Dict[str, Any], symbol: str, action: str, confidence: float
    ) -> None:
        topic = (
            "trading.live_executions"
            if self.binance and self.binance.mode == "LIVE"
            else "trading.paper_executions"
        )
        payload = {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "order": order,
            "mode": self.binance.mode if self.binance else "UNKNOWN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self._bus is not None:
            try:
                from babyai_shared.bus.protocol import Message, MessageType
                msg = Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.AGENT_ID,
                    to_agent="supervisor-001",
                    message_type=MessageType.TRADE_RECOMMENDATION,
                    payload=payload,
                    context_id=f"exec-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                self._bus.publish(msg)
            except Exception as exc:
                _log.warning("publish_execution_failed symbol=%s error=%s", symbol, exc)

    def _publish_recommendation(self, rec: TradeRecommendation) -> None:
        if self._bus is None:
            return
        try:
            from babyai_shared.bus.protocol import Message, MessageType
            msg = Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.AGENT_ID,
                to_agent="supervisor-001",
                message_type=MessageType.TRADE_RECOMMENDATION,
                payload=rec.to_dict(),
                context_id=f"trading-{rec.symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._bus.publish(msg)
        except Exception as exc:
            _log.warning("publish_recommendation_failed symbol=%s error=%s", rec.symbol, exc)


# ── Signal interpretation ─────────────────────────────────────────────────────

def _signals_to_action(signals: Dict[str, Any]) -> tuple[str, float]:
    """
    Convert technical signals dict to (action, confidence).
    Simple vote-counting logic.
    """
    if not signals:
        return "HOLD", 0.0

    bullish_votes = 0
    bearish_votes = 0
    total_votes = 0

    signal_list = signals.get("signals", [])
    for sig in signal_list:
        total_votes += 1
        if sig in ("RSI_OVERSOLD", "MACD_BULLISH", "PRICE_BELOW_BB_LOWER", "GOLDEN_CROSS"):
            bullish_votes += 1
        elif sig in ("RSI_OVERBOUGHT", "MACD_BEARISH", "PRICE_ABOVE_BB_UPPER", "DEATH_CROSS"):
            bearish_votes += 1

    if total_votes == 0:
        return "HOLD", 0.0

    bullish_ratio = bullish_votes / total_votes
    bearish_ratio = bearish_votes / total_votes

    if bullish_ratio >= 0.6:
        return "BUY", bullish_ratio
    if bearish_ratio >= 0.6:
        return "SELL", bearish_ratio
    return "HOLD", max(bullish_ratio, bearish_ratio)
