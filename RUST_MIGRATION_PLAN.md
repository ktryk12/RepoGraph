# RepoGraph Python → Rust Migration Plan

## Executive Summary

**Problem:** Python's GIL og interpreter overhead begrænser CPU-intensiv repo-analyse og concurrency performance.

**Solution:** Gradvis migrering til Rust for core performance-kritiske komponenter.

**Expected gains:** 5-20x performance improvement på indeksering, 2-5x på query throughput, eliminering af GC pauses.

---

## Phase 1: Assessment & Foundation (4 uger)

### 1.1 Performance Profiling
```bash
# Benchmark nuværende Python performance
- Repo indeksering (Tree-sitter parsing)
- Symbol lookup og graph traversal  
- Concurrent request handling
- Memory usage patterns
```

### 1.2 Component Analysis
| Component | LOC | Complexity | Migration Priority | Rust Equivalent |
|---|---|---|---|---|
| **Tree-sitter Parser** | ~500 | Medium | **HIGH** | `tree-sitter` crate |
| **Graph Store (CogDB)** | ~800 | High | **HIGH** | Custom graph + `sled`/`redb` |
| **Shared Retrieval** | ~1200 | Medium | Medium | `tokio` + `serde` |
| **API Routes** | ~900 | Low | Low | `axum` / `warp` |
| **Redis/Postgres** | ~600 | Low | Low | `redis` + `sqlx` crates |

### 1.3 Rust Ecosystem Mapping
```toml
[dependencies]
tree-sitter = "0.20"           # Parser bindings
sled = "0.34"                  # Embedded database  
tokio = { version = "1.0", features = ["full"] }
axum = "0.7"                   # Web framework
serde = { version = "1.0", features = ["derive"] }
sqlx = { version = "0.7", features = ["postgres", "runtime-tokio-rustls"] }
redis = { version = "0.24", features = ["tokio-comp"] }
clap = "4.0"                   # CLI parsing
```

---

## Phase 2: Core Engine Migration (8 uger)

### 2.1 Tree-sitter Parser (Uge 1-2)
```rust
// repograph-core/src/parser.rs
pub struct RepoParser {
    languages: HashMap<String, Language>,
    pool: ThreadPool,
}

impl RepoParser {
    pub async fn parse_repo(&self, path: &Path) -> Result<ParseResult> {
        // Parallel file parsing med rayon
        // Zero-copy string handling
        // Memory-mapped file reading
    }
}
```

### 2.2 Graph Store (Uge 3-5)
```rust
// repograph-core/src/graph.rs
pub struct GraphStore {
    db: sled::Db,
    indices: BTreeMap<String, sled::Tree>,
}

impl GraphStore {
    pub async fn insert_symbol(&self, symbol: Symbol) -> Result<()> {
        // Batch writes med ACID guarantees
        // Lock-free reads med snapshot isolation
    }
    
    pub async fn blast_radius(&self, symbol_id: &str) -> Vec<Symbol> {
        // Parallel graph traversal
        // Zero-allocation path finding
    }
}
```

### 2.3 Performance Benchmarks
Target improvements vs Python:
- **Indeksering:** 10-20x speedup (parallelisme + zero-copy)
- **Graph queries:** 5-10x speedup (memory efficiency)
- **Concurrent requests:** 3-5x throughput (async without GIL)

---

## Phase 3: API Layer Migration (4 uger)

### 3.1 REST API (Uge 1-2)
```rust
// repograph-api/src/main.rs
use axum::{Router, extract::State};

#[derive(Clone)]
struct AppState {
    graph: Arc<GraphStore>,
    redis: Arc<RedisPool>,
    postgres: Arc<PgPool>,
}

async fn main() {
    let app = Router::new()
        .route("/index", post(index_repo))
        .route("/symbols", get(search_symbols))
        .with_state(app_state);
        
    axum::Server::bind(&addr)
        .serve(app.into_make_service())
        .await?;
}
```

### 3.2 MCP Server (Uge 3-4)
```rust
// repograph-mcp/src/main.rs
use serde_json::Value;

struct McpServer {
    core: Arc<RepoGraphCore>,
}

impl McpServer {
    async fn handle_tool_call(&self, tool: &str, args: Value) -> Result<Value> {
        match tool {
            "index_repo" => self.core.index_repo(args).await,
            "search_symbols" => self.core.search_symbols(args).await,
            // ... 23 MCP tools
        }
    }
}
```

