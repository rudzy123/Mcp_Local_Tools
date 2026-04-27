from pathlib import Path

BASE_DIR = Path("data/notes").resolve()


def _safe_path(filename: str) -> Path:
    """
    Ensure the requested file is within the allowed directory.
    """
    path = (BASE_DIR / filename).resolve()
    if not path.is_file() or not str(path).startswith(str(BASE_DIR)):
        raise ValueError("Access denied")
    return path


def list_documents():
    """
    List available note files.
    """
    return [p.name for p in BASE_DIR.glob("*.md")]


def read_document(filename: str):
    """
    Read the contents of a single note file.
    """
    path = _safe_path(filename)
    return path.read_text()
