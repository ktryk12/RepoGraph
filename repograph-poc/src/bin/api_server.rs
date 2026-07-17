use clap::Parser;
use repograph_poc::{RepoAnalyzer, start_server};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "repograph-api")]
#[command(about = "RepoGraph Rust API Server")]
struct Cli {
    #[arg(long, default_value = "127.0.0.1")]
    host: String,

    #[arg(long, default_value = "8001")]
    port: u16,

    #[arg(long, default_value = ".repograph_db")]
    db_path: PathBuf,

    #[arg(long)]
    preload_repo: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    println!("🦀 Initializing RepoGraph Rust API Server");
    println!("Database: {}", cli.db_path.display());

    // Initialize analyzer
    let mut analyzer = RepoAnalyzer::new(&cli.db_path)?;

    // Optionally preload a repository
    if let Some(repo_path) = cli.preload_repo {
        println!("Preloading repository: {}", repo_path.display());
        let start_time = std::time::Instant::now();

        match analyzer.analyze_repository(&repo_path, true) {
            Ok(result) => {
                let elapsed = start_time.elapsed();
                println!("✅ Repository preloaded:");
                println!("  Files: {}", result.parse_result.file_count);
                println!("  Symbols: {}", result.parse_result.symbols.len());
                println!("  Time: {}ms", elapsed.as_millis());
            }
            Err(e) => {
                eprintln!("❌ Failed to preload repository: {}", e);
                eprintln!("Continuing with empty database...");
            }
        }
    }

    // Start the API server
    start_server(analyzer, &cli.host, cli.port).await?;

    Ok(())
}