from dotenv import load_dotenv

# Load environment variables before importing modules that initialize settings
load_dotenv()

from open_binancian_futures.cli import app

def main() -> None:
    """Main application entry point."""
    app()

if __name__ == "__main__":
    main()
