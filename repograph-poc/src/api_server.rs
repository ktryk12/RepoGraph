use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use tower::ServiceBuilder;
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing::{info, warn, error};

use crate::{RepoAnalyzer, GraphSymbol, graph_store::GraphStats};

#[derive(Clone)]
pub struct ApiState {
    pub analyzer: Arc<RwLock<RepoAnalyzer>>,
}

// API Request/Response Types
#[derive(Deserialize)]
pub struct IndexRequest {
    pub repo_path: String,
    pub parallel: Option<bool>,
}

#[derive(Serialize)]
pub struct IndexResponse {
    pub status: String,
    pub files_processed: usize,
    pub symbols_found: usize,
    pub analysis_time_ms: u64,
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub q: Option<String>,
    pub limit: Option<usize>,
}

#[derive(Serialize)]
pub struct SearchResponse {
    pub symbols: Vec<SymbolInfo>,
    pub total_count: usize,
}

#[derive(Serialize)]
pub struct SymbolInfo {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub risk_level: String,
    pub service: Option<String>,
    pub is_test: bool,
    pub signature: Option<String>,
}

impl From<GraphSymbol> for SymbolInfo {
    fn from(symbol: GraphSymbol) -> Self {
        SymbolInfo {
            id: symbol.id,
            name: symbol.name,
            kind: symbol.kind,
            file_path: symbol.file_path,
            start_line: symbol.start_line,
            end_line: symbol.end_line,
            risk_level: format!("{:?}", symbol.risk_level),
            service: symbol.service,
            is_test: symbol.is_test,
            signature: symbol.signature,
        }
    }
}

#[derive(Serialize)]
pub struct StatusResponse {
    pub status: String,
    pub graph_stats: GraphStats,
    pub version: String,
}

#[derive(Serialize)]
pub struct BlastRadiusResponse {
    pub symbol_id: String,
    pub affected_symbols: Vec<SymbolInfo>,
    pub blast_radius_count: usize,
}

#[derive(Serialize)]
pub struct ErrorResponse {
    pub error: String,
    pub code: u16,
}

// API Handlers
pub async fn index_repository(
    State(state): State<ApiState>,
    Json(request): Json<IndexRequest>,
) -> Result<Json<IndexResponse>, (StatusCode, Json<ErrorResponse>)> {
    info!("Indexing repository: {}", request.repo_path);

    let repo_path = std::path::Path::new(&request.repo_path);
    let parallel = request.parallel.unwrap_or(true);

    match state.analyzer.write() {
        Ok(mut analyzer) => {
            match analyzer.analyze_repository(repo_path, parallel) {
                Ok(result) => {
                    let response = IndexResponse {
                        status: "success".to_string(),
                        files_processed: result.parse_result.file_count,
                        symbols_found: result.parse_result.symbols.len(),
                        analysis_time_ms: result.analysis_time_ms,
                    };
                    info!("Repository indexed successfully: {} files, {} symbols",
                           result.parse_result.file_count, result.parse_result.symbols.len());
                    Ok(Json(response))
                }
                Err(e) => {
                    error!("Failed to analyze repository: {}", e);
                    Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                        error: format!("Analysis failed: {}", e),
                        code: 500,
                    })))
                }
            }
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

