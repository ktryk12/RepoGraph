from __future__ import annotations

from collections import defaultdict

from repograph.working_set.models import WorkingSet, WorkingSetFile, WorkingSetSymbol


class FakeGraphStore:
    def __init__(self) -> None:
        self._triples: list[tuple[str, str, str]] = []
        self._metadata: dict[str, str | None] = {"repo_path": None, "last_indexed": None}

    def put_triple(self, subject: str, predicate: str, obj: str) -> None:
        self._triples.append((subject, predicate, obj))

    def put_triples_batch(self, triples: list[tuple[str, str, str]]) -> None:
        self._triples.extend(triples)

    def clear(self) -> None:
        self._triples.clear()
        self._metadata = {"repo_path": None, "last_indexed": None}

    def callers_of(self, symbol: str) -> list[str]:
        return self.incoming(symbol, "CALLS")

    def callees_of(self, symbol: str) -> list[str]:
        return self.outgoing(symbol, "CALLS")

    def blast_radius(self, symbol: str, depth: int = 3) -> dict[str, list[str]]:
        visited: set[str] = set()
        frontier = {symbol}
        result: dict[str, list[str]] = {}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for current in frontier:
                callers = self.callers_of(current)
                result[current] = callers
                next_frontier.update(caller for caller in callers if caller not in visited)
            visited.update(frontier)
            frontier = next_frontier
            if not frontier:
                break
        return result

    def search(self, query: str, limit: int = 20) -> list[str]:
        lowered = query.lower()
        nodes = {subject for subject, _, _ in self._triples}
        return [node for node in sorted(nodes) if lowered in node.lower()][:limit]

    def file_symbols(self, filepath: str) -> list[str]:
        return [subject for subject, predicate, obj in self._triples if predicate == "IN_FILE" and obj == filepath]

    def outgoing(self, symbol: str, predicate: str) -> list[str]:
        return [obj for subject, pred, obj in self._triples if subject == symbol and pred == predicate]

    def incoming(self, symbol: str, predicate: str) -> list[str]:
        return [subject for subject, pred, obj in self._triples if obj == symbol and pred == predicate]

    def first_outgoing(self, symbol: str, predicate: str) -> str | None:
        matches = self.outgoing(symbol, predicate)
        return matches[-1] if matches else None

    def has_symbol(self, symbol: str) -> bool:
        return any(subject == symbol for subject, _, _ in self._triples)

    def stats(self) -> dict[str, int]:
        nodes = {subject for subject, _, _ in self._triples} | {obj for _, _, obj in self._triples}
        return {"node_count": len(nodes)}

    def load_metadata(self) -> dict[str, str | None]:
        return dict(self._metadata)

    def save_metadata(self, metadata: dict[str, str]) -> None:
        self._metadata = dict(metadata)


def make_symbol(index: int, *, risk_level: str = "medium", in_file: str | None = None) -> WorkingSetSymbol:
    return WorkingSetSymbol(
        symbol=f"pkg.symbol_{index}",
        in_file=in_file or f"src/module_{index % 4}.py",
        at_line=str(10 + index),
        signature=f"def symbol_{index}(value_{index}: str) -> str:",
        summary=f"Summary for symbol {index} " * 4,
        risk_level=risk_level,
        callers=max(0, 6 - index),
        calls=[f"pkg.dep_{index}_{n}" for n in range(4)],
    )


def make_working_set(
    *,
    symbol_count: int = 8,
    task_family: str = "targeted_refactor",
    token_budget: int = 4096,
    query: str = "Fix prompt budget regressions",
) -> WorkingSet:
    risks = ["high", "medium", "low"]
    symbols = [make_symbol(index, risk_level=risks[index % len(risks)]) for index in range(symbol_count)]
    grouped: dict[str, list[WorkingSetSymbol]] = defaultdict(list)
    for symbol in symbols:
        grouped[symbol.in_file or "__unknown__"].append(symbol)
    files = [
        WorkingSetFile(
            filepath=filepath,
            file_summary=f"Summary for {filepath}",
            symbols=file_symbols,
        )
        for filepath, file_symbols in grouped.items()
        if filepath != "__unknown__"
    ]
    return WorkingSet(
        id="ws:test",
        query=query,
        task_family=task_family,
        retrieval_id="retrieval:test",
        files=files,
        symbols=symbols,
        token_estimate=sum(max(1, len((symbol.summary or "") + (symbol.signature or "")) // 4) for symbol in symbols),
        token_budget=token_budget,
        compression="none",
        explanation="test working set",
        duration_ms=12,
    )
