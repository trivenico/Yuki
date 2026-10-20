"""Print safe manual setup instructions for a Piper voice model."""
from pathlib import Path

def main() -> None:
    target = Path("data/models/piper_voices")
    target.mkdir(parents=True, exist_ok=True)
    print("Piper voice setup is intentionally manual in this release.")
    print("Download a voice model and its matching .onnx.json from the official Piper voice catalog.")
    print(f"Place both files in: {target.resolve()}")
    print("Verify the source and checksums before running any downloaded model.")

if __name__ == "__main__":
    main()