---

## Phase 4: Integration & Testing (3 uger)

### 4.1 Python-Rust Bridge (Uge 1)
Mens migreringen kører, bridge med `pyo3`:
```rust
// python-bridge/src/lib.rs
use pyo3::prelude::*;

#[pyfunction]
fn parse_repo_rust(path: &str) -> PyResult<String> {
    let result = tokio::runtime::Runtime::new()
        .unwrap()
        .block_on(core::parse_repo(path))?;
    Ok(serde_json::to_string(&result)?)
}

#[pymodule]
fn repograph_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_repo_rust, m)?)?;
    Ok(())
}
```

### 4.2 Compatibility Testing (Uge 2)
```bash
# Test at alle 51 API endpoints virker identisk
./test-compatibility.sh python-api rust-api

# Test 23 MCP tools
./test-mcp-compatibility.sh

# Load testing
wrk -t12 -c400 -d30s http://localhost:8001/symbols
```

### 4.3 Deployment (Uge 3)
```dockerfile
# Containerfile.rust - Multi-stage build
FROM rust:1.75 as builder
COPY . /build
WORKDIR /build  
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /build/target/release/repograph /usr/local/bin/
CMD ["repograph"]
```

---

## Phase 5: Optimization & Monitoring (2 uger)

### 5.1 Performance Tuning
- Memory allocation profiling (`jemalloc`)
- CPU profiling (`perf`, `flamegraph`)
- Async runtime tuning (`tokio-console`)
- Database optimization (connection pooling)

### 5.2 Production Readiness
```rust
// Structured logging
use tracing::{info, warn, error};

// Metrics
use prometheus::{Counter, Histogram, Registry};

// Health checks
async fn health_check() -> impl IntoResponse {
    StatusCode::OK
}
```

---

## Risk Mitigation

### Technical Risks
| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Performance ikke som forventet** | Low | High | Prototype + benchmark tidligt |
| **Library ecosystem mangler** | Medium | Medium | Evaluate alternatives (Go?) |
| **Team learning curve** | High | Medium | Rust training + pair programming |
| **Migration bugs** | Medium | High | Parallel systems + gradual cutover |

### Business Risks
| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Extended timeline** | Medium | Medium | Phase-by-phase med fallback |
| **Resource allocation** | Medium | High | Dedicated migration team |
| **Customer impact** | Low | High | Blue-green deployment |

---

## Timeline & Resources

```
Month 1: [Assessment] [Foundation] 
Month 2: [Core Engine Migration ================]
Month 3: [API Layer] [Testing]
Month 4: [Optimization] [Production]

Team: 2-3 Rust developers + 1 Python expert
Budget: 4 måneder udvikling + 2 måneder support
```

---

## Success Metrics

### Performance KPIs
- **Repo indeksering:** < 30s for 100k LOC (vs 5+ minutter Python)
- **Symbol queries:** < 10ms P95 latency (vs 50-100ms Python)  
- **Concurrent requests:** 1000+ RPS (vs 200 RPS Python)
- **Memory usage:** 50% reduktion vs Python

### Quality KPIs
- **API compatibility:** 100% endpoint parity
- **MCP tools:** 23/23 tools working identisk
- **Uptime:** 99.9% during migration
- **Zero data loss** under normal operations

---

## Decision Points

### Go instead of Rust?
| Criterion | Rust | Go |
|---|---|---|
| **Raw performance** | ★★★★★ | ★★★★ |
| **Memory safety** | ★★★★★ | ★★★ |
| **Learning curve** | ★★ | ★★★★ |
| **Ecosystem** | ★★★★ | ★★★★★ |
| **Team familiarity** | ★★ | ★★★ |

**Recommendation:** Start med Rust prototype (Phase 1). Hvis complexity bliver for høj, pivot til Go i Phase 2.

### Big Bang vs Gradual Migration?
**Chosen:** Gradual migration med Python-Rust bridge for risk reduction.

---

## Next Steps

1. **Week 1:** Rust POC - parse single Python file med tree-sitter
2. **Week 2:** Performance benchmark vs Python equivalent  
3. **Week 3:** Team Rust training + architecture review
4. **Week 4:** Go/No-go decision baseret på POC resultater

Er du klar til at starte med Phase 1 assessment og Rust POC?