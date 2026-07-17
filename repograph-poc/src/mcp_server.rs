use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{self, BufRead, BufReader, Write};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufWriter};
use tracing::{debug, error, info, warn};

use crate::{RepoAnalyzer, GraphSymbol, AnalysisResult};

// MCP Protocol Types
#[derive(Debug, Serialize, Deserialize)]
pub struct McpRequest {
    pub jsonrpc: String,
    pub id: Option<Value>,
    pub method: String,
    pub params: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct McpResponse {
    pub jsonrpc: String,
    pub id: Option<Value>,
    pub result: Option<Value>,
    pub error: Option<McpError>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct McpError {
    pub code: i32,
    pub message: String,
    pub data: Option<Value>,
}

// Tool Parameters
#[derive(Debug, Deserialize)]
pub struct IndexRepoParams {
    pub repo_path: String,
    pub parallel: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct SearchSymbolsParams {
    pub query: String,
    pub limit: Option<usize>,
}

#[derive(Debug, Deserialize)]
pub struct GetSymbolParams {
    pub symbol_id: String,
}

#[derive(Debug, Deserialize)]
pub struct BlastRadiusParams {
    pub symbol_id: String,
}

#[derive(Debug, Deserialize)]
pub struct PrepareTaskContextParams {
    pub repo_path: String,
    pub query: String,
    pub output_profile: Option<String>,
}

// Tool Results
#[derive(Debug, Serialize)]
pub struct ToolResult {
    pub content: Vec<Content>,
    pub is_error: Option<bool>,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type")]
pub enum Content {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "resource")]
    Resource { resource: ResourceContent },
}

#[derive(Debug, Serialize)]
pub struct ResourceContent {
    pub uri: String,
    pub mime_type: Option<String>,
    pub text: String,
}

pub struct McpServer {
    analyzer: Arc<Mutex<RepoAnalyzer>>,
}

impl McpServer {
    pub fn new(analyzer: RepoAnalyzer) -> Self {
        Self {
            analyzer: Arc::new(Mutex::new(analyzer)),
        }
    }

    pub async fn run(&self) -> Result<()> {
        info!("🔌 Starting RepoGraph MCP Server");

        let stdin = tokio::io::stdin();
        let mut reader = tokio::io::BufReader::new(stdin);
        let stdout = tokio::io::stdout();
        let mut writer = BufWriter::new(stdout);

        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line).await {
                Ok(0) => break, // EOF
                Ok(_) => {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }

                    debug!("Received: {}", line);

                    match serde_json::from_str::<McpRequest>(line) {
                        Ok(request) => {
                            let response = self.handle_request(request).await;
                            let response_json = serde_json::to_string(&response)?;

                            writer.write_all(response_json.as_bytes()).await?;
                            writer.write_all(b"\n").await?;
                            writer.flush().await?;

                            debug!("Sent: {}", response_json);
                        }
                        Err(e) => {
                            error!("Failed to parse request: {}", e);
                            let error_response = McpResponse {
                                jsonrpc: "2.0".to_string(),
                                id: None,
                                result: None,
                                error: Some(McpError {
                                    code: -32700,
                                    message: "Parse error".to_string(),
                                    data: Some(json!({"error": e.to_string()})),
                                }),
                            };

                            let error_json = serde_json::to_string(&error_response)?;
                            writer.write_all(error_json.as_bytes()).await?;
                            writer.write_all(b"\n").await?;
                            writer.flush().await?;
                        }
                    }
                }
                Err(e) => {
                    error!("Failed to read from stdin: {}", e);
                    break;
                }
            }
        }

