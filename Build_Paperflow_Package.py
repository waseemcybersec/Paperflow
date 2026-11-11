"""Build a clean Windows source package without personal data or API keys."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DESTINATION = DIST / "Paperflow.zip"
ROOT_FILES = [
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "config.toml",
    ".env.example",
    "Start_Paperflow.bat",
    "Build_Paperflow_Package.py",
]
SOURCE_SUFFIXES = {".py", ".html", ".css", ".js"}


def main():
    DIST.mkdir(exist_ok=True)
    files = [ROOT / name for name in ROOT_FILES]
    files.extend(
        path for path in (ROOT / "src").rglob("*")
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and "__pycache__" not in path.parts
    )
    with ZipFile(DESTINATION, "w", compression=ZIP_DEFLATED, compresslevel=7) as archive:
        for file in sorted(files):
            archive.write(file, Path("Paperflow") / file.relative_to(ROOT))
    print(f"Created {DESTINATION}")
    print("Included source and launcher only; excluded .env, databases, logs, virtual environments, and paper files.")


if __name__ == "__main__":
    main()
