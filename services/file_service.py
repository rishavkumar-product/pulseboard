import shutil
from pathlib import Path
from fastapi import UploadFile
from fastapi.responses import FileResponse

STORAGE = Path("storage")


def pub_dir(pub_id: int) -> Path:
    d = STORAGE / "publications" / str(pub_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def script_dir(pub_id: int) -> Path:
    d = STORAGE / "scripts" / str(pub_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_upload(pub_id: int, file: UploadFile) -> tuple[str, str]:
    ext = Path(file.filename).suffix.lower().lstrip(".")
    if ext not in ("html", "htm", "docx"):
        raise ValueError("Only .html and .docx files are supported")
    file_type = "html" if ext in ("html", "htm") else "docx"
    dest = pub_dir(pub_id) / f"output.{file_type}"
    content = await file.read()
    dest.write_bytes(content)
    return str(dest), file_type


async def save_script(pub_id: int, file: UploadFile) -> str:
    dest = script_dir(pub_id) / "refresh.py"
    content = await file.read()
    dest.write_bytes(content)
    return str(dest)


def backup_output(pub_id: int):
    d = pub_dir(pub_id)
    for ext in ("html", "docx"):
        src = d / f"output.{ext}"
        if src.exists():
            shutil.copy2(src, d / f"output_prev.{ext}")


def restore_backup(pub_id: int):
    d = pub_dir(pub_id)
    for ext in ("html", "docx"):
        prev = d / f"output_prev.{ext}"
        if prev.exists():
            shutil.copy2(prev, d / f"output.{ext}")


def get_output_path(pub_id: int, file_type: str) -> Path:
    return pub_dir(pub_id) / f"output.{file_type}"


def stream_file(file_path: str, filename: str) -> FileResponse:
    return FileResponse(file_path, filename=filename)


def delete_pub_files(pub_id: int):
    d = STORAGE / "publications" / str(pub_id)
    if d.exists():
        shutil.rmtree(d)
    s = STORAGE / "scripts" / str(pub_id)
    if s.exists():
        shutil.rmtree(s)
