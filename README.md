# Lumi

![Lumi](https://github.com/your-username/lumi/raw/main/assets/screenshot.png)

Lumi is an interactive terminal-based AI assistant that leverages the power of LangChain, LangGraph, and multiple AI providers to provide a seamless conversational experience directly in your terminal.

## Features

- **Multi-provider AI Support**: Works with OpenAI, Anthropic, and other AI providers through LangChain
- **Interactive Terminal Interface**: Built with [Textual](https://textual.textualize.io/) for a rich TUI experience
- **Tool Integration**: Supports MCP (Model Context Protocol) tools for extended functionality
- **Theme Support**: Light and dark themes with automatic system theme detection
- **Real-time Interaction**: Streaming responses with thinking indicators
- **Tool Approval Workflow**: Secure tool execution with user approval prompts

## Installation

### Prerequisites

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Using uv (recommended)

```bash
# Clone the repository
git clone https://github.com/your-username/lumi.git
cd lumi

# Install dependencies and create virtual environment
uv sync

# Install the package in development mode
uv run pip install -e .
```

### Using pip

```bash
# Clone the repository
git clone https://github.com/your-username/lumi.git
cd lumi

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
```

## Usage

### Running the Application

```bash
# Method 1: Using the installed script
lumi

# Method 2: Using Python module
python -m lumi.tui
```

### Key Bindings

- `Escape`: Cancel current generation
- `Ctrl+C`: Quit the application
- `Ctrl+T`: Toggle between light and dark themes

## Configuration

Lumi uses environment variables for configuration. Create a `.env` file in the project root or set the following variables:

```bash
# OpenAI Configuration
OPENAI_API_KEY=your-openai-api-key

# Anthropic Configuration  
ANTHROPIC_API_KEY=your-anthropic-api-key

# Other provider keys as needed
```

## Development

### Setting up Development Environment

```bash
# Install development dependencies
uv sync --all

# Or with pip
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=lumi
```

### Code Formatting and Linting

```bash
# Format code with Ruff
ruff format .

# Check code style
ruff check .
```

## Project Structure

```
lumi/
├── tui/           # Terminal User Interface components
│   ├── app.py     # Main application
│   ├── widgets/   # Custom Textual widgets
│   └── renderers/ # Message rendering logic
├── agents/        # Agent implementations
├── api/           # API integrations
└── utils/         # Utility functions
```

## Dependencies

Lumi is built on top of several powerful libraries:

- **[LangChain](https://langchain.com/)**: Framework for building AI applications
- **[LangGraph](https://langchain-ai.github.io/langgraph/)**: Stateful, multi-actor workflows
- **[Textual](https://textual.textualize.io/)**: Python TUI framework
- **[FastAPI](https://fastapi.tiangolo.com/)**: Web framework (for potential API endpoints)
- **[MCP Adapters](https://github.com/modelcontextprotocol/)**: Model Context Protocol integration

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgements

- Built with ❤️ using modern Python tooling
- Inspired by the growing ecosystem of terminal-based AI interfaces
- Thanks to the Textual, LangChain, and LangGraph communities

---

*Note: Replace `your-username` with your actual GitHub username in the installation instructions.*