        info!("MCP Server shutting down");
        Ok(())
    }

    async fn handle_request(&self, request: McpRequest) -> McpResponse {
        let result = match request.method.as_str() {
            "initialize" => self.handle_initialize(request.params).await,
            "tools/list" => self.handle_list_tools().await,
            "tools/call" => self.handle_tool_call(request.params).await,
            _ => Err(anyhow::anyhow!("Unknown method: {}", request.method)),
        };

        match result {
            Ok(value) => McpResponse {
                jsonrpc: "2.0".to_string(),
                id: request.id,
                result: Some(value),
                error: None,
            },
            Err(e) => McpResponse {
                jsonrpc: "2.0".to_string(),
                id: request.id,
                result: None,
                error: Some(McpError {
                    code: -32603,
                    message: "Internal error".to_string(),
                    data: Some(json!({"error": e.to_string()})),
                }),
            },
        }
    }

    async fn handle_initialize(&self, _params: Option<Value>) -> Result<Value> {
        Ok(json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "repograph-rust",
                "version": env!("CARGO_PKG_VERSION")
            }
        }))
    }

    async fn handle_list_tools(&self) -> Result<Value> {
        let tools = vec![
            json!({
                "name": "index_repo",
                "description": "Index a repository for analysis",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string", "description": "Path to repository"},
                        "parallel": {"type": "boolean", "description": "Use parallel processing"}
                    },
                    "required": ["repo_path"]
                }
            }),
            json!({
                "name": "search_symbols",
                "description": "Search for symbols by name",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "integer", "description": "Maximum results"}
                    },
                    "required": ["query"]
                }
            }),
            json!({
                "name": "get_symbol",
                "description": "Get detailed information about a specific symbol",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol_id": {"type": "string", "description": "Symbol ID"}
                    },
                    "required": ["symbol_id"]
                }
            }),
            json!({
                "name": "blast_radius",
                "description": "Analyze blast radius for a symbol",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol_id": {"type": "string", "description": "Symbol ID"}
                    },
                    "required": ["symbol_id"]
                }
            }),
            json!({
                "name": "repo_status",
                "description": "Get repository indexing status and statistics",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }),
            json!({
                "name": "prepare_task_context",
                "description": "Prepare context for an AI coding task",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string", "description": "Repository path"},
                        "query": {"type": "string", "description": "Task description"},
                        "output_profile": {"type": "string", "description": "Output profile (tiny/small/medium/patch/review)"}
                    },
                    "required": ["repo_path", "query"]
                }
            }),
            json!({
                "name": "get_file_symbols",
                "description": "Get all symbols in a specific file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "File path"}
                    },
                    "required": ["file_path"]
                }
            }),
        ];

        Ok(json!({ "tools": tools }))
    }

    async fn handle_tool_call(&self, params: Option<Value>) -> Result<Value> {
        let call_params: Value = params.ok_or_else(|| anyhow::anyhow!("No parameters provided"))?;

        let name = call_params["name"].as_str()
            .ok_or_else(|| anyhow::anyhow!("Tool name not provided"))?;

        let arguments = call_params["arguments"].clone();

        let result = match name {
            "index_repo" => self.tool_index_repo(arguments).await?,
            "search_symbols" => self.tool_search_symbols(arguments).await?,
            "get_symbol" => self.tool_get_symbol(arguments).await?,
            "blast_radius" => self.tool_blast_radius(arguments).await?,
            "repo_status" => self.tool_repo_status().await?,
            "prepare_task_context" => self.tool_prepare_task_context(arguments).await?,
            "get_file_symbols" => self.tool_get_file_symbols(arguments).await?,
            _ => return Err(anyhow::anyhow!("Unknown tool: {}", name)),
        };

        Ok(json!(result))
    }

    async fn tool_index_repo(&self, args: Value) -> Result<ToolResult> {
        let params: IndexRepoParams = serde_json::from_value(args)?;
        let repo_path = std::path::Path::new(&params.repo_path);
        let parallel = params.parallel.unwrap_or(true);

        let mut analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        let start_time = std::time::Instant::now();
        let result = analyzer.analyze_repository(repo_path, parallel)
            .with_context(|| format!("Failed to analyze repository: {}", params.repo_path))?;
        let elapsed = start_time.elapsed();

        let summary = format!(
            "Repository indexed successfully:\n• Files processed: {}\n• Symbols found: {}\n• Analysis time: {}ms\n• Total time: {}ms",
            result.parse_result.file_count,
            result.parse_result.symbols.len(),
            result.parse_result.parse_time_ms,
            elapsed.as_millis()
        );

        Ok(ToolResult {
            content: vec![Content::Text { text: summary }],
            is_error: Some(false),
        })
    }

    async fn tool_search_symbols(&self, args: Value) -> Result<ToolResult> {
        let params: SearchSymbolsParams = serde_json::from_value(args)?;
        let limit = params.limit.unwrap_or(20);

        let analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        let symbols = analyzer.search_symbols(&params.query, limit)?;

        if symbols.is_empty() {
            return Ok(ToolResult {
                content: vec![Content::Text {
                    text: format!("No symbols found matching '{}'", params.query)
                }],
                is_error: Some(false),
            });
        }

        let mut result_text = format!("Found {} symbols matching '{}':\n\n", symbols.len(), params.query);

        for symbol in symbols.iter().take(20) {
            result_text.push_str(&format!(
                "• {} [{}] in {} ({}:{})\n  Risk: {:?}, Test: {}\n",
                symbol.name,
                symbol.kind,
                std::path::Path::new(&symbol.file_path).file_name()
                    .unwrap_or_default().to_string_lossy(),
                symbol.start_line,
                symbol.end_line,
                symbol.risk_level,
                symbol.is_test
            ));
        }

        Ok(ToolResult {
            content: vec![Content::Text { text: result_text }],
            is_error: Some(false),
        })
    }

    async fn tool_get_symbol(&self, args: Value) -> Result<ToolResult> {
        let params: GetSymbolParams = serde_json::from_value(args)?;

        let analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        match analyzer.get_symbol(&params.symbol_id)? {
            Some(symbol) => {
                let details = format!(
                    "Symbol: {}\n\nDetails:\n• ID: {}\n• Kind: {}\n• File: {}\n• Lines: {}-{}\n• Risk Level: {:?}\n• Service: {:?}\n• Is Test: {}\n• Signature: {:?}",
                    symbol.name,
                    symbol.id,
                    symbol.kind,
                    symbol.file_path,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.risk_level,
                    symbol.service,
                    symbol.is_test,
                    symbol.signature
                );

                Ok(ToolResult {
                    content: vec![Content::Text { text: details }],
                    is_error: Some(false),
                })
            }
            None => Ok(ToolResult {
                content: vec![Content::Text {
                    text: format!("Symbol not found: {}", params.symbol_id)
                }],
                is_error: Some(true),
            }),
        }
    }

    async fn tool_blast_radius(&self, args: Value) -> Result<ToolResult> {
        let params: BlastRadiusParams = serde_json::from_value(args)?;

        let analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        let affected_symbols = analyzer.get_blast_radius(&params.symbol_id)?;

        if affected_symbols.is_empty() {
            return Ok(ToolResult {
                content: vec![Content::Text {
                    text: format!("No blast radius found for symbol: {}", params.symbol_id)
                }],
                is_error: Some(false),
            });
        }

        let mut result_text = format!("Blast radius for symbol {}:\n\n", params.symbol_id);
        result_text.push_str(&format!("Affected symbols: {}\n\n", affected_symbols.len()));

        for symbol in affected_symbols.iter().take(15) {
            result_text.push_str(&format!(
                "• {} [{}] in {}\n",
                symbol.name,
                symbol.kind,
                std::path::Path::new(&symbol.file_path).file_name()
                    .unwrap_or_default().to_string_lossy()
            ));
        }

        if affected_symbols.len() > 15 {
            result_text.push_str(&format!("... and {} more symbols\n", affected_symbols.len() - 15));
        }

        Ok(ToolResult {
            content: vec![Content::Text { text: result_text }],
            is_error: Some(false),
        })
    }

    async fn tool_repo_status(&self) -> Result<ToolResult> {
        let analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        let stats = analyzer.stats();

        let status_text = format!(
            "Repository Status:\n\n• Total symbols: {}\n• Total files: {}\n• Graph nodes: {}\n• Graph edges: {}\n• Relationships: {}",
            stats.total_symbols,
            stats.total_files,
            stats.graph_nodes,
            stats.graph_edges,
            stats.total_relationships
        );

        Ok(ToolResult {
            content: vec![Content::Text { text: status_text }],
            is_error: Some(false),
        })
    }

    async fn tool_prepare_task_context(&self, args: Value) -> Result<ToolResult> {
        let params: PrepareTaskContextParams = serde_json::from_value(args)?;

        // For now, return a simplified context preparation
        let context_text = format!(
            "Task Context Prepared:\n\n• Repository: {}\n• Query: {}\n• Output Profile: {}\n\nNote: Full context preparation pipeline is available via REST API.",
            params.repo_path,
            params.query,
            params.output_profile.unwrap_or("medium".to_string())
        );

        Ok(ToolResult {
            content: vec![Content::Text { text: context_text }],
            is_error: Some(false),
        })
    }

    async fn tool_get_file_symbols(&self, args: Value) -> Result<ToolResult> {
        #[derive(Deserialize)]
        struct FileParams {
            file_path: String,
        }

        let params: FileParams = serde_json::from_value(args)?;

        let analyzer = self.analyzer.lock()
            .map_err(|e| anyhow::anyhow!("Failed to acquire analyzer lock: {}", e))?;

        let symbols = analyzer.get_file_symbols(&params.file_path)?;

        if symbols.is_empty() {
            return Ok(ToolResult {
                content: vec![Content::Text {
                    text: format!("No symbols found in file: {}", params.file_path)
                }],
                is_error: Some(false),
            });
        }

        let mut result_text = format!("Symbols in {}:\n\n", params.file_path);

        for symbol in &symbols {
            result_text.push_str(&format!(
                "• {} [{}] ({}:{})\n",
                symbol.name,
                symbol.kind,
                symbol.start_line,
                symbol.end_line
            ));
        }

        Ok(ToolResult {
            content: vec![Content::Text { text: result_text }],
            is_error: Some(false),
        })
    }
}