pub async fn get_status(
    State(state): State<ApiState>,
) -> Result<Json<StatusResponse>, (StatusCode, Json<ErrorResponse>)> {
    match state.analyzer.read() {
        Ok(analyzer) => {
            let stats = analyzer.stats();
            let response = StatusResponse {
                status: "running".to_string(),
                graph_stats: stats,
                version: env!("CARGO_PKG_VERSION").to_string(),
            };
            Ok(Json(response))
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

pub async fn search_symbols(
    State(state): State<ApiState>,
    Query(query): Query<SearchQuery>,
) -> Result<Json<SearchResponse>, (StatusCode, Json<ErrorResponse>)> {
    let search_query = query.q.unwrap_or_default();
    let limit = query.limit.unwrap_or(50);

    if search_query.is_empty() {
        return Err((StatusCode::BAD_REQUEST, Json(ErrorResponse {
            error: "Query parameter 'q' is required".to_string(),
            code: 400,
        })));
    }

    match state.analyzer.read() {
        Ok(analyzer) => {
            match analyzer.search_symbols(&search_query, limit) {
                Ok(symbols) => {
                    let symbol_infos: Vec<SymbolInfo> = symbols.into_iter()
                        .map(SymbolInfo::from)
                        .collect();
                    let total_count = symbol_infos.len();

                    Ok(Json(SearchResponse {
                        symbols: symbol_infos,
                        total_count,
                    }))
                }
                Err(e) => {
                    error!("Search failed: {}", e);
                    Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                        error: format!("Search failed: {}", e),
                        code: 500,
                    })))
                }
            }
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

pub async fn get_symbol(
    State(state): State<ApiState>,
    Path(symbol_id): Path<String>,
) -> Result<Json<SymbolInfo>, (StatusCode, Json<ErrorResponse>)> {
    match state.analyzer.read() {
        Ok(analyzer) => {
            match analyzer.get_symbol(&symbol_id) {
                Ok(Some(symbol)) => {
                    Ok(Json(SymbolInfo::from(symbol)))
                }
                Ok(None) => {
                    Err((StatusCode::NOT_FOUND, Json(ErrorResponse {
                        error: format!("Symbol not found: {}", symbol_id),
                        code: 404,
                    })))
                }
                Err(e) => {
                    error!("Failed to get symbol: {}", e);
                    Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                        error: format!("Failed to get symbol: {}", e),
                        code: 500,
                    })))
                }
            }
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

pub async fn get_blast_radius(
    State(state): State<ApiState>,
    Path(symbol_id): Path<String>,
) -> Result<Json<BlastRadiusResponse>, (StatusCode, Json<ErrorResponse>)> {
    match state.analyzer.read() {
        Ok(analyzer) => {
            match analyzer.get_blast_radius(&symbol_id) {
                Ok(symbols) => {
                    let symbol_infos: Vec<SymbolInfo> = symbols.into_iter()
                        .map(SymbolInfo::from)
                        .collect();
                    let blast_radius_count = symbol_infos.len();

                    Ok(Json(BlastRadiusResponse {
                        symbol_id,
                        affected_symbols: symbol_infos,
                        blast_radius_count,
                    }))
                }
                Err(e) => {
                    error!("Blast radius analysis failed: {}", e);
                    Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                        error: format!("Blast radius analysis failed: {}", e),
                        code: 500,
                    })))
                }
            }
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

pub async fn get_file_symbols(
    State(state): State<ApiState>,
    Path(file_path): Path<String>,
) -> Result<Json<SearchResponse>, (StatusCode, Json<ErrorResponse>)> {
    match state.analyzer.read() {
        Ok(analyzer) => {
            match analyzer.get_file_symbols(&file_path) {
                Ok(symbols) => {
                    let symbol_infos: Vec<SymbolInfo> = symbols.into_iter()
                        .map(SymbolInfo::from)
                        .collect();
                    let total_count = symbol_infos.len();

                    Ok(Json(SearchResponse {
                        symbols: symbol_infos,
                        total_count,
                    }))
                }
                Err(e) => {
                    error!("Failed to get file symbols: {}", e);
                    Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                        error: format!("Failed to get file symbols: {}", e),
                        code: 500,
                    })))
                }
            }
        }
        Err(e) => {
            error!("Failed to acquire analyzer lock: {}", e);
            Err((StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "Internal server error".to_string(),
                code: 500,
            })))
        }
    }
}

// Create the API router
pub fn create_router(analyzer: Arc<RwLock<RepoAnalyzer>>) -> Router {
    let state = ApiState { analyzer };

    Router::new()
        // Core endpoints
        .route("/status", get(get_status))
        .route("/index", post(index_repository))

        // Symbol endpoints
        .route("/symbols", get(search_symbols))
        .route("/symbol/:id", get(get_symbol))
        .route("/file/*path", get(get_file_symbols))

        // Graph analysis endpoints
        .route("/blast-radius/:id", get(get_blast_radius))

        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
                .layer(CorsLayer::permissive())
        )
        .with_state(state)
}

// Server startup function
pub async fn start_server(
    analyzer: RepoAnalyzer,
    host: &str,
    port: u16,
) -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let analyzer = Arc::new(RwLock::new(analyzer));
    let app = create_router(analyzer);

    let addr = format!("{}:{}", host, port);
    let listener = tokio::net::TcpListener::bind(&addr).await?;

    info!("🦀 RepoGraph Rust API server listening on {}", addr);
    info!("API endpoints:");
    info!("  GET  /status");
    info!("  POST /index");
    info!("  GET  /symbols?q=<query>&limit=<limit>");
    info!("  GET  /symbol/<id>");
    info!("  GET  /file/<path>");
    info!("  GET  /blast-radius/<id>");

    axum::serve(listener, app).await?;

    Ok(())
}