"""Shared offline workflow for LocalDelta's terminal and desktop frontends.

No AI, network calls, or direct business-table writes occur here. All database
mutations are delegated to the existing guarded PDP importer.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pdp
import privacy_bridge


class WorkflowError(Exception):
    """Safe, displayable workflow failure."""


@dataclass(frozen=True)
class Review:
    database: Path
    package: Path
    evidence_dir: Path | None
    package_sha256: str
    package_id: str
    base_revision: int
    description: str
    preview: dict[str, Any]
    operations: tuple[dict[str, Any], ...]
    db_revision: int
    db_fingerprint: str


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _require_file(value: str | Path, description: str) -> Path:
    path = _path(value)
    if not path.is_file():
        raise WorkflowError(f"{description}不存在：{path}")
    return path


def _run_pdp(*args: str) -> dict[str, Any]:
    """Invoke the same legacy CLI entrypoint with captured structured results."""
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = pdp.main(list(args))
    except Exception as exc:
        raise WorkflowError(f"LocalDelta 执行失败：{exc}") from exc
    if rc != 0:
        raise WorkflowError((stderr.getvalue() or stdout.getvalue() or "PDP 拒绝执行").strip())
    output = stdout.getvalue().strip()
    try:
        return json.loads(output)
    except (ValueError, TypeError):
        raise WorkflowError(f"无法解析 PDP {args[0]} 输出：{output[:500]}") from None


def status(db: str | Path) -> dict[str, Any]:
    path = _require_file(db, "数据库")
    return _run_pdp("status", "--db", str(path))


def initialize(db: str | Path) -> dict[str, Any]:
    """PDP init backs up the database before its first schema write."""
    path = _require_file(db, "数据库")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = pdp.main(["init", "--db", str(path)])
    if rc != 0:
        raise WorkflowError((stderr.getvalue() or stdout.getvalue()).strip())
    return status(path)


def create_database(db: str | Path) -> dict[str, Any]:
    """Create and initialize a NEW, empty portfolio DB; never replace existing bytes.

    Creation uses O_EXCL, so even a file appearing between the dialog and the
    click cannot be overwritten. Schema creation/initialization is local only.
    """
    if not str(db).strip():
        raise WorkflowError("请指定新数据库的保存路径")
    path = _path(db)
    if path.suffix.lower() not in (".sqlite3", ".sqlite", ".db"):
        raise WorkflowError("数据库文件必须使用 .sqlite3、.sqlite 或 .db 扩展名")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL blocks overwrite even if the existing path is a symlink.
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WorkflowError(f"目标文件已存在，绝不覆盖：{path}") from exc
    except OSError as exc:
        raise WorkflowError(f"无法创建数据库：{exc}") from exc
    os.close(fd)
    try:
        sql = (Path(__file__).resolve().parent / "legacy_schema.sql").read_text(encoding="utf-8")
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.executescript(sql)
            conn.commit()
        return initialize(path)
    except Exception as exc:
        # Only remove the file that THIS operation exclusively created.
        # In case of an interrupted initialization the user may retry safely.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, WorkflowError):
            raise
        raise WorkflowError(f"新建数据库未完成：{exc}") from exc


def preview(db: str | Path, package: str | Path, evidence_dir: str | Path | None = None) -> Review:
    database = _require_file(db, "数据库")
    pack = _require_file(package, "增量包")
    evidence = _path(evidence_dir) if evidence_dir else None
    if evidence is not None and not evidence.is_dir():
        raise WorkflowError(f"证据目录不存在：{evidence}")
    original_sha256 = hashlib.sha256(pack.read_bytes()).hexdigest()
    try:
        pkg, proof = pdp.load_package(pack, str(evidence) if evidence else None)
        pdp.validate_pkg(pkg, proof)
    except Exception as exc:
        raise WorkflowError(f"增量包校验失败：{exc}") from exc
    args = ["plan", "--db", str(database), "--package", str(pack)]
    if evidence:
        args += ["--evidence-dir", str(evidence)]
    before = status(database)
    plan = _run_pdp(*args)
    after = status(database)
    if hashlib.sha256(pack.read_bytes()).hexdigest() != original_sha256:
        raise WorkflowError("预演期间更新包发生变化，请重新进行预演")
    if (before["revision"], before["data_fingerprint"]) != (after["revision"], after["data_fingerprint"]):
        raise WorkflowError("预演期间数据库发生变化，请重新进行预演")
    return Review(
        database=database,
        package=pack,
        evidence_dir=evidence,
        package_sha256=original_sha256,
        package_id=pkg["package_id"],
        base_revision=pkg["base_revision"],
        description=pkg.get("description", ""),
        preview=plan,
        operations=tuple(pkg["operations"]),
        db_revision=before["revision"],
        db_fingerprint=before["data_fingerprint"],
    )


def apply(review: Review) -> dict[str, Any]:
    """Recheck the exact reviewed package and DB state immediately before apply."""
    if hashlib.sha256(_require_file(review.package, "增量包").read_bytes()).hexdigest() != review.package_sha256:
        raise WorkflowError("增量包在预演后被修改，拒绝导入；请重新预演")
    fresh = preview(review.database, review.package, review.evidence_dir)
    if fresh.package_id != review.package_id or fresh.package_sha256 != review.package_sha256:
        raise WorkflowError("增量包与已审阅的内容不一致，请重新预演")
    if (fresh.db_revision, fresh.db_fingerprint) != (review.db_revision, review.db_fingerprint):
        raise WorkflowError("数据库自预演后发生变化，拒绝导入；请重新预演")
    if fresh.preview["status"] == "already_applied":
        return fresh.preview
    if fresh.preview["status"] != "preview":
        raise WorkflowError("预演状态异常，拒绝导入")
    args = ["apply", "--db", str(fresh.database), "--package", str(fresh.package)]
    if fresh.evidence_dir:
        args += ["--evidence-dir", str(fresh.evidence_dir)]
    return _run_pdp(*args)


def history(db: str | Path) -> list[dict[str, Any]]:
    return status(db)["recent_packages"]


def _private_output(db: str | Path, prefix: str, suffix: str = ".json") -> Path:
    db = _require_file(db, "数据库")
    import uuid
    folder = db.parent / ".localdelta" / "outbox"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{prefix}-{uuid.uuid4().hex[:12]}{suffix}"


def cloud_context(db: str | Path) -> Path:
    output = _private_output(db, "cloud-context")
    try:
        privacy_bridge.export_context(str(_path(db)), str(output))
    except (pdp.PDPError, OSError, ValueError) as exc:
        raise WorkflowError(str(exc)) from exc
    return output


def private_alias_map(db: str | Path) -> Path:
    output = _private_output(db, "PRIVATE-aliases")
    try:
        privacy_bridge.export_local_aliases(str(_path(db)), str(output))
    except (pdp.PDPError, OSError, ValueError) as exc:
        raise WorkflowError(str(exc)) from exc
    return output


def bind_draft(db: str | Path, draft: str | Path) -> Path:
    draft = _require_file(draft, "AI 草稿")
    output = _private_output(db, "bound-update", ".pdp.json")
    try:
        privacy_bridge.bind_draft(str(_path(db)), str(draft), str(output))
    except (pdp.PDPError, OSError, ValueError, TypeError) as exc:
        raise WorkflowError(str(exc)) from exc
    return output


def readable_review(review: Review) -> str:
    """Human-readable record contents; local only, may contain sensitive facts."""
    state = review.preview.get("status", "unknown")
    lines = [
        f"数据库：{review.database}",
        f"更新包：{review.package}",
        f"包 ID：{review.package_id}",
        f"说明：{review.description or '(无)'}",
        f"预演状态：{state}",
        f"当前版本：{review.db_revision}    预期版本：{review.preview.get('revision')}",
        f"操作数量：{len(review.operations)}",
        "",
    ]
    if state == "already_applied":
        lines.append("这个更新包已经导入过；再次执行不会改变数据库。")
        return "\n".join(lines)
    for n, op in enumerate(review.operations, 1):
        action = op["action"]
        table = op["table"]
        lines.append(f"[{n}] {action.upper()} / {table}")
        if action == "create":
            lines += [f"    {field}: {json.dumps(value, ensure_ascii=False)}" for field, value in op["record"].items()]
        elif action == "correct":
            for field, newer in op["changes"].items():
                lines.append(f"    {field}: {json.dumps(op['before'][field], ensure_ascii=False)} → {json.dumps(newer, ensure_ascii=False)}")
            lines.append(f"    原因：{op.get('reason', '')}")
        else:
            lines.append(f"    记录 ID：{op.get('id', '')}")
            lines.append(f"    作废原因：{op.get('reason', '')}")
        lines.append(f"    来源：{op['source'].get('kind')}, {op['source'].get('reference')}")
        lines.append("")
    return "\n".join(lines)


def make_demo() -> tuple[Path, Path]:
    """Prepare a distinct synthetic workspace without touching any user database."""
    import uuid
    from examples.create_demo import main as create_demo
    folder = Path.home() / ".localdelta" / "demos" / ("demo-" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    create_demo(folder, quiet=True)
    return folder / "demo.sqlite3", folder / "example_update.pdp.zip"
