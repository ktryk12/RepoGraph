use clap::Parser;
use repograph_poc::{RepoAnalyzer, McpServer};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "repograph-mcp")]
#[command(about = "RepoGraph MCP Server - Model Context Protocol interface for Claude Code")]
struct Cli {
    #[arg(long, default_value = ".repograph_db")]
    db_path: PathBuf,

    #[arg(long)]
    tenant_id: Option<String>,

    #[arg(long)]
    preload_repo: Option<PathBuf>,

    #[arg(long, default_value = "false")]
    verbose: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    // Initialize logging
    let log_level = if cli.verbose {
        tracing::Level::DEBUG
    } else {
        tracing::Level::INFO
    };

    tracing_subscriber::fmt()
        .with_max_level(log_level)
        .with_writer(std::io::stderr) // Log to stderr so stdout is clean for MCP
        .init();

    // Initialize analyzer
    let mut analyzer = RepoAnalyzer::new(&cli.db_path)?;

    // Handle tenant ID if provided
    if let Some(tenant_id) = cli.tenant_id {
        eprintln!("🏢 Using tenant ID: {}", tenant_id);
        // In a full implementation, this would configure tenant-specific database paths
    }

    // Optionally preload a repository
    if let Some(repo_path) = cli.preload_repo {
        eprintln!("📂 Preloading repository: {}", repo_path.display());
        let start_time = std::time::Instant::now();

        match analyzer.analyze_repository(&repo_path, true) {
            Ok(result) => {
                let elapsed = start_time.elapsed();
                eprintln!("✅ Repository preloaded:");
                eprintln!("  Files: {}", result.parse_result.file_count);
                eprintln!("  Symbols: {}", result.parse_result.symbols.len());
                eprintln!("  Time: {}ms", elapsed.as_millis());
            }
            Err(e) => {
                eprintln!("❌ Failed to preload repository: {}", e);
                eprintln!("Continuing with empty database...");
            }
        }
    }

    eprintln!("🔌 Starting RepoGraph MCP Server");
    eprintln!("📊 Database: {}", cli.db_path.display());
    eprintln!("🎯 Ready for MCP requests on stdio...");

    // Create and run MCP server
    let mcp_server = McpServer::new(analyzer);
    mcp_server.run().await?;

    Ok(())
}