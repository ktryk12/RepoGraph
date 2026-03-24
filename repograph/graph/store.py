"""CogDB-backed graph store wrapper for RepoGraph."""

from __future__ import annotations

from pathlib import Path

from cog.torque import Graph

from repograph.indexer.schema import CALLS, IN_FILE

Triple = tuple[str, str, str]


class RepoGraph:
    """Small convenience wrapper around the CogDB graph API."""

    def __init__(self, db_path: str = ".repograph") -> None:
        storage_path = Path(db_path).expanduser().resolve()
        self.db_path = storage_path
        self.g = Graph(
            "repograph",
            cog_home=storage_path.name,
            cog_path_prefix=str(storage_path.parent),
        )

    def put_triple(self, subject: str, predicate: str, obj: str) -> None:
        self.g.put(subject, predicate, obj)

    def put_triples_batch(self, triples: list[Triple]) -> None:
        self.g.put_batch(triples)

    def clear(self) -> None:
        self.g.truncate()

    def callers_of(self, symbol: str) -> list[str]:
        """Hvem kalder dette symbol?"""
        return self.incoming(symbol, CALLS)

    def callees_of(self, symbol: str) -> list[str]:
        """Hvad kalder dette symbol?"""
        return self.outgoing(symbol, CALLS)

    def blast_radius(self, symbol: str, depth: int = 3) -> dict[str, list[str]]:
        """
        Beregn hvad der pavirkes hvis symbol aendres.

        Traverser CALLS-kanter baglaens op til `depth` hop.
        Returner: {symbol: [liste af afhaengige symboler]}
        """
        visited: set[str] = set()
        frontier = {symbol}
        result: dict[str, list[str]] = {}

        for _ in range(depth):
            next_frontier: set[str] = set()
            for current_symbol in frontier:
                callers = self.callers_of(current_symbol)
                result[current_symbol] = callers
                next_frontier.update(caller for caller in callers if caller not in visited)
            visited.update(frontier)
            frontier = next_frontier
            if not frontier:
                break

        return result

    def search(self, query: str, limit: int = 20) -> list[str]:
        """Simpel symbol-sogning via node IDs, filtreret i Python."""
        nodes = [
            node_id
            for node_id in _result_ids(self.g.v().all())
            if self.g.v(node_id).out(IN_FILE).all().get("result")
        ]
        lowered_query = query.lower()
        return [node_id for node_id in nodes if lowered_query in node_id.lower()][:limit]

    def file_symbols(self, filepath: str) -> list[str]:
        """Alle symboler defineret i en given fil."""
        result = self.g.v().has(IN_FILE, filepath).all()
        return _result_ids(result)

    def outgoing(self, symbol: str, predicate: str) -> list[str]:
        result = self.g.v(symbol).out(predicate).all()
        return _result_ids(result)

    def incoming(self, symbol: str, predicate: str) -> list[str]:
        result = self.g.v(symbol).inc(predicate).all()
        return _result_ids(result)

    def first_outgoing(self, symbol: str, predicate: str) -> str | None:
        matches = self.outgoing(symbol, predicate)
        return matches[0] if matches else None

    def has_symbol(self, symbol: str) -> bool:
        return symbol in set(_result_ids(self.g.v().all()))

    def stats(self) -> dict[str, int]:
        """Graf-statistik til status endpoint."""
        all_nodes = self.g.v().all()
        return {"node_count": len(all_nodes.get("result", []))}


def _result_ids(result: dict) -> list[str]:
    return [entry["id"] for entry in result.get("result", []) if "id" in entry]
